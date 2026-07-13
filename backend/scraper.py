import asyncio
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

_IMAGE_CONTENT_TYPES = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
})

# Hard cap: never download more than this many images from a single page.
MAX_IMAGES_PER_PAGE = 100


async def scrape_images(page_url: str) -> List[Tuple[bytes, str, str]]:
    """
    Fetch the page at page_url and download all images found on it concurrently.
    Returns a list of (image_bytes, filename, source_url).
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(page_url)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        img_urls: set = set()

        for tag in soup.find_all("img"):
            for attr in ("src", "data-src", "data-lazy-src"):
                src = tag.get(attr)
                if src:
                    img_urls.add(urljoin(page_url, src))
                    break

        for tag in soup.find_all("source"):
            srcset = tag.get("srcset", "")
            for part in srcset.split(","):
                parts = part.strip().split()
                if parts:
                    img_urls.add(urljoin(page_url, parts[0]))

        url_list = list(img_urls)[:MAX_IMAGES_PER_PAGE]

        async def fetch_one(img_url: str) -> Optional[Tuple[bytes, str, str]]:
            try:
                r = await client.get(img_url, timeout=10.0)
                ct = r.headers.get("content-type", "").split(";")[0].strip()
                if ct not in _IMAGE_CONTENT_TYPES:
                    return None
                filename = urlparse(img_url).path.rstrip("/").split("/")[-1] or "image"
                return (r.content, filename, img_url)
            except Exception:
                return None

        results = await asyncio.gather(*[fetch_one(u) for u in url_list])

    return [r for r in results if r is not None]
