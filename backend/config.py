from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

REFERENCE_DIR = DATA_DIR / "reference"
REFERENCE_EMBEDDINGS = DATA_DIR / "reference_embeddings.npy"
REFERENCE_META = DATA_DIR / "reference_embeddings_meta.json"
UPLOADS_DIR = DATA_DIR / "uploads"
CACHE_DIR = DATA_DIR / "cache"
GALLERY_DL_DIR = DATA_DIR / "gallery-dl"

# Cosine similarity threshold: faces with similarity >= this value are considered a match.
# ArcFace normed embeddings; typical useful range is 0.28–0.50.
MATCH_THRESHOLD: float = 0.4
