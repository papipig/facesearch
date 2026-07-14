import asyncio
import json
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
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
async def search_stream(url: str = Form(...)):
    """SSE endpoint — streams per-image progress while processing a URL."""

    async def event_generator():
        try:
            scraped = await scrape_images(url)
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
