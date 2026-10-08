import asyncio
import base64
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import aiohttp

import config

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

LANG_NAMES = {
    "uz": "o'zbek tilida (lotin alifbosida)",
    "ru": "на русском языке",
    "en": "in English",
}


log = logging.getLogger(__name__)
_cooldown: dict[tuple[str, str], float] = {}
COOLDOWN_SECONDS = 300


class GeminiError(Exception):
    pass


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise GeminiError("AI javobi JSON emas")
        return json.loads(match.group(0))


ACCURACY_RULES = """ANIQLIK QOIDALARI (juda muhim):
- Faqat ishonchli, tekshirilgan faktlarni yoz. Sana, raqam, ism va joy nomlari aniq bo'lsin.
- Agar "MANBA" berilgan bo'lsa, undan FAQAT mavzuga bevosita tegishli faktlarni ol; mavzuga aloqasi
  yo'q qismlarni (boshqa tashkilot, shaxs, joy haqidagi ma'lumot) butunlay e'tiborsiz qoldir.
  Mavzudan chetga chiqma — taqdimot/referat aynan berilgan mavzu haqida bo'lsin.
- Aniq bilmagan raqamni o'ylab topma — umumiyroq, lekin to'g'ri ifoda ishlat.
- Iqtiboslar faqat haqiqiy va mashhur bo'lsin, muallifi to'g'ri ko'rsatilsin.
- Imlo va grammatika mukammal bo'lsin; o'zbek tilida lotin alifbosi va to'g'ri tutuq belgisi (o', g', ')."""


def _source_block(source: str) -> str:
    return f"\n\nMANBA (Wikipedia, faktlarni tekshirish uchun):\n\"\"\"\n{source}\n\"\"\"\n" if source else ""


def _candidates() -> list[tuple[str, str]]:
    models = [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL]
    pairs = [(m, k) for m in models for k in config.GEMINI_API_KEYS]
    now = time.time()
    fresh = [p for p in pairs if _cooldown.get(p, 0) <= now]
    return fresh or pairs


async def _call(parts: list[dict], temperature: float = 0.6) -> dict:
    if not config.GEMINI_API_KEYS:
        raise GeminiError("GEMINI_API_KEY sozlanmagan")
    timeout = aiohttp.ClientTimeout(total=240)
    last_error = "noma'lum xato"
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model, key in _candidates():
            headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
            key_no = config.GEMINI_API_KEYS.index(key) + 1
            url = URL.format(model=model)
            use_thinking = bool(config.GEMINI_THINKING)
            attempt = 0
            while attempt < 2:
                gen = {"temperature": temperature, "responseMimeType": "application/json"}
                if use_thinking:
                    gen["thinkingConfig"] = {"thinkingLevel": config.GEMINI_THINKING}
                payload = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
                started = time.time()
                try:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        data = await resp.json(content_type=None)
                        if resp.status == 200:
                            candidates = data.get("candidates") or []
                            if not candidates:
                                raise GeminiError("AI bo'sh javob qaytardi")
                            parts_out = candidates[0].get("content", {}).get("parts", [])
                            text = "".join(p.get("text", "") for p in parts_out)
                            usage = data.get("usageMetadata", {})
                            log.info("Gemini %s (key %d) ok in %.1fs (thinking=%s, out=%s, think=%s)", model,
                                     key_no, time.time() - started, use_thinking, usage.get("candidatesTokenCount"),
                                     usage.get("thoughtsTokenCount"))
                            return _parse_json(text)
                        last_error = f"{model} HTTP {resp.status}: {str(data)[:300]}"
                        log.warning("Gemini %s (key %d) failed in %.1fs: %s", model, key_no,
                                    time.time() - started, last_error[:160])
                        if resp.status == 400 and use_thinking and "thinking" in str(data).lower():
                            use_thinking = False
                            continue
                        if resp.status in (401, 403, 429) or "API_KEY" in str(data):
                            _cooldown[(model, key)] = time.time() + COOLDOWN_SECONDS
                            break
                        if resp.status == 400:
                            raise GeminiError(last_error)
                        break
                except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
                    last_error = f"{model}: {e!r}"
                attempt += 1
                if attempt < 2:
                    await asyncio.sleep(1.5)
    raise GeminiError(last_error)


