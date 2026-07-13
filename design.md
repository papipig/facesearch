# Design & Implementation

## Overview

A web application that accepts images (via direct upload or URL scraping) and searches for the presence of a specific known individual (referred to as **X**) using face recognition. The system reports whether X appears in each image, along with a confidence score and a bounding box overlay (depends on user input cases).

---

## Input Modes

| Mode | Description | Result
|---|---|
| Single image upload | User uploads one image file | Confidence score and bounding box overlay
| Multiple image upload | User uploads a batch of image files | List of images links, with confidence score
| URL | User provides a URL; the system scrapes all images from that single page | List of images links, with confidence score

For image links: when user clicks on link, it gets same result as "single image upload" user input case.

---

## Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend | Python + **FastAPI** | Async, fast, auto-generated OpenAPI docs |
| Face recognition | **InsightFace** (ArcFace / `buffalo_l` model) | Best free accuracy; state-of-the-art, open source |
| URL scraping | `httpx` + `BeautifulSoup` | Lightweight HTTP + HTML parsing |
| Frontend | Plain HTML/CSS/JS (or minimal React) | No framework overhead needed |
| Storage | Local filesystem (reference images for X) | X is fixed and pre-loaded |

> **Why InsightFace?** ArcFace (the underlying model) consistently tops face verification benchmarks (e.g. LFW, IJB-C). InsightFace ships it as a ready-to-use Python library. It is free for non-commercial use.

---

## Reference Data — X

A fixed set of reference images of X lives in `data/reference/`. Embeddings are computed once and persisted to disk so restarts are instant.

### Startup flow

```
Startup
  │
  ▼
Load data/reference_embeddings.npy  ←── exists?
  │                                          │
  │ YES                                      │ NO (or stale)
  ▼                                          ▼
Check staleness                        Compute embeddings
(hash of reference/ vs stored hash)    from all images in data/reference/
  │                                          │
  ├── up to date → use cached embeddings     ├── each image must have exactly 1 face
  │                                          │   (crash otherwise — see below)
  └── stale → recompute & save              └── save to data/reference_embeddings.npy
                                                 + save reference_hash alongside
```

### Staleness detection
At save time, a **fingerprint** of the reference folder is stored next to the embeddings:
- Sorted list of filenames + SHA-256 of each file → combined hash.
- On startup, recompute this hash and compare. Recompute embeddings if it differs.
- Stored in `data/reference_embeddings_meta.json`.

### Reference image requirements
Each file in `data/reference/` **must contain exactly one clearly visible face**.
- 0 faces detected → **crash at startup** with a descriptive error message naming the bad file.
- >1 face detected → **crash at startup** with the same.
- This forces the operator to keep the reference set clean before the server can run.

### Updating X
1. Replace / add / remove files in `data/reference/`.
2. Delete `data/reference_embeddings.npy` and `data/reference_embeddings_meta.json` (or simply let staleness detection handle it automatically on next restart).
3. Restart the backend.

---

## Search Pipeline

```
Input images
     │
     ▼
┌─────────────────────┐
│  Face Detection      │  ← InsightFace detector (RetinaFace)
│  per image           │    returns bounding boxes + face crops
└────────┬────────────┘
         │  (one embedding per detected face)
         ▼
┌─────────────────────┐
│  Face Embedding      │  ← ArcFace encoder → 512-d vector
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Similarity vs X     │  ← cosine similarity against cached X embeddings
│  (all ref images)    │    score = max similarity across X's refs
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Decision            │  ← threshold (e.g. 0.4 cosine distance)
│  match / no match    │    configurable per deployment
└─────────────────────┘
```

### Matching logic
- For each face detected in the input image, compute its embedding.
- Compare against all X reference embeddings using **cosine similarity**.
- A face is a **match** if `max_similarity ≥ threshold`.
- Report the best-matching face per image.

---

## URL Scraping

1. Fetch the HTML of the provided URL (single page, no link following).
2. Extract all `<img src="...">` and `<source srcset="...">` URLs.
3. Download each image (skip on error / non-image content-type).
4. Feed the downloaded images into the standard search pipeline.

