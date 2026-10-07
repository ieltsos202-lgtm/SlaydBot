import asyncio
import logging

import aiohttp

log = logging.getLogger(__name__)
UA = "SlaydMasterBot/1.0 (https://t.me/slaydmaster_uz_bot) aiohttp"
MAX_CHARS = 6000


async def _get(session: aiohttp.ClientSession, url: str, params: dict) -> dict:
    async with session.get(url, params=params) as r:
        if r.status != 200:
            log.warning("Wikipedia HTTP %s", r.status)
            return {}
        return await r.json(content_type=None)


def _stems(text: str) -> set[str]:
    words = (w.strip(".,:;!?«»\"'()—-").lower() for w in text.split())
    return {w[:6] for w in words if len(w) >= 4}


def relevance(topic: str, title: str) -> float:
    keys = _stems(topic)
    if not keys:
        return 0.0
    title_stems = _stems(title)
    return len(keys & title_stems) / len(keys)


async def _article(session: aiohttp.ClientSession, lang: str, topic: str) -> str:
    api = f"https://{lang}.wikipedia.org/w/api.php"
    params = {"action": "query", "list": "search", "srsearch": topic, "srlimit": "5", "format": "json"}
    hits = (await _get(session, api, params)).get("query", {}).get("search", [])
    scored = sorted(((relevance(topic, h["title"]), h["title"]) for h in hits), reverse=True)
    if not scored or scored[0][0] < 0.5:
        log.info("Wikipedia: mos maqola topilmadi (%s)", scored[:2])
        return ""
    title = scored[0][1]
    params = {"action": "query", "prop": "extracts", "explaintext": "1", "exsectionformat": "plain",
              "titles": title, "format": "json", "redirects": "1"}
    pages = (await _get(session, api, params)).get("query", {}).get("pages", {})
    text = next(iter(pages.values()), {}).get("extract", "") if pages else ""
    text = "\n".join(line for line in text.splitlines() if line.strip())
    return f"[{lang}.wikipedia — {title}]\n{text[:MAX_CHARS]}" if text else ""


async def context(topic: str, lang: str, timeout: float = 7.0) -> str:
    """Wikipedia'dan mavzu bo'yicha faktik kontekst. Xato/vaqt tugasa bo'sh qator."""
    langs = [lang]
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": UA},
                                         timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            results = await asyncio.wait_for(
                asyncio.gather(*(_article(session, lg, topic) for lg in langs), return_exceptions=True),
                timeout)
    except Exception as e:
        log.warning("Wikipedia context failed: %r", e)
        return ""
    return "\n\n".join(r for r in results if isinstance(r, str) and r)
