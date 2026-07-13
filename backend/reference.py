import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from .config import REFERENCE_DIR, REFERENCE_EMBEDDINGS, REFERENCE_META


def _folder_fingerprint(directory: Path) -> str:
    """SHA-256 over all filenames + file contents, sorted by filename."""
    _IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    h = hashlib.sha256()
    for f in sorted(directory.iterdir()):
        if f.is_file() and f.suffix.lower() in _IMAGE_SUFFIXES:
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def load_reference_embeddings(face_app) -> np.ndarray:
    """
    Return ArcFace embeddings for all X reference images.
    Uses on-disk cache when up-to-date; recomputes otherwise.
    Exits the process if any reference image is invalid.
    """
    _IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    ref_files = sorted(
        f for f in REFERENCE_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in _IMAGE_SUFFIXES
    )
    if not ref_files:
        sys.exit(
            "STARTUP ERROR: data/reference/ is empty.\n"
            "Add at least one reference image of X (one face per image) before starting."
        )

    current_hash = _folder_fingerprint(REFERENCE_DIR)

    # Attempt to load from cache
    if REFERENCE_EMBEDDINGS.exists() and REFERENCE_META.exists():
        try:
            meta = json.loads(REFERENCE_META.read_text())
            if meta.get("hash") == current_hash:
                embeddings = np.load(str(REFERENCE_EMBEDDINGS))
                print(f"[reference] Loaded {len(embeddings)} cached embedding(s) for X.")
                return embeddings
        except Exception:
            pass  # fall through and recompute

    print("[reference] Computing embeddings for X reference images...")
    embeddings = []
    for f in ref_files:
        img = cv2.imread(str(f))
        if img is None:
            sys.exit(f"STARTUP ERROR: Cannot read reference image: {f.name}")

        faces = face_app.get(img)
        if len(faces) == 0:
            sys.exit(
                f"STARTUP ERROR: No face detected in reference image: {f.name}\n"
                "Each reference image must contain exactly one clearly visible face."
            )
        if len(faces) > 1:
            sys.exit(
                f"STARTUP ERROR: {len(faces)} faces detected in reference image: {f.name}\n"
                "Each reference image must contain exactly one face."
            )

        embeddings.append(faces[0].normed_embedding)
        print(f"[reference]   OK  {f.name}")

    arr = np.array(embeddings, dtype=np.float32)
    np.save(str(REFERENCE_EMBEDDINGS), arr)
    REFERENCE_META.write_text(json.dumps({"hash": current_hash}))
    print(f"[reference] Saved {len(arr)} embedding(s) to cache.")
    return arr
