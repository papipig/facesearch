import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_IMAGE_CONTENT_TYPES = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/bmp",
})

# Extensions to skip before making any network request.
# SVG/PDF are vector formats — they cannot contain a photo of a face.
_EXCLUDED_EXTENSIONS = frozenset({".gif", ".ico", ".svg", ".pdf"})

# Minimum pixel width encoded in CDN thumbnail URLs (e.g. "250px-photo.jpg").
# Faces need at least ~100 px across in the source image to be detectable.
_MIN_FACE_PX = 100

# Minimum downloaded file size. Anything smaller is a tracking pixel or tiny icon.
_MIN_FILE_BYTES = 2048  # 2 KB

# Matches the size prefix in CDN thumbnail paths: ".../330px-filename.jpg"
_PX_HINT_RE = re.compile(r'/(\d+)px-[^/]+$')


def _url_px_hint(path: str) -> Optional[int]:
    """Return the pixel width encoded in a CDN thumbnail path, or None."""
    m = _PX_HINT_RE.search(path)
    return int(m.group(1)) if m else None


def _dedup_thumbnails(urls: List[str]) -> List[str]:
    """
    For CDN URLs that encode a pixel size (e.g. Wikimedia's /NNpx-filename
    pattern), keep only the largest variant of each unique source image.
    URLs with no size hint are kept as-is.
    """
    best: Dict[str, Tuple[int, str]] = {}  # canonical_key -> (px, url)
    no_hint: List[str] = []
    for url in urls:
        path = urlparse(url).path
        px = _url_px_hint(path)
        if px is None:
            no_hint.append(url)
            continue
        # Canonical key: strip the NNpx- segment so all sizes of one image share a key
        key = _PX_HINT_RE.sub('', path)
        if key not in best or px > best[key][0]:
            best[key] = (px, url)
    kept = no_hint + [v[1] for v in best.values()]
    return kept

# Hard cap: never download more than this many images from a single page.
MAX_IMAGES_PER_PAGE = 100

# Known user-agent presets.  Key is sent from the frontend radio button.
USER_AGENTS: dict = {
    "android": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Mobile Safari/537.36"
    ),
    "iphone": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Mobile/15E148 Safari/604.1"
    ),
    "win-chrome": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "win-firefox": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
        "Gecko/20100101 Firefox/126.0"
    ),
    "mac-safari": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Safari/605.1.15"
    ),
}

# Total wall-clock budget for the image-download phase.
# If the budget is exceeded we return whatever has completed rather than failing.
DOWNLOAD_TIMEOUT = 120.0  # seconds

# Maximum time to honour a Retry-After header.
# Prevents a misconfigured/hostile server from sleeping every slot indefinitely.
MAX_RETRY_AFTER = 10.0  # seconds


