import asyncio
import json
import logging
import mimetypes
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# Mirror uvicorn's log format for our own loggers
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(name)s - %(message)s",
)
# Per-image detail: change to logging.DEBUG to see every SKIP/OK/FAIL line
logging.getLogger("backend.scraper").setLevel(logging.DEBUG)
from insightface.app import FaceAnalysis

from .config import CACHE_DIR, REFERENCE_DIR, UPLOADS_DIR
from .reference import load_reference_embeddings
from . import search as search_module
from .scraper import scrape_images

# Ensure runtime directories exist at import time
for _d in (REFERENCE_DIR, UPLOADS_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    face_app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    face_app.prepare(ctx_id=0, det_size=(640, 640))
    embeddings = load_reference_embeddings(face_app)
    search_module.set_reference_embeddings(embeddings)
    search_module.set_face_app(face_app)
    yield


app = FastAPI(title="xddsearch", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class _NoCacheAssets(BaseHTTPMiddleware):
    """Force browsers to revalidate JS and CSS on every request."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-cache"
        return response

app.add_middleware(_NoCacheAssets)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search")
async def search(
    images: List[UploadFile] = File(default=[]),
    url: Optional[str] = Form(default=None),
):
    if not images and not url:
        raise HTTPException(status_code=400, detail="Provide at least one image or a URL.")

    results = []

    for upload in images:
        data = await upload.read()
        result = await asyncio.to_thread(search_module.process_image, data, upload.filename or "upload")
        results.append(result)

    if url:
        try:
            scraped = await scrape_images(url)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to scrape URL: {exc}")
        for image_bytes, filename, source_url in scraped:
            result = await asyncio.to_thread(
                search_module.process_image, image_bytes, filename, source_url
            )
            results.append(result)

    return JSONResponse(results)


@app.post("/search/stream")
async def search_stream(
    url: str = Form(...),
    user_agent: Optional[str] = Form(default=None),
):
    """SSE endpoint — streams per-image progress while processing a URL."""

    async def event_generator():
        try:
            scraped = await scrape_images(url, user_agent=user_agent or "android")
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"
            return

        total = len(scraped)
        yield f"data: {json.dumps({'type': 'scraped', 'total': total})}\n\n"

        for i, (image_bytes, filename, source_url) in enumerate(scraped, 1):
            yield f"data: {json.dumps({'type': 'progress', 'current': i, 'total': total})}\n\n"
            result = await asyncio.to_thread(
                search_module.process_image, image_bytes, filename, source_url
            )
            yield f"data: {json.dumps({'type': 'result', 'data': result})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/search/facebook")
async def search_facebook(username: str = Form(...)):
    """SSE endpoint — downloads a Facebook profile's photos via gallery-dl and searches them."""
    if not re.match(r'^[A-Za-z0-9._\-]{1,100}$', username):
        raise HTTPException(status_code=400, detail="Invalid Facebook username.")

    profile_url = f"https://www.facebook.com/{username}"

    async def event_generator():
        yield f"data: {json.dumps({'type': 'downloading', 'count': 0})}\n\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "gallery-dl", "-d", tmpdir, "--no-mtime", profile_url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                yield f"data: {json.dumps({'type': 'error', 'detail': 'gallery-dl not found on server.'})}\n\n"
                return

            # Drain stderr concurrently to prevent pipe-buffer deadlock
            stderr_task = asyncio.create_task(proc.stderr.read())

            # Stream stdout: gallery-dl prints one downloaded file path per line
            dl_count = 0
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                filepath = line.decode(errors="replace").strip()
                if filepath:
                    dl_count += 1
                    yield f"data: {json.dumps({'type': 'downloading', 'count': dl_count, 'filename': Path(filepath).name})}\n\n"

            await proc.wait()
            stderr_bytes = await stderr_task

            _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
            images = sorted(
                f for f in Path(tmpdir).rglob("*")
                if f.is_file() and f.suffix.lower() in _IMAGE_EXTS
            )
            total = len(images)

            if total == 0 and proc.returncode != 0:
                detail = stderr_bytes.decode(errors="replace")[:500] or "gallery-dl returned no images."
                yield f"data: {json.dumps({'type': 'error', 'detail': detail})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'scraped', 'total': total})}\n\n"

            matched = 0
            for i, img_path in enumerate(images, 1):
                yield f"data: {json.dumps({'type': 'progress', 'current': i, 'total': total})}\n\n"
                image_bytes = img_path.read_bytes()
                result = await asyncio.to_thread(
                    search_module.process_image, image_bytes, img_path.name, profile_url
                )
                if result["match"]:
                    matched += 1
                    yield f"data: {json.dumps({'type': 'result', 'data': result})}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'total': total, 'matched': matched})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/images/{sha256}")
def serve_image(sha256: str):
    # Validate: must be lowercase hex only — prevents path traversal
    if not sha256 or not all(c in "0123456789abcdef" for c in sha256.lower()) or len(sha256) != 64:
        raise HTTPException(status_code=400, detail="Invalid image ID.")
    sha256 = sha256.lower()

    for path in UPLOADS_DIR.iterdir():
        if path.stem == sha256:
            mt, _ = mimetypes.guess_type(path.name)
            return FileResponse(str(path), media_type=mt or "application/octet-stream")

    raise HTTPException(status_code=404, detail="Image not found.")


# Serve frontend as a catch-all — must be mounted last so API routes take priority
_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


def run():
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    run()
