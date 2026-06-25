"""
Fashion Archive — Twelve Labs Service
Handles all video ingestion, indexing, and semantic search via Twelve Labs API.

Twelve Labs processes YouTube URLs natively — no downloading required.
Models used:
  - marengo2.7: semantic search, visual understanding
  - pegasus1.2: video-to-text generation (look descriptions)
"""

import os
import base64
import asyncio
import httpx
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential
import logging

logger = logging.getLogger(__name__)

TWELVE_LABS_BASE_URL = "https://api.twelvelabs.io/v1.3"
INDEX_ID = os.getenv("TWELVE_LABS_INDEX_ID")

# Keep a module-level reference for backwards compatibility (may be None at import time).
# Always access via _get_api_key() at call time so that:
#   (a) dotenv loaded after import still works, and
#   (b) a missing key raises a clear error instead of crashing httpx on None.encode().
API_KEY = os.getenv("TWELVE_LABS_API_KEY")


def _get_api_key() -> str:
    key = os.getenv("TWELVE_LABS_API_KEY") or API_KEY
    if not key:
        raise RuntimeError(
            "TWELVE_LABS_API_KEY is not set. "
            "Add it to backend/.env or export it before starting the server."
        )
    return key


def _get_index_id() -> str:
    idx = os.getenv("TWELVE_LABS_INDEX_ID") or INDEX_ID
    if not idx:
        raise RuntimeError(
            "TWELVE_LABS_INDEX_ID is not set. "
            "Add it to backend/.env after running the first ingest."
        )
    return idx

# Raw cosine pre-filter — keeps only candidates with a non-trivial similarity signal
# before they reach the calibration layer in main.py.
# Set deliberately low (0.03) so the calibration floor (SEARCH_CONFIDENCE_FLOOR,
# default 50 on the 0-100 scale) governs what the user actually sees.
# Changing the effective display cutoff: adjust SEARCH_CONFIDENCE_FLOOR, not this.
SIMILARITY_THRESHOLD = 0.03

# Known brands — used to extract brand filter from free-text queries
KNOWN_BRANDS = [
    "Chanel", "Dior", "Gucci", "Valentino", "Versace", "Prada", "Miu Miu",
    "Loewe", "Hermès", "Hermes", "Fendi", "Givenchy", "Celine", "Céline",
    "Balenciaga", "Bottega Veneta", "Burberry", "Louis Vuitton",
    "Alexander McQueen", "Saint Laurent", "Rick Owens", "Jacquemus",
    "Jil Sander", "Issey Miyake", "Maison Margiela", "Vivienne Westwood",
]


def extract_brand_from_query(query: str):
    """
    Return (brand_name_or_None, cleaned_query_without_brand).
    Removes the matched brand token from the query so the embedding focuses
    on visual attributes only (Marengo can't encode brand names anyway).
    """
    q_lower = query.lower()
    for brand in KNOWN_BRANDS:
        if brand.lower() in q_lower:
            cleaned = query.replace(brand, "").replace(brand.lower(), "").strip(" ,")
            # collapse double spaces
            import re
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            return brand, cleaned or query
    return None, query


def get_headers() -> dict:
    return {
        "x-api-key": _get_api_key(),
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────
# INDEX MANAGEMENT
# ─────────────────────────────────────────

async def create_index(name: str = "fashion-archive") -> str:
    """
    Create a Twelve Labs index for the archive.
    Run once during setup. Returns index_id to save in .env.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{TWELVE_LABS_BASE_URL}/indexes",
            headers=get_headers(),
            json={
                "name": name,
                "engines": [
                    {
                        "name": "marengo2.7",
                        "options": ["visual", "conversation", "text_in_video"],
                    },
                    {
                        "name": "pegasus1.2",
                        "options": ["visual", "conversation"],
                    },
                ],
                "addons": ["thumbnail"],
            },
        )
        response.raise_for_status()
        data = response.json()
        logger.info(f"Created index: {data['_id']}")
        return data["_id"]


async def get_index_info() -> dict:
    """Fetch current index statistics."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TWELVE_LABS_BASE_URL}/indexes/{INDEX_ID}",
            headers=get_headers(),
        )
        response.raise_for_status()
        return response.json()


# ─────────────────────────────────────────
# VIDEO INGESTION
# ─────────────────────────────────────────