async def scrape_images(
    page_url: str,
    user_agent: str = "android",
) -> List[Tuple[bytes, str, str]]:
    """
    Fetch the page at page_url and download all images found on it concurrently.
    Returns a list of (image_bytes, filename, source_url).
    user_agent: key from USER_AGENTS (falls back to 'android' if unknown).
    """
    ua_string = USER_AGENTS.get(user_agent, USER_AGENTS["android"])
    _HEADERS = {
        "User-Agent": ua_string,
        # Full browser navigation headers — sites like Facebook reject requests
        # that look like plain HTTP clients (return 400/403).
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    log.info("User-agent preset: %s → %s", user_agent, ua_string[:60])

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0, headers=_HEADERS) as client:
        log.info("Fetching page: %s", page_url)
        resp = await client.get(page_url)
        if resp.status_code >= 500:
            resp.raise_for_status()  # server-side error — nothing we can do
        elif resp.status_code >= 400:
            # Client error (e.g. Facebook 400, paywalled 403) — log and try to
            # parse whatever HTML was returned; OGP meta tags are often present
            # even on restricted pages.
            log.warning("Page returned HTTP %d — attempting to parse HTML anyway",
                        resp.status_code)
        log.info("Page fetched: status=%s content-type=%s",
                 resp.status_code, resp.headers.get("content-type", "?"))

        soup = BeautifulSoup(resp.text, "html.parser")
        # Use dict as an ordered set — preserves document order and is stable
        # across Python processes (unlike set, whose iteration order depends on
        # PYTHONHASHSEED and changes every run).
        img_urls: dict = {}

        for tag in soup.find_all("img"):
            # Prefer lazy-load attributes — src is often a tiny placeholder
            for attr in ("data-src", "data-lazy-src", "src"):
                src = tag.get(attr)
                if src:
                    img_urls[urljoin(page_url, src)] = None
                    break
            # Also collect from srcset on <img> (e.g. Wikipedia infoboxes)
            srcset = tag.get("srcset", "")
            for part in srcset.split(","):
                parts = part.strip().split()
                if parts:
                    img_urls[urljoin(page_url, parts[0])] = None

        # Parse <noscript> blocks — Wikipedia hides the real <img> inside them
        for noscript in soup.find_all("noscript"):
            inner = BeautifulSoup(noscript.get_text(), "html.parser")
            for tag in inner.find_all("img"):
                src = tag.get("src")
                if src:
                    img_urls[urljoin(page_url, src)] = None

        for tag in soup.find_all("source"):
            srcset = tag.get("srcset", "")
            for part in srcset.split(","):
                parts = part.strip().split()
                if parts:
                    img_urls[urljoin(page_url, parts[0])] = None

        # Open Graph / Twitter Card images — social platforms (Facebook,
        # Instagram, Twitter/X, LinkedIn) expose these meta tags even on
        # pages that require login, making them the only reliable image source.
        for tag in soup.find_all("meta", property="og:image"):
            content = tag.get("content")
            if content:
                img_urls[urljoin(page_url, content)] = None
        for tag in soup.find_all("meta", attrs={"name": "twitter:image"}):
            content = tag.get("content")
            if content:
                img_urls[urljoin(page_url, content)] = None
        for tag in soup.find_all("meta", property="og:image:secure_url"):
            content = tag.get("content")
            if content:
                img_urls[urljoin(page_url, content)] = None

        url_list = _dedup_thumbnails(list(img_urls.keys()))[:MAX_IMAGES_PER_PAGE]
        log.info("URLs extracted: %d unique after dedup (from %d raw, capped at %d)",
                 len(url_list), len(img_urls), MAX_IMAGES_PER_PAGE)

        # Limit concurrent downloads — firing 40+ requests at once triggers
        # Wikimedia's (and most CDNs') rate limiter with HTTP 429.
        sem = asyncio.Semaphore(5)

        async def fetch_one(img_url: str) -> Optional[Tuple[bytes, str, str]]:
            parsed_url = urlparse(img_url)
            path_lower = parsed_url.path.lower().split("?")[0]

            # --- Pre-request filters (no network cost) ---
            if any(path_lower.endswith(ext) for ext in _EXCLUDED_EXTENSIONS):
                log.debug("SKIP (excluded ext): %s", img_url)
                return None

            px = _url_px_hint(parsed_url.path)
            if px is not None and px < _MIN_FACE_PX:
                log.debug("SKIP (thumbnail too small: %dpx < %dpx): %s", px, _MIN_FACE_PX, img_url)
                return None

            # --- Throttled download with 429 retry ---
            async with sem:
                for attempt in range(3):
                    try:
                        r = await client.get(img_url, timeout=10.0)
                        if r.status_code == 429:
                            raw_wait = r.headers.get("Retry-After", 2 ** attempt)
                            wait = min(float(raw_wait), MAX_RETRY_AFTER)
                            log.debug("429 rate-limited (retry %d/3, wait %.1fs): %s",
                                      attempt + 1, wait, img_url)
                            await asyncio.sleep(wait)
                            continue
                        ct = r.headers.get("content-type", "").split(";")[0].strip()
                        if ct not in _IMAGE_CONTENT_TYPES:
                            log.debug("SKIP (content-type=%s status=%s): %s", ct, r.status_code, img_url)
                            return None
                        if len(r.content) < _MIN_FILE_BYTES:
                            log.debug("SKIP (file too small: %d bytes): %s", len(r.content), img_url)
                            return None
                        filename = parsed_url.path.rstrip("/").split("/")[-1] or "image"
                        log.debug("OK   (content-type=%s size=%d px=%s): %s",
                                  ct, len(r.content), px, img_url)
                        return (r.content, filename, img_url)
                    except Exception as exc:
                        log.warning("FAIL (%s): %s", exc, img_url)
                        return None
                log.warning("FAIL (429 after 3 retries): %s", img_url)
                return None

        tasks = [asyncio.create_task(fetch_one(u)) for u in url_list]
        done, pending = await asyncio.wait(tasks, timeout=DOWNLOAD_TIMEOUT)

        if pending:
            log.warning("%d image(s) still in-flight after %.0fs budget — cancelling",
                        len(pending), DOWNLOAD_TIMEOUT)
            for t in pending:
                t.cancel()
            # Await cancellations so they don't leak
            await asyncio.gather(*pending, return_exceptions=True)

        good = [t.result() for t in done if not t.exception() and t.result() is not None]
        log.info("Images fetched: %d / %d succeeded (%d timed out)",
                 len(good), len(url_list), len(pending))

    return good
