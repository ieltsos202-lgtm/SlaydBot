import asyncio
import io
import logging
import os
import uuid

import aiohttp
from PIL import Image

import config

log = logging.getLogger(__name__)
UA = "SlaydMasterBot/1.0 (Telegram presentation bot)"
COMMONS = "https://commons.wikimedia.org/w/api.php"
PEXELS = "https://api.pexels.com/v1/search"
OPENVERSE = "https://api.openverse.org/v1/images/"


async def _pexels(session: aiohttp.ClientSession, query: str) -> list[str]:
    if not config.PEXELS_API_KEY:
        return []
    params = {"query": query, "per_page": "5", "orientation": "landscape"}
    async with session.get(PEXELS, params=params, headers={"Authorization": config.PEXELS_API_KEY}) as r:
        if r.status != 200:
            return []
        data = await r.json(content_type=None)
    return [p["src"]["large2x"] for p in data.get("photos", []) if p.get("src", {}).get("large2x")]


async def _openverse(session: aiohttp.ClientSession, query: str) -> list[str]:
    params = {"q": query, "page_size": "8", "size": "large", "mature": "false", "category": "photograph"}
    async with session.get(OPENVERSE, params=params) as r:
        if r.status != 200:
            return []
        data = await r.json(content_type=None)
    urls = []
    for item in data.get("results", []):
        w, h = item.get("width") or 0, item.get("height") or 0
        if item.get("url") and (not w or (max(w, h) >= 900 and 0.5 <= w / max(h, 1) <= 2.4)):
            urls.append(item["url"])
    return urls


async def _commons(session: aiohttp.ClientSession, query: str) -> list[str]:
    params = {
        "action": "query", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": "6", "gsrlimit": "10", "prop": "imageinfo",
        "iiprop": "url|size|mime", "iiurlwidth": "1600", "format": "json",
    }
    async with session.get(COMMONS, params=params) as r:
        if r.status != 200:
            return []
        data = await r.json(content_type=None)
    pages = sorted((data.get("query", {}).get("pages") or {}).values(), key=lambda p: p.get("index", 99))
    urls = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        width, height = info.get("width", 0), info.get("height", 1)
        if info.get("mime") in ("image/jpeg", "image/png") and max(width, height) >= 900 \
                and 0.5 <= width / max(height, 1) <= 2.4:
            urls.append(info.get("thumburl") or info.get("url"))
    return urls


async def _download(session: aiohttp.ClientSession, url: str, workdir: str) -> str | None:
    async with session.get(url) as r:
        if r.status != 200 or (r.content_length or 0) > 12_000_000:
            return None
        raw = await r.read()

    def save() -> str:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((1800, 1800))
        path = os.path.join(workdir, f"{uuid.uuid4().hex}.jpg")
        img.save(path, "JPEG", quality=86)
        return path

    return await asyncio.to_thread(save)


def _variants(query: str, fallback: str) -> list[str]:
    words = query.split()
    out = [query]
    if len(words) > 2:
        out.append(" ".join(words[:2]))
    if fallback and fallback not in out:
        out.append(fallback)
    return out


async def fetch(queries: dict, workdir: str, fallback: str = "", deadline: float = 13.0) -> dict:
    """queries: {key: search text} -> {key: local jpg path}. Missing images are skipped."""
    os.makedirs(workdir, exist_ok=True)
    used: set[str] = set()
    lock = asyncio.Lock()
    timeout = aiohttp.ClientTimeout(total=25)

    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as session:
        async def one(key, query: str):
            try:
                urls: list[str] = []
                for variant in _variants(query, fallback):
                    urls = (await _pexels(session, variant) or await _openverse(session, variant)
                            or await _commons(session, variant))
                    if urls:
                        break
                for url in urls:
                    async with lock:
                        if url in used:
                            continue
                        used.add(url)
                    path = await _download(session, url, workdir)
                    if path:
                        return key, path
            except Exception as e:
                log.warning("Image fetch failed for %r: %r", query, e)
            return key, None

        tasks = [asyncio.create_task(one(k, q)) for k, q in queries.items() if q]
        if not tasks:
            return {}
        done, pending = await asyncio.wait(tasks, timeout=deadline)
        for t in pending:
            t.cancel()
    results = [t.result() for t in done if not t.cancelled() and t.exception() is None]
    return {k: p for k, p in results if p}