async def ingest_youtube_url(youtube_url: str, metadata: dict) -> str:
    """
    Submit a YouTube URL for ingestion into Twelve Labs.
    Returns task_id for polling status.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{TWELVE_LABS_BASE_URL}/tasks",
            headers=get_headers(),
            json={
                "index_id": INDEX_ID,
                "url": youtube_url,
                "metadata": metadata,
            },
        )
        response.raise_for_status()
        data = response.json()
        task_id = data["_id"]
        logger.info(f"Ingestion task created: {task_id} for {youtube_url}")
        return task_id


async def ingest_local_file(file_path: str, metadata: dict) -> str:
    """
    Upload a local video file to Twelve Labs and ingest it.
    Use this for WeTransfer downloads, partner uploads, or any file
    that cannot be accessed via a public URL.

    Twelve Labs multipart upload — supports files up to 2GB.
    Larger files (full show archives) should be split or use S3.

    Args:
        file_path: Absolute path to the video file on disk
                   e.g. /Users/yourname/Downloads/dior_ss2024.mp4
        metadata:  Dict with brand, season, year, show_id etc.

    Returns:
        task_id for polling with get_task_status()
    """
    import os
    import mimetypes

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Video file not found: {file_path}")

    file_size = os.path.getsize(file_path)
    file_name = os.path.basename(file_path)
    mime_type = mimetypes.guess_type(file_path)[0] or "video/mp4"

    logger.info(f"Uploading {file_name} ({file_size / 1024 / 1024:.1f} MB) to Twelve Labs")

    # Twelve Labs multipart upload — do NOT include Content-Type in headers
    # httpx sets the correct multipart boundary automatically
    upload_headers = {"x-api-key": _get_api_key()}

    with open(file_path, "rb") as f:
        upload_timeout = max(300.0, file_size / (1024 * 1024) * 3)  # ~3s per MB, min 5 min
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=30.0,
                read=upload_timeout,
                write=upload_timeout,
                pool=30.0,
            )
        ) as client:
            response = await client.post(
                f"{TWELVE_LABS_BASE_URL}/tasks",
                headers=upload_headers,
                data={
                    "index_id": INDEX_ID,
                    "metadata": str(metadata),  # Twelve Labs accepts metadata as string in multipart
                },
                files={
                    "video_file": (file_name, f, mime_type),
                },
            )

    if response.status_code not in (200, 201):
        logger.error(f"Upload failed: {response.status_code} {response.text}")
        response.raise_for_status()

    data = response.json()
    task_id = data["_id"]
    logger.info(f"File upload task created: {task_id} for {file_name}")
    return task_id


async def ingest_wetransfer(wetransfer_url: str, metadata: dict, download_dir: str = "/tmp") -> str:
    """
    Download a WeTransfer link and ingest the video file.

    WeTransfer links cannot be ingested directly — they require a download step.
    This function handles the full flow:
      1. Download the file from WeTransfer to a local temp directory
      2. Upload to Twelve Labs via multipart
      3. Clean up the temp file

    Args:
        wetransfer_url: The WeTransfer link Marzio sent
        metadata:       Dict with brand, season, year, show_id etc.
        download_dir:   Where to save the temp file (default: /tmp)

    Returns:
        task_id for polling with get_task_status()

    NOTE: WeTransfer download may take several minutes for large files.
    This is a blocking operation — run via background task in production.
    """
    import os
    import tempfile

    logger.info(f"Downloading from WeTransfer: {wetransfer_url}")

    # WeTransfer redirects to a direct download URL
    # Follow redirects and stream the file to disk
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0),
        follow_redirects=True,
    ) as client:
        async with client.stream("GET", wetransfer_url) as response:
            response.raise_for_status()

            # Get filename from Content-Disposition header if available
            content_disp = response.headers.get("content-disposition", "")
            if "filename=" in content_disp:
                file_name = content_disp.split("filename=")[-1].strip('"')
            else:
                file_name = f"wetransfer_{metadata.get('brand','show')}_{metadata.get('season','')}.mp4"

            # Clean filename
            file_name = "".join(c for c in file_name if c.isalnum() or c in "._- ").strip()
            file_path = os.path.join(download_dir, file_name)

            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            with open(file_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):  # 1MB chunks
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size:
                        pct = downloaded / total_size * 100
                        logger.info(f"Download progress: {pct:.1f}% ({downloaded / 1024 / 1024:.1f} MB)")

    logger.info(f"Download complete: {file_path} ({os.path.getsize(file_path) / 1024 / 1024:.1f} MB)")

    try:
        # Upload to Twelve Labs
        task_id = await ingest_local_file(file_path, metadata)
        return task_id
    finally:
        # Always clean up the temp file
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up temp file: {file_path}")


async def get_task_status(task_id: str) -> dict:
    """
    Poll ingestion task status.
    Status: pending | indexing | ready | failed
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TWELVE_LABS_BASE_URL}/tasks/{task_id}",
            headers=get_headers(),
        )
        response.raise_for_status()
        return response.json()


