"""
Fashion Archive — Twelve Labs Service
Handles all video ingestion, indexing, and semantic search via Twelve Labs API.

Twelve Labs processes YouTube URLs natively — no downloading required.
Models used:
  - marengo2.7: semantic search, visual understanding
  - pegasus1.2: video-to-text generation (look descriptions)
"""

import os
import httpx
import asyncio
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential
import logging

logger = logging.getLogger(__name__)

TWELVE_LABS_BASE_URL = "https://api.twelvelabs.io/v1.2"
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
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=30.0,
                read=300.0,   # 5 min read timeout for large files
                write=300.0,  # 5 min write timeout for large uploads
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
async def semantic_search(
    query: str,
    limit: int = 20,
    filters: Optional[dict] = None,
) -> list:
    """
    Run semantic search across the entire indexed archive.
    Returns clips with relevance scores, timestamps, and thumbnails.

    Supports natural language queries like:
    - "red carpet gowns with dramatic trains"
    - "minimalist white structured jackets 1990s"
    - "oversized shoulders power dressing"
    """
    search_options = ["visual", "conversation"]

    payload = {
        "index_id": INDEX_ID,
        "query": query,
        "search_options": search_options,
        "page_limit": limit,
        "threshold": "medium",
    }

    if filters:
        payload["filter"] = filters

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{TWELVE_LABS_BASE_URL}/search",
            headers=get_headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    results = []
    for clip in data.get("data", []):
        results.append({
            "video_id": clip["video_id"],
            "score": clip["score"],
            "start": clip["start"],
            "end": clip["end"],
            "thumbnail_url": clip.get("thumbnail_url"),
            "metadata": clip.get("metadata", {}),
        })

    return results


# ─────────────────────────────────────────
# VIDEO ANALYSIS — LOOK EXTRACTION
# ─────────────────────────────────────────

async def generate_look_descriptions(video_id: str) -> list:
    """
    Use Pegasus to generate structured descriptions of each look in a show.
    Returns a list of timestamped look objects.

    Pegasus understands fashion context — it can identify garments,
    colours, silhouettes, and styling details from video.
    """
    prompt = """
    Analyse this fashion show video. For each distinct look (outfit change or model appearance):

    1. Identify the look number in sequence
    2. Describe the complete outfit in detail: garments, fabrics, construction
    3. List all colours present
    4. Describe the silhouette and proportion
    5. Note any standout or signature pieces
    6. Identify accessories, shoes, hair and makeup direction if visible

    Return structured data for each look. Be specific and precise —
    this data will be used for professional fashion research.
    """

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{TWELVE_LABS_BASE_URL}/generate",
            headers=get_headers(),
            json={
                "video_id": video_id,
                "prompt": prompt,
                "temperature": 0.2,  # Low temperature for factual accuracy
            },
        )
        response.raise_for_status()
        data = response.json()

    # Parse the generated text into structured looks
    raw_text = data.get("data", "")
    return _parse_look_descriptions(raw_text, video_id)


async def get_video_summary(video_id: str) -> str:
    """Generate a high-level summary of a show — themes, mood, direction."""
    prompt = """
    Provide a professional editorial summary of this fashion show:
    - Overall creative direction and mood
    - Key themes and references
    - Dominant silhouettes and proportions
    - Colour story
    - Standout moments
    - How this collection relates to broader fashion context

    Write as a fashion editor, 150-200 words.
    """

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{TWELVE_LABS_BASE_URL}/generate",
            headers=get_headers(),
            json={
                "video_id": video_id,
                "prompt": prompt,
                "temperature": 0.4,
            },
        )
        response.raise_for_status()
        return response.json().get("data", "")


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