---

## API Design

```
POST /search
  Body: multipart/form-data
    images[]:  list of image files   (optional)
    url:       string                (optional)
  Returns: JSON results array

GET /images/<sha256>
  Returns: the raw image file (uploaded or scraped)
  Used by the frontend to display images in multi-image / URL result views

GET /health
  Returns: { "status": "ok" }
```

> Uploaded and scraped images are saved to `data/uploads/<sha256>.<ext>` at search time and served by this endpoint. Cleanup is manual.

### Response format (per image)

One object per image, with a `faces` array — **one entry per detected face**.

```json
{
  "filename": "photo.jpg",
  "source_url": null,
  "match": true,
  "faces": [
    {
      "face_index": 0,
      "match": true,
      "confidence": 0.87,
      "bounding_box": { "x": 120, "y": 45, "w": 80, "h": 90 }
    },
    {
      "face_index": 1,
      "match": false,
      "confidence": 0.21,
      "bounding_box": { "x": 300, "y": 60, "w": 75, "h": 85 }
    }
  ]
}
```

- `match` (top-level): `true` if **any** face in the image matched X
- `faces[]`: one entry per detected face, regardless of match outcome
  - `confidence`: cosine similarity score vs X (0–1)
  - `bounding_box`: pixel coords in the original image
  - `match`: whether this specific face cleared the threshold

---

## Frontend

Single-page UI with three tabs/sections:

1. **Single image** — drag-and-drop or file picker
2. **Multiple images** — multi-file picker
3. **URL** — text input for the page URL

Results displayed as a gallery: each image shown with an overlay bounding box (green = match, red = no match) and confidence score.

Bounding box rendering is done **client-side**: the frontend draws on a `<canvas>` element placed over the image, using the `bounding_box` coordinates returned by the API. No annotated image is generated server-side.

---

## Project Structure

```
xddsearch/
├── backend/
│   ├── main.py              # FastAPI app & routes
│   ├── search.py            # pipeline: detect → embed → match
│   ├── scraper.py           # URL → image list
│   ├── reference.py         # load & cache X embeddings at startup
│   └── config.py            # threshold, paths, settings
├── data/
│   ├── reference/           # reference images of X (pre-loaded, one face per image)
│   ├── reference_embeddings.npy        # cached ArcFace embeddings for X
│   ├── reference_embeddings_meta.json  # fingerprint of reference/ for staleness check
│   ├── uploads/             # images received via upload or URL scrape (served by GET /images/<sha256>)
│   └── cache/               # result cache, one JSON file per image (named by SHA-256)
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── requirements.txt
└── design.md
```

---

## Result Caching

To avoid re-running the pipeline on the same image twice, results are persisted on disk and looked up before processing.

### Cache key
The filename is the **SHA-256 hash of the image binary** (hex-encoded), e.g. `a3f8c1...d9.json`.

### Cache location
`data/cache/<sha256>.json`

### Lookup flow
```
Incoming image
     │
     ▼
Compute SHA-256 hash
     │
     ├── cache hit?  → return stored JSON directly (skip pipeline)
     │
     └── cache miss? → run pipeline → write result to data/cache/<hash>.json → return
```

### Cached file format
Same structure as the API response for that image, plus a `cached_at` timestamp:
```json
{
  "sha256": "a3f8c1...d9",
  "cached_at": "2026-07-13T10:42:00Z",
  "match": true,
  "faces": [ ... ]
}
```

---

## Decisions

| Topic | Decision |
|---|---|
| Result logging | Yes — cached per image as `data/cache/<sha256>.json` |
| Cache invalidation | Manual — user deletes `data/cache/` when X's references change |
| Upload/scrape limits | None for now |
| Image serving | Backend stores files in `data/uploads/`, served via `GET /images/<sha256>` |
| Bounding box rendering | Client-side `<canvas>` using API coordinates |
| Authentication | None |
| Video input | Out of scope |
| Multi-person mode | Out of scope — system matches against X only |
| Deployment | Local dev (uvicorn) + installable package (`pip install -e .` / startup script) |