async def wait_for_ingestion(task_id: str, poll_interval: int = 10) -> dict:
    """
    Poll until ingestion completes. Use for scripts/CLI — not for API endpoints.
    For API use, poll via get_task_status and return progress to client.
    """
    while True:
        status = await get_task_status(task_id)
        state = status.get("status")
        logger.info(f"Task {task_id}: {state}")

        if state == "ready":
            return status
        elif state == "failed":
            raise Exception(f"Ingestion failed for task {task_id}: {status}")

        await asyncio.sleep(poll_interval)


# ─────────────────────────────────────────
# SEMANTIC SEARCH
# ─────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def embed_text(text: str) -> Optional[list]:
    """
    Embed a text query using Marengo3.0 → 512-dim vector.
    Returns a list of floats, or None on failure.
    """
    import uuid
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="model_name"\r\n\r\nmarengo3.0\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="text"\r\n\r\n{text}\r\n'
        f'--{boundary}--\r\n'
    ).encode()
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{TWELVE_LABS_BASE_URL}/embed",
            headers={"x-api-key": _get_api_key(), "Content-Type": f"multipart/form-data; boundary={boundary}"},
            content=body,
        )
        if r.status_code != 200:
            logger.warning(f"embed_text failed ({r.status_code}): {r.text[:200]}")
            return None
        segments = r.json().get("text_embedding", {}).get("segments", [])
        return segments[0]["float"] if segments else None


async def embed_image(image_bytes: bytes) -> Optional[list]:
    """
    Embed an image (JPEG bytes) using Marengo3.0 → 512-dim vector.
    Returns a list of floats, or None on failure.
    """
    import uuid
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="model_name"\r\n\r\nmarengo3.0\r\n'.encode() +
        f'--{boundary}\r\nContent-Disposition: form-data; name="image_file"; filename="frame.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode() +
        image_bytes +
        f'\r\n--{boundary}--\r\n'.encode()
    )
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{TWELVE_LABS_BASE_URL}/embed",
            headers={"x-api-key": _get_api_key(), "Content-Type": f"multipart/form-data; boundary={boundary}"},
            content=body,
        )
        if r.status_code != 200:
            logger.warning(f"embed_image failed ({r.status_code}): {r.text[:200]}")
            return None
        segments = r.json().get("image_embedding", {}).get("segments", [])
        return segments[0]["float"] if segments else None


