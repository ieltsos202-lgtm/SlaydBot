"""Namuna taqdimot/referat yaratish (reklama skrinshotlari uchun).

Ishlatish: python make_sample.py "Mavzu" [theme] [slides] [lang]
"""
import asyncio
import json
import logging
import os
import sys
import time

import config
import docs
import gemini
import images
import slides
import wiki


async def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "Amir Temur va uning davlati"
    theme = sys.argv[2] if len(sys.argv) > 2 else "ocean"
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    lang = sys.argv[4] if len(sys.argv) > 4 else "uz"
    out = os.path.join(config.BASE_DIR, "data", "samples")
    os.makedirs(out, exist_ok=True)

    t0 = time.time()
    source = await wiki.context(topic, lang)
    print(f"wiki: {len(source)} belgi, {time.time() - t0:.1f}s")
    t1 = time.time()
    content = await gemini.presentation(topic, count, lang, source)
    print(f"gemini: {time.time() - t1:.1f}s")
    with open(os.path.join(out, "content.json"), "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
    print("layouts:", [s.get("layout") for s in content["slides"]])
    pics = await images.fetch(slides.image_queries(content, topic), os.path.join(out, "img"),
                              str(content.get("image_query") or ""))
    print("images:", len(pics), "/", len(slides.image_queries(content, topic)))
    pptx_path = os.path.join(out, f"namuna_{theme}.pptx")
    slides.build_pptx(content, theme, lang, pptx_path, pics)
    print(f"PPTX: {pptx_path} | jami {time.time() - t0:.1f}s")

    if "--docx" in sys.argv:
        ref = await gemini.referat(topic, 5, lang, source)
        docx_path = os.path.join(out, "namuna_referat.docx")
        docs.build_docx(ref, lang, docx_path)
        print("DOCX:", docx_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    asyncio.run(main())
