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
API_KEY = os.getenv("TWELVE_LABS_API_KEY")
INDEX_ID = os.getenv("TWELVE_LABS_INDEX_ID")


def get_headers() -> dict:
    return {
        "x-api-key": API_KEY,
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
    upload_headers = {"x-api-key": API_KEY}

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
            headers={"x-api-key": API_KEY, "Content-Type": f"multipart/form-data; boundary={boundary}"},
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
            headers={"x-api-key": API_KEY, "Content-Type": f"multipart/form-data; boundary={boundary}"},
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
) -> list:
    """
    Semantic search. Uses pgvector cosine similarity when embeddings exist,
    falls back to TL /search rank-based scoring otherwise.
    """
    from services.database import AsyncSessionLocal, Moment, Show
    from sqlalchemy import select, text as sql_text

    # Try pgvector path first
    query_vec = await embed_text(query)
    if query_vec:
        try:
            # Format floats to avoid scientific notation (pgvector can't parse 1e-05)
            vec_str = "[" + ",".join(f"{v:.8f}" for v in query_vec) + "]"
            # Interpolate directly — safe since vec_str is machine-generated floats only
            async with AsyncSessionLocal() as session:
                rows = await session.execute(
                    sql_text(f"""
                        SELECT m.id, m.show_id, m.timestamp_start, m.timestamp_end,
                               m.description, m.thumbnail_url,
                               m.embedding <=> '{vec_str}'::vector AS distance
                        FROM moments m
                        WHERE m.embedding IS NOT NULL
                        ORDER BY m.embedding <=> '{vec_str}'::vector
                        LIMIT {limit}
                    """)
                )
                pgvec_results = rows.fetchall()

            if pgvec_results:
                # Normalize scores: top result → ~0.95, rest scale down linearly
                n = len(pgvec_results)
                show_ids = list({r.show_id for r in pgvec_results})
                async with AsyncSessionLocal() as session:
                    shows_rows = await session.execute(
                        select(Show).where(Show.id.in_(show_ids))
                    )
                    shows_map = {s.id: s for s in shows_rows.scalars().all()}

                results = []
                for i, r in enumerate(pgvec_results):
                    show = shows_map.get(r.show_id)
                    # Rank-normalized score: position 0 → 0.95, last → 0.30
                    score = 0.95 - (i / max(n - 1, 1)) * 0.65
                    results.append({
                        "video_id": show.video_id if show else None,
                        "score": round(score, 4),
                        "start": r.timestamp_start,
                        "end": r.timestamp_end,
                        "thumbnail_url": r.thumbnail_url,
                        "metadata": {},
                        "_moment_id": str(r.id),
                        "_show_id": str(r.show_id),
                    })
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
            headers={"x-api-key": API_KEY},
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
                    headers={"x-api-key": API_KEY},
                )
                if r.status_code == 200:
                    thumb_list = r.json().get("hls", {}).get("thumbnail_urls", [])
                    thumbnails[vid] = thumb_list[0] if thumb_list else None
            except Exception:
                pass

    results = []
    for clip in clips:
        rank = clip.get("rank", total)
        score = max(0.0, 1.0 - (rank - 1) / max(total, 1))
        results.append({
            "video_id": clip["video_id"],
            "score": score,
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
            headers={"x-api-key": API_KEY},
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


async def get_hls_url(video_id: str) -> Optional[str]:
    """Fetch the HLS stream URL for a TL video."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{TWELVE_LABS_BASE_URL}/indexes/{INDEX_ID}/videos/{video_id}",
            headers={"x-api-key": API_KEY},
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


async def describe_frame_with_claude(image_bytes: bytes, brand: str, season: str) -> str:
    """Send a video frame to Claude vision and get a precise fashion description."""
    import anthropic
    client = anthropic.AsyncAnthropic()
    b64 = base64.standard_b64encode(image_bytes).decode()
    message = await client.messages.create(
        model="claude-sonnet-4-20250514",
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
                        f"You are a fashion intelligence system. Describe this {brand} {season} "
                        f"runway look in one precise sentence under 25 words. "
                        f"Focus on: garment type, silhouette, colour, fabric, key styling details. "
                        f"Do not describe the model, runway, or setting. "
                        f"Use specific fashion vocabulary."
                    ),
                },
            ],
        }],
    )
    return message.content[0].text.strip()


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


async def generate_look_descriptions(video_id: str, brand: str = "", season: str = "") -> list:
    """
    Extract timestamped looks from a show.
    v1.3: search (multipart) to find clips, Claude for descriptions.
    """
    look_queries = [
        "model walking on runway wearing outfit",
        "fashion look runway walk",
        "designer clothing on catwalk",
    ]

    seen_timestamps: set = set()
    raw_clips = []

    for query in look_queries:
        clips = await _search_clips_for_video(video_id, query)
        for clip in clips:
            bucket = round(clip["start"] / 3) * 3
            if bucket not in seen_timestamps:
                seen_timestamps.add(bucket)
                raw_clips.append(clip)

    raw_clips.sort(key=lambda c: c["start"])
    logger.info(f"Found {len(raw_clips)} distinct clips for video {video_id}")

    # Fetch HLS URL once per video for frame extraction
    hls_url = await get_hls_url(video_id)

    if not raw_clips:
        logger.warning(f"No clips found for {video_id}, creating single full-video moment")
        return [{
            "video_id": video_id,
            "look_number": 1,
            "description": f"{brand} {season} runway show".strip(),
            "timestamp_start": 0.0,
            "timestamp_end": 9999.0,
            "thumbnail_url": None,
            "score": 0,
            "garments": [], "colours": [], "silhouette": None, "key_pieces": [],
        }]

    looks = []
    for i, clip in enumerate(raw_clips, 1):
        transcription = clip.get("transcription", "")
        try:
            description = await _describe_clip_with_claude(
                brand, season, clip["start"], transcription, hls_url=hls_url
            )
        except Exception as e:
            logger.warning(f"Claude description failed for clip {clip['start']}s: {e}")
            description = f"{brand} {season} look {i}".strip()

        looks.append({
            "video_id": video_id,
            "look_number": i,
            "description": description,
            "timestamp_start": clip["start"],
            "timestamp_end": clip["end"],
            "thumbnail_url": clip.get("thumbnail_url"),
            "score": clip.get("score", 0),
            "garments": [], "colours": [], "silhouette": None, "key_pieces": [],
        })
        await asyncio.sleep(0.3)

    logger.info(f"Generated {len(looks)} look descriptions for video {video_id}")
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
    """
    prompt = """
    Extract all credits and attribution information visible in this video:
    - Any text overlays, name cards, or title sequences
    - Brand name and season if shown
    - Any designer, creative director, or collaborator names mentioned
    - Sponsor or partner names if visible

    Return as structured list.
    """

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{TWELVE_LABS_BASE_URL}/generate",
            headers=get_headers(),
            json={
                "video_id": video_id,
                "prompt": prompt,
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        return {"raw": response.json().get("data", "")}


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