async def semantic_search(
    query: str,
    limit: int = 20,
    filters: Optional[dict] = None,
    cross_house: bool = False,
) -> list:
    """
    Semantic search. Uses pgvector cosine similarity when embeddings exist,
    falls back to TL /search rank-based scoring otherwise.
    """
    from services.database import AsyncSessionLocal, Moment, Show
    from sqlalchemy import select, text as sql_text

    # Extract brand from query so pgvector embeds visual terms only
    brand_filter, visual_query = extract_brand_from_query(query)

    # Diversity caps: limit moments per show and per brand for concept queries.
    # Brand queries (brand_filter set) bypass both caps — depth in one brand is desired.
    _raw_show_cap = os.environ.get("MAX_MOMENTS_PER_SHOW", "3")
    try:
        _diversity_cap = max(1, int(_raw_show_cap))
    except (TypeError, ValueError):
        _diversity_cap = 3

    _raw_brand_cap = os.environ.get("MAX_MOMENTS_PER_BRAND", "4")
    try:
        _brand_cap = max(1, int(_raw_brand_cap))
    except (TypeError, ValueError):
        _brand_cap = 4
    # Cross-house queries: cap at 2 per brand so results spread across more houses
    if cross_house:
        _brand_cap = min(_brand_cap, 2)

    # Try pgvector exact KNN path first (index dropped → always a full sequential scan,
    # which is sub-20ms for 3,280 vectors and guarantees the brand WHERE filter works).
    query_vec = await embed_text(visual_query)
    if query_vec:
        try:
            # Format floats to avoid scientific notation (pgvector can't parse 1e-05)
            vec_str = "[" + ",".join(f"{v:.8f}" for v in query_vec) + "]"
            brand_clause = f"AND s.brand ILIKE '{brand_filter.replace(chr(39), chr(39)*2)}'" if brand_filter else ""
            # Fetch a wider candidate pool so diversity cap has material to work with.
            # For brand queries the cap is not applied, so fetch only what was requested.
            fetch_limit = limit if brand_filter else min(limit * 5, 500)
            # Interpolate directly — safe: vec_str is machine-generated floats, brand_clause uses ILIKE with single-quote escape
            async with AsyncSessionLocal() as session:
                rows = await session.execute(
                    sql_text(f"""
                        SELECT m.id, m.show_id, m.timestamp_start, m.timestamp_end,
                               m.description, m.thumbnail_url,
                               m.embedding <=> '{vec_str}'::vector AS distance
                        FROM moments m
                        JOIN shows s ON s.id = m.show_id
                        WHERE m.embedding IS NOT NULL
                        {brand_clause}
                        ORDER BY m.embedding <=> '{vec_str}'::vector
                        LIMIT {fetch_limit}
                    """)
                )
                pgvec_results = rows.fetchall()

            show_ids = list({r.show_id for r in pgvec_results})
            async with AsyncSessionLocal() as session:
                shows_rows = await session.execute(
                    select(Show).where(Show.id.in_(show_ids))
                )
                shows_map = {s.id: s for s in shows_rows.scalars().all()}

            results = []
            per_show_count: dict = {}   # show_id → count
            per_brand_count: dict = {}  # brand (lower) → count across all shows
            for r in pgvec_results:
                similarity = 1.0 - float(r.distance)
                if similarity < SIMILARITY_THRESHOLD:
                    continue
                # Apply diversity caps only for concept queries (no brand filter)
                if not brand_filter:
                    show_count = per_show_count.get(r.show_id, 0)
                    if show_count >= _diversity_cap:
                        continue
                    # Per-brand cap: prevents one house's multiple shows dominating
                    show_obj = shows_map.get(r.show_id)
                    brand_key = (show_obj.brand or "").lower() if show_obj else ""
                    if brand_key:
                        if per_brand_count.get(brand_key, 0) >= _brand_cap:
                            continue
                        per_brand_count[brand_key] = per_brand_count.get(brand_key, 0) + 1
                    per_show_count[r.show_id] = show_count + 1
                show = shows_map.get(r.show_id)
                results.append({
                    "video_id": show.video_id if show else None,
                    "score": round(similarity, 4),
                    "start": r.timestamp_start,
                    "end": r.timestamp_end,
                    "thumbnail_url": r.thumbnail_url,
                    "metadata": {},
                    "_moment_id": str(r.id),
                    "_show_id": str(r.show_id),
                })
                if len(results) >= limit:
                    break
            return results
        except Exception as e:
            logger.warning(f"pgvector search failed, falling back to TL: {e}")

    # Fallback: TL /search
    files = _multipart_fields(
        index_id=INDEX_ID,
        query_text=query,
        search_options="visual",
        page_limit=str(limit),
        threshold="medium",
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{TWELVE_LABS_BASE_URL}/search",
            headers={"x-api-key": _get_api_key()},
            files=files,
        )
        response.raise_for_status()
        data = response.json()

    clips = data.get("data", [])
    total = len(clips)

    # Pre-fetch thumbnails for all unique video_ids
    video_ids = list({c["video_id"] for c in clips})
    thumbnails: dict = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        for vid in video_ids:
            try:
                r = await client.get(
                    f"{TWELVE_LABS_BASE_URL}/indexes/{INDEX_ID}/videos/{vid}",
                    headers={"x-api-key": _get_api_key()},
                )
                if r.status_code == 200:
                    thumb_list = r.json().get("hls", {}).get("thumbnail_urls", [])
                    thumbnails[vid] = thumb_list[0] if thumb_list else None
            except Exception:
                pass

    results = []
    for clip in clips:
        # Use TL's real score (0.0–1.0); fall back to rank-derived only if absent
        score = clip.get("score")
        if score is None:
            rank = clip.get("rank", total)
            score = max(0.0, 1.0 - (rank - 1) / max(total, 1))
        score = float(score)
        if score < SIMILARITY_THRESHOLD:
            continue
        results.append({
            "video_id": clip["video_id"],
            "score": round(score, 4),
            "start": clip.get("start", 0),
            "end": clip.get("end", 0),
            "thumbnail_url": thumbnails.get(clip["video_id"]),
            "metadata": clip.get("metadata", {}),
        })

    return results


