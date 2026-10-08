"""Chek tekshiruvini haqiqiy Gemini bilan sinash: python tests/receipt_smoke.py [rasm.jpg]"""
import asyncio
import io
import logging
import os
import sys

from PIL import Image, ImageDraw, ImageFont

import config
import gemini

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


def fake_receipt() -> bytes:
    img = Image.new("RGB", (720, 1280), (245, 246, 250))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(os.path.join(config.BASE_DIR, "assets", "fonts", "ShantellSans.ttf"), 34)
    small = ImageFont.truetype(os.path.join(config.BASE_DIR, "assets", "fonts", "ShantellSans.ttf"), 26)
    d.rectangle([0, 0, 720, 140], fill=(0, 170, 255))
    d.text((40, 45), "Click", fill="white", font=font)
    d.text((40, 220), "O'tkazma muvaffaqiyatli", fill=(20, 150, 60), font=font)
    d.text((40, 300), "5 000,00 so'm", fill=(0, 0, 0), font=font)
    last4 = config.CARD_NUMBER[-4:] if config.CARD_NUMBER else "3004"
    rows = [("Qabul qiluvchi", f"9860 16** **** {last4}"), ("Sana", "08.10.2026 17:40"),
            ("Tranzaksiya ID", "7302918846"), ("Komissiya", "0 so'm"), ("Holat", "Bajarildi")]
    for i, (k, v) in enumerate(rows):
        d.text((40, 420 + i * 70), k, fill=(110, 110, 120), font=small)
        d.text((380, 420 + i * 70), v, fill=(0, 0, 0), font=small)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


async def main():
    image = open(sys.argv[1], "rb").read() if len(sys.argv) > 1 else fake_receipt()
    print("keys:", len(config.GEMINI_API_KEYS), "card:", config.CARD_NUMBER[-4:])
    print(await gemini.verify_receipt(image, "image/jpeg", 5000, config.CARD_NUMBER))


asyncio.run(main())
