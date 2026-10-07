import io
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config
import db
import handwriting as hw


def fake_template_photo(page_index: int, font_file: str = "Pangolin.ttf", angle: float = 1.5,
                        seed: int = 1) -> bytes:
    """Daftarga yozilgan shablonni taqlid qiluvchi rasm (katak, qiyshiq, shovqinli)."""
    rng = random.Random(seed)
    w, h, cell = 1500, 1900, 38
    img = Image.new("RGB", (w, h), (236, 234, 226))
    d = ImageDraw.Draw(img)
    for y in range(0, h, cell):
        d.line([(0, y), (w, y)], fill=(170, 195, 220), width=2)
    for x in range(0, w, cell):
        d.line([(x, 0), (x, h)], fill=(170, 195, 220), width=2)
    font = ImageFont.truetype(os.path.join(hw.FONT_DIR, font_file), 64)
    y = cell * 4
    for line in hw.TEMPLATE_PAGES[page_index]["lines"]:
        x = cell * 2
        for ch in line.split():
            d.text((x + rng.randint(-3, 3), y + rng.randint(-2, 2)), ch, font=font, fill=(35, 55, 140), anchor="ls")
            x += cell * 3
        y += cell * 3
    img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=(200, 200, 195))
    arr = np.asarray(img).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    arr *= (0.82 + 0.18 * (xx / w))[..., None]
    arr += np.random.default_rng(seed).normal(0, 4, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def build_pack() -> tuple[dict, list[dict]]:
    glyphs, infos = {}, []
    for i in (0, 1):
        g, info = hw.extract_template(fake_template_photo(i, seed=i + 1), i)
        infos.append(info)
        for ch, samples in g.items():
            glyphs.setdefault(ch, []).extend(samples)
    return {"kind": "template", "glyphs": glyphs, "ink": infos[0]["ink"]}, infos


def test_extract_template_coverage():
    pack, infos = build_pack()
    expected = {c for p in hw.TEMPLATE_PAGES[:2] for line in p["lines"] for c in line.split()}
    coverage = len(set(pack["glyphs"]) & expected) / len(expected)
    assert coverage >= 0.9, (coverage, [i["missing"] for i in infos])
    assert len(pack["glyphs"]["a"]) >= 3


def test_render_pages_and_variation():
    pack, _ = build_pack()
    text = "Vatan ostonadan boshlanadi. O'zbekiston go'zal yurt. " * 40
    pages = hw.render(pack, "Mening Vatanim", [text, text], paper="katak", seed=3)
    assert len(pages) >= 2
    assert pages[0].size == (int(165 * hw.PX_MM), int(205 * hw.PX_MM))
    rng = random.Random(1)
    arr, top, _ = pack["glyphs"]["a"][0]
    a1, _, _ = hw.vary(arr, top, rng)
    a2, _, _ = hw.vary(arr, top, rng)
    assert a1.shape != a2.shape or not np.allclose(a1, a2)


def test_font_pack_render_and_capacity():
    for key in hw.FONTS:
        pack = {"kind": "font", "font": key, "slant": 8, "weight": "normal", "ink": (30, 50, 140)}
        pages = hw.render(pack, "Sarlavha", ["Привет, мир! O'zbekiston 2026."], paper="a4", seed=1)
        assert len(pages) == 1
        assert hw.capacity_words(pack, "katak") > 40


async def test_pack_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "DATABASE_URL", "")
    await db.init()
    pack, _ = build_pack()
    await hw.save_pack(5, pack)
    await hw.save_pack(5, {"kind": "font", "font": "caveat"})
    assert (await hw.load_pack(5))["font"] == "caveat"
    await hw.save_pack(7, pack)
    assert set((await hw.load_pack(7))["glyphs"]) == set(pack["glyphs"])
    assert await hw.load_pack(6) is None


def test_pg_sql_translation():
    assert db.pg_sql("SELECT * FROM users WHERE id=? AND x=?") == "SELECT * FROM users WHERE id=$1 AND x=$2"
    assert db.pg_sql("INSERT INTO payments (a) VALUES (?)").endswith("VALUES ($1) RETURNING id")
    schema = db.pg_sql(db.SCHEMA)
    assert "BIGSERIAL PRIMARY KEY" in schema and "BYTEA" in schema and "INTEGER" not in schema