# ─────────────────────────────────────────
# VIDEO ANALYSIS — LOOK EXTRACTION
# ─────────────────────────────────────────

def _multipart_fields(**kwargs) -> dict:
    """Convert string kwargs to httpx multipart files format (no actual files)."""
    return {k: (None, str(v)) for k, v in kwargs.items()}


async def _search_clips_for_video(video_id: str, query: str, page_limit: int = 50) -> list:
    """Search within a specific video using v1.3 multipart/form-data API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{TWELVE_LABS_BASE_URL}/search",
            headers={"x-api-key": _get_api_key()},
            files=_multipart_fields(
                index_id=INDEX_ID,
                query_text=query,
                search_options="visual",
                page_limit=str(page_limit),
                threshold="low",
            ),
        )
        if response.status_code != 200:
            logger.warning(f"Search failed ({response.status_code}): {response.text[:200]}")
            return []
        return response.json().get("data", [])


async def delete_video(video_id: str) -> bool:
    """
    Delete a video from the TL index.
    Returns True on success, False if already gone (404) — never raises.
    Must be called LAST during a replace: only after new moments are committed.

    TL has two delete endpoints depending on how the video was ingested:
      - /indexes/{id}/videos/{vid}         — task-based ingestion (URL/file upload)
      - /indexes/{id}/indexed-assets/{vid} — asset-based ingestion
    We try the task endpoint first; on 409 we retry via indexed-assets.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.delete(
            f"{TWELVE_LABS_BASE_URL}/indexes/{INDEX_ID}/videos/{video_id}",
            headers={"x-api-key": _get_api_key()},
        )
    if r.status_code in (200, 204):
        logger.info(f"TL video deleted (task endpoint): {video_id}")
        return True
    if r.status_code == 404:
        logger.warning(f"TL video already absent (404): {video_id}")
        return False
    if r.status_code == 409:
        # Video was ingested as an asset — use the indexed-assets endpoint
        async with httpx.AsyncClient(timeout=30) as client:
            r2 = await client.delete(
                f"{TWELVE_LABS_BASE_URL}/indexes/{INDEX_ID}/indexed-assets/{video_id}",
                headers={"x-api-key": _get_api_key()},
            )
        if r2.status_code in (200, 204):
            logger.info(f"TL video deleted (indexed-assets endpoint): {video_id}")
            return True
        if r2.status_code == 404:
            logger.warning(f"TL indexed-asset already absent (404): {video_id}")
            return False
        logger.error(f"TL indexed-assets delete failed for {video_id}: {r2.status_code} {r2.text[:200]}")
        return False
    logger.error(f"TL delete failed for {video_id}: {r.status_code} {r.text[:200]}")
    return False


async def get_hls_url(video_id: str) -> Optional[str]:
    """Fetch the HLS stream URL for a TL video."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{TWELVE_LABS_BASE_URL}/indexes/{INDEX_ID}/videos/{video_id}",
            headers={"x-api-key": _get_api_key()},
        )
        if r.status_code != 200:
            return None
        thumbs = r.json().get("hls", {}).get("thumbnail_urls", [])
        url = r.json().get("hls", {}).get("video_url")
        return url


async def extract_frame(hls_url: str, timestamp: float) -> Optional[bytes]:
    """Extract a JPEG frame at timestamp from an HLS stream via ffmpeg."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-ss", str(timestamp), "-i", hls_url,
        "-vframes", "1", "-f", "image2", "-vcodec", "mjpeg", "pipe:1",
        "-loglevel", "quiet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return stdout if stdout else None
    except asyncio.TimeoutError:
        proc.kill()
        return None


