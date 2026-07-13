import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .config import CACHE_DIR, UPLOADS_DIR, MATCH_THRESHOLD

_reference_embeddings: Optional[np.ndarray] = None
_face_app = None

_VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def set_reference_embeddings(embeddings: np.ndarray) -> None:
    global _reference_embeddings
    _reference_embeddings = embeddings


def set_face_app(app) -> None:
    global _face_app
    _face_app = app


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _best_similarity(embedding: np.ndarray) -> float:
    """Max cosine similarity between a face embedding and all X reference embeddings.
    Both embedding and reference vectors are L2-normalised by InsightFace, so
    cosine similarity equals the dot product.
    """
    sims = _reference_embeddings @ embedding  # shape (n_refs,)
    return float(sims.max())


def process_image(
    image_bytes: bytes,
    filename: str,
    source_url: Optional[str] = None,
) -> dict:
    """
    Run the full detection + matching pipeline for one image.
    Returns a cached result immediately if the image was seen before.
    """
    assert _reference_embeddings is not None, "Reference embeddings not loaded."
    assert _face_app is not None, "Face app not initialised."

    sha = _sha256(image_bytes)
    cache_path = CACHE_DIR / f"{sha}.json"

    if cache_path.exists():
        return json.loads(cache_path.read_text())

    # Persist image to uploads/
    ext = Path(filename).suffix.lower()
    if ext not in _VALID_EXTENSIONS:
        ext = ".jpg"
    upload_path = UPLOADS_DIR / f"{sha}{ext}"
    if not upload_path.exists():
        upload_path.write_bytes(image_bytes)

    result: dict = {
        "sha256": sha,
        "filename": filename,
        "source_url": source_url,
        "match": False,
        "faces": [],
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        # Unreadable image — store empty result and return
        cache_path.write_text(json.dumps(result))
        return result

    faces = _face_app.get(img)
    for i, face in enumerate(faces):
        sim = _best_similarity(face.normed_embedding)
        matched = sim >= MATCH_THRESHOLD
        bbox = face.bbox.astype(int)
        result["faces"].append({
            "face_index": i,
            "match": matched,
            "confidence": round(sim, 4),
            "bounding_box": {
                "x": int(bbox[0]),
                "y": int(bbox[1]),
                "w": int(bbox[2] - bbox[0]),
                "h": int(bbox[3] - bbox[1]),
            },
        })
        if matched:
            result["match"] = True

    cache_path.write_text(json.dumps(result))
    return result