async def presentation(topic: str, slides: int, lang: str, source: str = "") -> dict:
    prompt = f"""Sen dunyo darajasidagi taqdimot dizayneri va soha mutaxassisisan. Mavzu: "{topic}".
{ACCURACY_RULES}{_source_block(source)}
Barcha matnni {LANG_NAMES[lang]} yoz (faqat "image_query" ingliz tilida).
Aniq {slides} ta mazmunli slayd tuz (titul va "rahmat" slaydi bunga kirmaydi).

Har slayd uchun mazmunga eng mos "layout" tanla va ularni ARALASHTIR (bir xil layout ketma-ket 2 martadan ko'p bo'lmasin):
- "agenda": 1-slayd, reja. "bullets": 4-6 ta qisqa band (har biri 3-8 so'z).
- "bullets": asosiy matn. "bullets": 3-5 ta punkt, har biri 10-22 so'z, aniq fakt bilan.
- "cards": 3-4 ta tushuncha/omil. "items": [{{"title": "2-4 so'z", "text": "12-25 so'z"}}].
- "stats": muhim raqamlar. "stats": 3-4 ta [{{"value": "juda qisqa, 7 belgigacha: 1370, 35 yil, 70%, 2 mln, 32 ming", "label": "4-10 so'z"}}], "bullets": 1 ta qisqa izoh.
- "timeline": tarix/bosqichlar. "events": 4-5 ta [{{"year": "yil yoki bosqich (1-3 so'z)", "text": "8-16 so'z"}}].
- "quote": mashhur shaxsning mavzuga oid haqiqiy iqtibosi. "quote": {{"text": "15-35 so'z", "author": "muallif"}}.
- "two_column": taqqoslash/afzallik-kamchilik. "left"/"right": {{"title": "1-4 so'z", "bullets": ["3 ta, har biri 5-12 so'z"]}}.
Talablar: kamida {max(2, slides // 3)} ta "bullets" (ular rasm bilan chiqadi), kamida 1 ta "stats" yoki "timeline",
kamida 1 ta "cards", ko'pi bilan 1 ta "quote". "stats" faqat haqiqiy raqamlar bo'lsa ishlatilsin.
Oxirgi slayd — xulosa ("bullets" yoki "cards"). Matn faktlarga boy, aniq, takrorsiz, xatosiz bo'lsin.
"bullets" va "quote" slaydlariga hamda butun taqdimotga "image_query" ber: rasm qidirish uchun
2-4 ta inglizcha so'z, aniq ko'rinadigan predmet, joy yoki jarayon (masalan "Registan Samarkand",
"solar panels field", "padlock laptop keyboard", "server room"). Tirik odam portretlari, siyosatchilar,
logotip va matnli rasm so'rama (tarixiy shaxs mavzusi bundan mustasno). Har slayd uchun so'rov har xil bo'lsin.
Faqat shu JSON formatda javob ber:
{{"title": "qisqa, ta'sirli sarlavha (3-8 so'z)", "subtitle": "tagsarlavha (6-14 so'z)",
  "image_query": "...",
  "slides": [{{"layout": "...", "title": "slayd sarlavhasi (2-7 so'z)", "image_query": "...", ...layoutga mos maydonlar}}]}}"""
    data = await _call([{"text": prompt}])
    if not isinstance(data.get("slides"), list) or not data["slides"]:
        raise GeminiError("Slaydlar yaratilmadi")
    return data


async def referat(topic: str, pages: int, lang: str, source: str = "") -> dict:
    sections = max(3, pages // 2)
    words = pages * 280
    prompt = f"""Sen tajribali ilmiy yozuvchi va soha mutaxassisisan. Mavzu: "{topic}".
{ACCURACY_RULES}{_source_block(source)}
Referatni {LANG_NAMES[lang]} yoz. Umumiy hajm taxminan {words} so'z bo'lsin.
Tuzilma: kirish (2-3 abzats), {sections} ta asosiy bo'lim (har birida 3-5 ta to'liq abzats),
xulosa (2 abzats), 5-8 ta foydalanilgan adabiyot (real mavjud kitob/manbalar uslubida).
Matn ilmiy uslubda, mantiqiy, takrorsiz bo'lsin.
Faqat shu JSON formatda javob ber:
{{"title": "referat sarlavhasi",
  "intro": ["abzats"],
  "sections": [{{"heading": "bo'lim nomi", "paragraphs": ["abzats"]}}],
  "conclusion": ["abzats"],
  "references": ["manba"]}}"""
    data = await _call([{"text": prompt}])
    if not isinstance(data.get("sections"), list) or not data["sections"]:
        raise GeminiError("Referat bo'limlari yaratilmadi")
    return data


async def analyze_handwriting(photos: list[bytes], fonts: dict) -> dict:
    catalog = "\n".join(f'- "{k}": {v[2]}' for k, v in fonts.items())
    prompt = f"""Rasmlarda qo'lda yozilgan matn bor. Yozuv uslubini sinchiklab tahlil qil va quyidagi
qo'lyozma shriftlardan eng o'xshashini tanla:
{catalog}
Baholash: harflar ulanganmi (kursiv) yoki alohida (bosma), qiyalik burchagi (gradus, o'ngga qiya musbat,
odatda -10..30), harf o'lchami, chiziq qalinligi, harflar orasidagi masofa.
Faqat JSON qaytar:
{{"is_handwriting": true/false, "font": "kalit", "slant": son, "size": "small"|"medium"|"large",
  "weight": "thin"|"normal"|"bold", "spacing": "tight"|"normal"|"wide", "connected": true/false,
  "description": "yozuv haqida o'zbekcha 1 jumla"}}"""
    parts = [{"text": prompt}] + [
        {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(p).decode()}} for p in photos[:3]]
    data = await _call(parts, temperature=0.0)
    if data.get("font") not in fonts:
        data["font"] = "caveat"
    try:
        data["slant"] = max(-10.0, min(30.0, float(data.get("slant") or 0)))
    except (TypeError, ValueError):
        data["slant"] = 0.0
    for key, allowed, default in (("size", ("small", "medium", "large"), "medium"),
                                  ("weight", ("thin", "normal", "bold"), "normal"),
                                  ("spacing", ("tight", "normal", "wide"), "normal")):
        if data.get(key) not in allowed:
            data[key] = default
    return data