async def describe_frame_with_claude(image_bytes: bytes, brand: str = "", season: str = "",
                                      _attempt: int = 0) -> str:
    """
    Describe a runway frame using Claude Haiku vision.
    Brand/season are NOT included in the prompt — garment description only.
    Attribution comes from show metadata at display time.
    Retries on 429 rate-limit with backoff (up to 3 attempts).
    """
    import anthropic
    client = anthropic.AsyncAnthropic()
    b64 = base64.standard_b64encode(image_bytes).decode()
    try:
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Describe the garment worn by the model in this runway image in one precise sentence under 25 words. "
                            "Include: garment type, silhouette, colour, fabric or texture, and one key styling detail. "
                            "Do not describe the model's appearance, the runway, the setting, or name any fashion house."
                        ),
                    },
                ],
            }],
        )
    except anthropic.RateLimitError:
        if _attempt < 3:
            wait = 30 * (2 ** _attempt)  # 30s, 60s, 120s
            logger.warning(f"Claude Haiku rate-limited (attempt {_attempt + 1}), retrying in {wait}s")
            await asyncio.sleep(wait)
            return await describe_frame_with_claude(image_bytes, brand, season, _attempt=_attempt + 1)
        raise
    text = message.content[0].text.strip()
    # Strip any markdown headers Haiku occasionally adds, collapse to single line
    lines = [l for l in text.splitlines() if not l.startswith("#")]
    return " ".join(" ".join(lines).split())


async def _describe_clip_with_claude(brand: str, season: str, timestamp_start: float,
                                      transcription: str = "", hls_url: Optional[str] = None) -> str:
    """Describe a clip — uses vision if HLS URL provided, text fallback otherwise."""
    if hls_url:
        frame = await extract_frame(hls_url, timestamp_start)
        if frame:
            try:
                return await describe_frame_with_claude(frame, brand, season)
            except Exception as e:
                logger.warning(f"Vision description failed at {timestamp_start}s: {e}")

    # Text-only fallback
    import anthropic
    client = anthropic.AsyncAnthropic()
    context = f"Brand: {brand}, Season: {season}, Timestamp: {timestamp_start:.0f}s"
    if transcription:
        context += f", Audio: \"{transcription[:200]}\""
    message = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=60,
        messages=[{
            "role": "user",
            "content": (
                f"Fashion intelligence system. One-sentence runway description under 20 words. "
                f"Garment type, silhouette, colour, fabric, styling. No model. Fashion vocabulary.\n\n"
                f"Context: {context}"
            ),
        }],
    )
    return message.content[0].text.strip()


STATIC_THUMBNAILS_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "thumbnails")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")


def save_thumbnail(moment_id: str, image_bytes: bytes) -> str:
    """
    Save JPEG bytes to static/thumbnails/{moment_id}.jpg and return the URL.
    Creates the directory if missing.
    """
    os.makedirs(STATIC_THUMBNAILS_DIR, exist_ok=True)
    path = os.path.join(STATIC_THUMBNAILS_DIR, f"{moment_id}.jpg")
    with open(path, "wb") as f:
        f.write(image_bytes)
    return f"{API_BASE_URL}/static/thumbnails/{moment_id}.jpg"


_HEDGE_PHRASES = [
    "i can see", "cannot definitively identify", "i'm unable", "i cannot",
    "appears to be a runway", "it appears", "it seems", "i think", "i believe",
    "likely", "probably", "hard to tell", "difficult to determine",
]
_PLACEHOLDER_RE = None


def _is_valid_description(text: str) -> bool:
    """
    Return False if the description is a hedge, refusal, or placeholder.
    Brand/season "look N" placeholders and short outputs are also rejected.
    """
    import re
    global _PLACEHOLDER_RE
    if _PLACEHOLDER_RE is None:
        _PLACEHOLDER_RE = re.compile(r"\blook\s+\d+\b", re.I)

    if not text or len(text.strip()) < 15:
        return False
    low = text.lower()
    for phrase in _HEDGE_PHRASES:
        if phrase in low:
            return False
    if _PLACEHOLDER_RE.search(text):
        return False
    # "— look at Ns" pattern
    if "look at" in low:
        return False
    return True


async def _stream_analyze(video_id: str, prompt: str,
                           start: Optional[float] = None,
                           end: Optional[float] = None,
                           _attempt: int = 0) -> str:
    """
    POST /analyze with optional start/end segment, collect streamed NDJSON → full text.
    Retries up to 2 times on ReadTimeout with increasing backoff.
    """
    import json
    payload: dict = {"video_id": video_id, "prompt": prompt, "temperature": 0.1}
    if start is not None:
        payload["start"] = start
    if end is not None:
        payload["end"] = end

    # Use explicit Timeout object: connect/write short, read long (Pegasus streams slowly)
    _timeout = httpx.Timeout(connect=30.0, read=240.0, write=30.0, pool=30.0)

    text_parts = []
    try:
        async with httpx.AsyncClient(timeout=_timeout) as client:
            async with client.stream(
                "POST",
                f"{TWELVE_LABS_BASE_URL}/analyze",
                headers=get_headers(),
                json=payload,
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    logger.warning(f"_stream_analyze {response.status_code}: {body[:200]}")
                    return ""
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("event_type") == "text_generation":
                            text_parts.append(obj.get("text", ""))
                    except Exception:
                        pass
    except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
        if _attempt < 2:
            wait = 15 * (2 ** _attempt)  # 15s, 30s
            logger.warning(f"_stream_analyze timeout (attempt {_attempt + 1}), retrying in {wait}s")
            await asyncio.sleep(wait)
            return await _stream_analyze(video_id, prompt, start=start, end=end, _attempt=_attempt + 1)
        logger.error(f"_stream_analyze gave up after 3 attempts: {exc}")
        raise

    return "".join(text_parts)


def _parse_pegasus_looks(raw_text: str, video_id: str) -> list:
    """
    Parse Pegasus structured output: 'Look N | START | END | description'
    Also handles the legacy 'Look N' split format as fallback.
    Returns list of look dicts with timestamp_start/end and description.
    """
    import re
    looks = []

    # Primary: pipe-delimited format produced by our structured prompt
    pipe_pattern = re.compile(
        r"Look\s+(\d+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|(.+?)(?=\nLook\s+\d+|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    matches = pipe_pattern.findall(raw_text)

    if matches:
        for look_num, start_s, end_s, desc in matches:
            desc = desc.strip().split("\n")[0].strip()  # first line only
            looks.append({
                "video_id": video_id,
                "look_number": int(look_num),
                "description": desc if _is_valid_description(desc) else None,
                "timestamp_start": float(start_s),
                "timestamp_end": float(end_s),
                "thumbnail_url": None,
                "score": 0,
                "garments": [], "colours": [], "silhouette": None, "key_pieces": [],
            })
        return looks

    # Fallback: legacy 'Look N' split (no timestamps — caller must merge with clip list)
    sections = re.split(r"Look\s+(\d+)", raw_text, flags=re.IGNORECASE)
    for i in range(1, len(sections), 2):
        desc = sections[i + 1].strip() if i + 1 < len(sections) else ""
        looks.append({
            "video_id": video_id,
            "look_number": int(sections[i]),
            "description": desc if _is_valid_description(desc) else None,
            "timestamp_start": 0.0,
            "timestamp_end": 0.0,
            "thumbnail_url": None,
            "score": 0,
            "garments": [], "colours": [], "silhouette": None, "key_pieces": [],
        })
    return looks


async def describe_segment_with_pegasus(video_id: str, start: float, end: float) -> Optional[str]:
    """
    Per-segment Pegasus call for a single look (fallback when one-call output is vague).
    Returns a valid description string or None.
    IMPORTANT: prompt never mentions brand/house — garment description only.
    """
    prompt = (
        "Describe the garment worn by the model in this segment in one precise sentence under 25 words. "
        "Include: garment type, silhouette, colour, fabric or texture, and one key styling detail. "
        "Do not describe the model's appearance, the runway, the setting, or name any fashion house."
    )
    text = await _stream_analyze(video_id, prompt, start=start, end=end)
    if _is_valid_description(text):
        return text.strip()
    return None


async def generate_look_descriptions(video_id: str, brand: str = "", season: str = "") -> list:
    """
    Extract timestamped looks from a show using Pegasus via POST /analyze.
    One structured call per show; per-segment fallback for vague looks.
    Brand/season are NOT passed to Pegasus — garment description only.
    Attribution (brand, season) comes from the shows row at storage/display time.
    """
    # One-call structured prompt — asks for pipe-delimited look list with timestamps
    prompt = (
        "List every runway look in this show in chronological order. "
        "For each individual model's walk, output exactly this format on one line:\n"
        "Look N | START | END | description\n"
        "Where START and END are times in seconds (integers), and description is one precise sentence "
        "under 25 words covering: garment type, silhouette, colour, fabric or texture, key styling detail. "
        "Do not name any fashion house or brand. Do not describe the model's face or body. "
        "Do not describe the runway or setting. "
        "If multiple models walk together, list each as a separate look."
    )

    raw = await _stream_analyze(video_id, prompt)
    looks = _parse_pegasus_looks(raw, video_id)

    if not looks:
        logger.warning(f"Pegasus returned no parseable looks for {video_id} — falling back to search clips")
        # Fall back to TL search clips to get timestamps, then per-segment Pegasus
        look_queries = ["model walking on runway wearing outfit", "fashion look runway walk"]
        seen: set = set()
        raw_clips = []
        for query in look_queries:
            for clip in await _search_clips_for_video(video_id, query):
                bucket = round(clip["start"] / 3) * 3
                if bucket not in seen:
                    seen.add(bucket)
                    raw_clips.append(clip)
        raw_clips.sort(key=lambda c: c["start"])
        for i, clip in enumerate(raw_clips, 1):
            desc = await describe_segment_with_pegasus(video_id, clip["start"], clip["end"])
            looks.append({
                "video_id": video_id,
                "look_number": i,
                "description": desc,
                "timestamp_start": clip["start"],
                "timestamp_end": clip["end"],
                "thumbnail_url": None,
                "score": clip.get("score", 0),
                "garments": [], "colours": [], "silhouette": None, "key_pieces": [],
            })
        if not looks:
            return []

    # Fetch HLS URL once — needed for frame extraction
    hls_url = await get_hls_url(video_id)

    # Per-segment pass: fix vague descriptions + extract thumbnails
    vague_count = 0
    for look in looks:
        # Midpoint frame for both thumbnail and embedding (Phase 4)
        midpoint = (look["timestamp_start"] + look["timestamp_end"]) / 2.0
        frame = await extract_frame(hls_url, midpoint) if hls_url else None

        # Fix vague/null descriptions with per-segment Pegasus call
        if not _is_valid_description(look["description"] or ""):
            desc = await describe_segment_with_pegasus(
                video_id, look["timestamp_start"], look["timestamp_end"]
            )
            look["description"] = desc
            if desc is None:
                vague_count += 1

        # Store frame on look for caller to save after moment ID is known
        look["_frame"] = frame
        await asyncio.sleep(0.15)

    if vague_count:
        logger.info(f"{vague_count} looks left null (vague/refused) for video {video_id}")

    logger.info(f"Generated {len(looks)} looks for video {video_id} via Pegasus")
    return looks


async def get_video_summary(video_id: str, brand: str = "", season: str = "") -> str:
    """Generate a show summary using Claude."""
    import anthropic
    client = anthropic.AsyncAnthropic()
    message = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=250,
        messages=[{
            "role": "user",
            "content": (
                f"Write a professional editorial summary (150-200 words) of the {brand} {season} "
                f"runway show. Cover creative direction, key themes, dominant silhouettes, colour story, "
                f"and standout moments. Write as a fashion editor."
            ),
        }],
    )
    return message.content[0].text.strip()


async def extract_credits(video_id: str) -> dict:
    """
    Extract visible credits, name cards, and any text overlaid on the video.
    Used for attribution — who worked on what.
    Uses /analyze (POST /generate was removed in v1.3).
    """
    prompt = (
        "Extract all credits and attribution information visible in this video: "
        "any text overlays, name cards, or title sequences; brand name and season if shown; "
        "any designer, creative director, or collaborator names mentioned; "
        "sponsor or partner names if visible. Return as a structured list."
    )
    raw = await _stream_analyze(video_id, prompt)
    return {"raw": raw}


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def _parse_look_descriptions(raw_text: str, video_id: str) -> list:
    """
    Parse Pegasus free-text output into structured look objects.
    This is intentionally simple for MVP — improve with Claude in production.
    """
    looks = []
    # Split on "Look N" pattern
    import re
    sections = re.split(r"Look\s+(\d+)", raw_text, flags=re.IGNORECASE)

    look_number = 0
    for i in range(1, len(sections), 2):
        look_number = int(sections[i])
        description = sections[i + 1].strip() if i + 1 < len(sections) else ""

        looks.append({
            "video_id": video_id,
            "look_number": look_number,
            "description": description,
            "garments": [],     # Claude enrichment fills these in
            "colours": [],
            "silhouette": None,
            "key_pieces": [],
        })

    return looks