async def handwritten(topic: str, words: int, lang: str, source: str = "") -> dict:
    prompt = f"""Sen a'lochi o'quvchi/talabasan va daftarga qo'lda yoziladigan yozma ish (insho) yozasan.
Mavzu: "{topic}".
{ACCURACY_RULES}{_source_block(source)}
Matnni {LANG_NAMES[lang]} yoz.
HAJM JUDA MUHIM: jami {words} so'z ({int(words * 0.9)} dan kam emas, {int(words * 1.1)} dan ko'p emas).
Buning uchun aniq {max(3, round(words / 55))} ta abzats yoz, har biri taxminan 55 so'z.
Talablar: tabiiy, ravon, o'quvchi qo'lda yozgandek uslub; birinchi abzats mavzuga kirish, oxirgisi xulosa,
lekin "kirish", "asosiy qism", "xulosa" kabi tuzilma so'zlarini matnga yozma.
Sarlavha, ro'yxat, markdown, emoji, qavs ichidagi izohlar va maxsus belgilar ishlatma.
Faqat oddiy harflar, raqamlar va . , ! ? : ; - ' belgilaridan foydalan.
Faqat JSON qaytar: {{"title": "qisqa sarlavha", "paragraphs": ["abzats", "..."]}}"""
    data = await _call([{"text": prompt}])
    paras = [str(p).strip() for p in data.get("paragraphs") or [] if str(p).strip()]
    if not paras:
        raise GeminiError("Yozma ish matni yaratilmadi")
    return {"title": str(data.get("title") or topic).strip(), "paragraphs": paras}


def parse_amount(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = re.sub(r"[^\d.,]", "", str(value))
    s = re.sub(r"[.,]\d{1,2}$", "", s)
    s = re.sub(r"[.,]", "", s)
    return int(s) if s else None


def card_matches(data: dict, card: str) -> bool:
    return re.sub(r"\D", "", str(data.get("card_last4") or ""))[-4:] == re.sub(r"\D", "", card)[-4:]


async def verify_receipt(image: bytes, mime: str, expected_amount: int, card: str) -> dict:
    last4 = re.sub(r"\D", "", card)[-4:]
    today = datetime.now(timezone(timedelta(hours=5))).strftime("%d.%m.%Y")
    prompt = f"""Rasmda bank/to'lov ilovasidagi (Click, Payme, Uzum, bank ilovasi va h.k.)
o'tkazma cheki bormi tekshir. Kutilgan: kamida {expected_amount} so'm, qabul qiluvchi karta
oxirgi 4 raqami {last4}.
MUHIM: bugungi sana {today} (Toshkent vaqti). Chekdagi sana bugun yoki so'nggi kunlar bo'lsa bu NORMAL,
yil sababli chekni shubhali deb hisoblama. Tranzaksiya raqamining ko'rinishi ham shubha sababi emas.
Confidence "low" faqat aniq tahrir/montaj izlari bo'lsa (shrift yoki fon nomuvofiqligi, ustidan yozilgan
raqamlar), o'tkazma muvaffaqiyatsiz bo'lsa yoki rasm chek bo'lmasa qo'yiladi.
Faqat JSON qaytar:
{{"is_receipt": true/false, "success": true/false, "amount": son yoki null,
  "card_last4": "1234" yoki null, "transaction_id": "matn" yoki null,
  "confidence": "high"|"medium"|"low", "reason": "qisqa izoh"}}"""
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(image).decode()}},
    ]
    data = await _call(parts, temperature=0.0)
    amount = parse_amount(data.get("amount"))
    card_ok = card_matches(data, card)
    data["amount"] = amount
    data["ok"] = bool(
        data.get("is_receipt") and data.get("success") and amount is not None
        and amount >= expected_amount and card_ok and data.get("confidence") != "low"
    )
    return data
