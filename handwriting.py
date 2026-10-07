"""Qo'lyozmani klonlash va yozma ishni qo'lyozma ko'rinishida chizish.

Ikki rejim:
- "template": foydalanuvchi shablonni daftarga yozadi, har bir harfning haqiqiy shakli kesib olinadi.
- "font": erkin rasm bo'yicha AI tanlagan qo'lyozma shrift + foydalanuvchi uslubi (qiyalik, qalinlik...).
Har ikkala holatda har bir harf har safar biroz o'zgartirib (masshtab, burilish, elastik deformatsiya) chiziladi.
"""
import gzip
import io
import math
import os
import pickle
import random
from datetime import datetime, timezone

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

import config

FONT_DIR = os.path.join(config.BASE_DIR, "assets", "fonts")
HAND_DIR = os.path.join(config.BASE_DIR, "data", "hand")
STD_XH = 48

FONTS = {
    "caveat": ("Caveat.ttf", 4, "tabiiy, tez yozilgan oddiy qo'lyozma, yumaloq harflar, biroz qiya"),
    "marck": ("MarckScript.ttf", 14, "nafis, qiya, harflari bir-biriga ulangan kursiv (klassik maktab yozuvi)"),
    "badscript": ("BadScript.ttf", 18, "ingichka, cho'zinchoq, kuchli qiya, ulangan kursiv"),
    "neucha": ("Neucha.ttf", 0, "bosma harflarga o'xshash, tik, sodda, biroz burchakli"),
    "pangolin": ("Pangolin.ttf", 0, "bosma, yumaloq, aniq va toza, tik yozuv"),
    "shantell": ("ShantellSans.ttf", 0, "bosma, keng, norasmiy, qalinroq yozuv"),
}

TEMPLATE_PAGES = [
    {"title": "1-varaq: kichik harflar va raqamlar", "required": True, "lines": [
        "a a a b b b c c c d d d", "e e e f f f g g g h h h", "i i i j j j k k k l l l",
        "m m m n n n o o o p p p", "q q q r r r s s s t t t", "u u u v v v w w w x x x",
        "y y y z z z ' ' ' . . .", "0 1 2 3 4 5 6 7 8 9 , ,"]},
    {"title": "2-varaq: bosh harflar va belgilar", "required": True, "lines": [
        "a o e n m u", "A A B B C C D D E E F F", "G G H H I I J J K K L L",
        "M M N N O O P P Q Q R R", "S S T T U U V V W W X X", "Y Y Z Z ! ! ? ? : ; - ( )"]},
    {"title": "3-varaq (ixtiyoriy, rus tilida yozish uchun): kirill harflari", "required": False, "lines": [
        "а а б б в в г г д д е е", "ё ё ж ж з з и и й й к к", "л л м м н н п п т т ф ф",
        "ц ц ч ч ш ш щ щ ъ ъ ы ы", "ь ь э э ю ю я я", "Б Б Г Г Д Д Ж Ж З З И И",
        "Л Л П П Ф Ф Ц Ц Ч Ч Ш Ш", "Щ Щ Э Э Ю Ю Я Я Й Й Ё Ё"]},
]

XH_SET = set("acemnorsuvwxz") | set("авгежзиклмнопстхчшъыьэюя")
NO_BASE = set("gjpqy,;'-()") | set("друфцщ")
ALIASES = {
    "ʻ": "'", "ʼ": "'", "‘": "'", "’": "'", "`": "'", "´": "'",
    "–": "-", "—": "-", "…": ".",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "У": "Y", "Х": "X",
}
PAPERS = {
    "katak": {"name": "📐 Katak daftar", "w": 165, "h": 205, "grid": 5.0, "ruled": None, "margin": True,
              "pitch": 10.0, "xh": 2.6, "left": 10.0, "top": 15.0, "bottom": 8.0},
    "chiziqli": {"name": "📏 Chiziqli daftar", "w": 165, "h": 205, "grid": None, "ruled": 8.0, "margin": True,
                 "pitch": 8.0, "xh": 2.5, "left": 10.0, "top": 16.0, "bottom": 8.0},
    "a4": {"name": "📄 Oq A4 varaq", "w": 210, "h": 297, "grid": None, "ruled": None, "margin": False,
           "pitch": 9.0, "xh": 2.7, "left": 22.0, "top": 22.0, "bottom": 20.0},
}
INKS = {"auto": None, "blue": (28, 52, 150), "black": (32, 32, 38), "violet": (70, 40, 140)}
PX_MM = 8


class HandwritingError(Exception):
    pass


# ---------------------------------------------------------------- image analysis

def _load(image_bytes: bytes) -> np.ndarray:
    img = ImageOps.exif_transpose(Image.open(__import__("io").BytesIO(image_bytes))).convert("RGB")
    img.thumbnail((2200, 2200))
    return np.asarray(img)


def _ink_map(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    small = cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    k = max(9, (min(small.shape) // 25) | 1)
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 3)
    bg = cv2.resize(bg, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)
    norm = np.clip(gray / np.maximum(bg, 1.0), 0, 1)
    return np.clip((1.0 - norm) / 0.5, 0, 1)


def _threshold(ink: np.ndarray) -> float:
    vals = (ink[ink > 0.12] * 255).astype(np.uint8)
    if vals.size < 100:
        return 0.3
    otsu, _ = cv2.threshold(vals.reshape(1, -1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(np.clip(otsu / 255, 0.28, 0.62))


def _binary(ink: np.ndarray) -> np.ndarray:
    u8 = (ink * 255).astype(np.uint8)
    bw = (u8 > int(_threshold(ink) * 255)).astype(np.uint8)
    h, w = bw.shape
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w // 25), 1)))
    lines |= cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, h // 25))))
    lines = cv2.dilate(lines, np.ones((3, 3), np.uint8))
    clean = bw & (1 - lines)
    repaired = cv2.morphologyEx(clean, cv2.MORPH_CLOSE, np.ones((5, 3), np.uint8))
    clean |= repaired & lines & bw
    return cv2.morphologyEx(clean, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def _deskew_angle(bw: np.ndarray) -> float:
    small = cv2.resize(bw * 255, None, fx=0.35, fy=0.35, interpolation=cv2.INTER_AREA)
    h, w = small.shape
    best, best_score = 0.0, -1.0
    for angle in np.arange(-6, 6.01, 0.25):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rot = cv2.warpAffine(small, m, (w, h))
        score = float(np.var(rot.sum(axis=1)))
        if score > best_score:
            best, best_score = float(angle), score
    return best


def _rotate(arr: np.ndarray, angle: float) -> np.ndarray:
    h, w = arr.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    border = (255, 255, 255) if arr.ndim == 3 else 0
    return cv2.warpAffine(arr, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=border)


def ink_color(rgb: np.ndarray, ink: np.ndarray) -> tuple[int, int, int]:
    sel = ink > 0.65
    if sel.sum() < 50:
        return INKS["blue"]
    col = np.median(rgb[sel], axis=0)
    darkest = np.array(col, dtype=np.float32)
    lum = darkest.mean()
    if lum > 110:
        darkest *= 110 / lum
    return tuple(int(c) for c in darkest)


def photo_ink(image_bytes: bytes) -> tuple[int, int, int]:
    rgb = _load(image_bytes)
    return ink_color(rgb, _ink_map(rgb))


def template_text(page_index: int) -> str:
    return "\n".join(TEMPLATE_PAGES[page_index]["lines"])


class _Unit:
    __slots__ = ("labels", "x0", "y0", "x1", "y1")

    def __init__(self, label, x0, y0, x1, y1):
        self.labels, self.x0, self.y0, self.x1, self.y1 = [label], x0, y0, x1, y1

    def absorb(self, other: "_Unit"):
        self.labels += other.labels
        self.x0, self.y0 = min(self.x0, other.x0), min(self.y0, other.y0)
        self.x1, self.y1 = max(self.x1, other.x1), max(self.y1, other.y1)

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2

    @property
    def h(self):
        return self.y1 - self.y0


def _units(bw: np.ndarray) -> tuple[list[_Unit], np.ndarray, float]:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    raw = [(i, *stats[i]) for i in range(1, n) if stats[i][4] >= 8]
    if len(raw) < 5:
        raise HandwritingError("Rasmda yozuv topilmadi")
    heights = np.array([r[4] for r in raw])
    big = heights[heights >= np.percentile(heights, 50)]
    mh = float(np.median(big))
    units = []
    for i, x, y, w, h, area in raw:
        if max(w, h) < 0.08 * mh or h > 4.5 * mh or w > 8 * mh:
            continue
        units.append(_Unit(i, x, y, x + w, y + h))
    units.sort(key=lambda u: u.x0)
    parent = list(range(len(units)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    max_w = max((u.x1 - u.x0 for u in units), default=0)
    for a, ua in enumerate(units):
        for b in range(a + 1, len(units)):
            ub = units[b]
            if ub.x0 > ua.x0 + max_w:
                break
            overlap = min(ua.x1, ub.x1) - max(ua.x0, ub.x0)
            if overlap <= 0 or overlap < 0.5 * min(ua.x1 - ua.x0, ub.x1 - ub.x0):
                continue
            if max(ua.y0, ub.y0) - min(ua.y1, ub.y1) < 0.6 * mh:
                parent[find(b)] = find(a)
    groups: dict[int, _Unit] = {}
    for i, u in enumerate(units):
        root = find(i)
        if root in groups:
            groups[root].absorb(u)
        else:
            groups[root] = u
    return list(groups.values()), labels, mh


def _lines(units: list[_Unit], mh: float) -> list[list[_Unit]]:
    rows: list[list[_Unit]] = []
    for u in sorted(units, key=lambda u: u.cy):
        if rows and u.cy - np.mean([v.cy for v in rows[-1]]) < 0.65 * mh:
            rows[-1].append(u)
        else:
            rows.append([u])
    rows = [sorted(r, key=lambda u: u.x0) for r in rows]
    return [r for r in rows if len(r) >= 2 or (r and r[0].h > 0.5 * mh)]


def _fit_count(row: list[_Unit], n: int) -> list[_Unit] | None:
    row = list(row)
    while len(row) > n:
        gaps = [row[i + 1].x0 - row[i].x1 for i in range(len(row) - 1)]
        i = int(np.argmin(gaps))
        row[i].absorb(row[i + 1])
        row.pop(i + 1)
    return row if len(row) == n else None


def _align(expected: list[list[str]], rows: list[list[_Unit]]) -> list[tuple[list[str], list[_Unit]]]:
    """Order-preserving alignment of template lines to detected rows (DP)."""
    E, D = len(expected), len(rows)
    INF = 10 ** 9
    cost = [[INF] * (D + 1) for _ in range(E + 1)]
    back = [[None] * (D + 1) for _ in range(E + 1)]
    cost[0][0] = 0
    for i in range(E + 1):
        for j in range(D + 1):
            c = cost[i][j]
            if c >= INF:
                continue
            if j < D and c + len(rows[j]) < cost[i][j + 1]:
                cost[i][j + 1], back[i][j + 1] = c + len(rows[j]), (i, j, "skip_row")
            if i < E and c + 2 * len(expected[i]) < cost[i + 1][j]:
                cost[i + 1][j], back[i + 1][j] = c + 2 * len(expected[i]), (i, j, "skip_line")
            if i < E and j < D:
                diff = len(rows[j]) - len(expected[i])
                m = c + (abs(diff) if diff >= 0 else 3 * -diff)
                if m < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1], back[i + 1][j + 1] = m, (i, j, "match")
    pairs, i, j = [], E, D
    while (i, j) != (0, 0) and back[i][j]:
        pi, pj, op = back[i][j]
        if op == "match":
            pairs.append((expected[pi], rows[pj]))
        i, j = pi, pj
    return pairs[::-1]


def _plausible(ch: str, top: float, bottom: float) -> bool:
    h = bottom - top
    if ch in XH_SET:
        return 0.55 <= h <= 1.7 and -0.4 <= bottom <= 0.45
    if ch.isalpha() or ch.isdigit():
        return 0.8 <= h <= 3.2
    return h <= 2.6


def extract_template(image_bytes: bytes, page_index: int) -> tuple[dict, dict]:
    """Shablon rasmidan glyphlarni ajratadi. Qaytaradi: (glyphs, info)."""
    page = TEMPLATE_PAGES[page_index]
    rgb = _load(image_bytes)
    ink = _ink_map(rgb)
    angle = _deskew_angle((ink > _threshold(ink)).astype(np.uint8))
    if abs(angle) >= 0.25:
        rgb, ink = _rotate(rgb, angle), _rotate(ink, angle)
    bw = _binary(ink)
    units, labels, mh = _units(bw)
    rows = _lines(units, mh)
    expected = [line.split() for line in page["lines"]]
    pairs = _align(expected, rows)

    assigned = []
    for chars, row in pairs:
        fitted = _fit_count(row, len(chars))
        if fitted:
            assigned.append((chars, fitted))
    xh_samples = [u.h for chars, row in assigned for ch, u in zip(chars, row) if ch in XH_SET]
    if len(xh_samples) < 3:
        raise HandwritingError("Harflar aniqlanmadi. Rasm tiniq, yorug' va to'g'ri olinganiga ishonch hosil qiling.")
    xh = float(np.median(xh_samples))
    scale = STD_XH / xh

    glyphs: dict[str, list] = {}
    bad = 0
    for chars, row in assigned:
        base_pts = [(u.cx, u.y1) for ch, u in zip(chars, row) if ch not in NO_BASE]
        if len(base_pts) >= 3:
            xs, ys = np.array(base_pts).T
            slope, icpt = np.polyfit(xs, ys, 1)
        else:
            slope, icpt = 0.0, float(np.median([u.y1 for u in row]))
        line_glyphs = []
        for ch, u in zip(chars, row):
            base = slope * u.cx + icpt
            top, bottom = (u.y0 - base) / xh, (u.y1 - base) / xh
            if not _plausible(ch, top, bottom):
                bad += 1
                continue
            pad = 3
            y0, y1 = max(0, u.y0 - pad), min(bw.shape[0], u.y1 + pad)
            x0, x1 = max(0, u.x0 - pad), min(bw.shape[1], u.x1 + pad)
            region = np.isin(labels[y0:y1, x0:x1], u.labels).astype(np.uint8)
            region = cv2.dilate(region, np.ones((3, 3), np.uint8), iterations=1)
            crop = np.clip((ink[y0:y1, x0:x1] - 0.12) / 0.88, 0, 1) * region
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            line_glyphs.append((ch, (crop * 255).astype(np.uint8), (y0 - base) / xh, (y1 - base) / xh))
        if len(line_glyphs) >= 0.7 * len(chars):
            for ch, arr, top, bottom in line_glyphs:
                glyphs.setdefault(ch, []).append((arr, float(top), float(bottom)))
    expected_chars = {c for line in expected for c in line}
    info = {"found": sorted(glyphs), "missing": sorted(expected_chars - set(glyphs)),
            "ink": ink_color(rgb, ink), "rows": len(rows), "bad": bad}
    return glyphs, info


# ---------------------------------------------------------------- glyph providers

class FontSource:
    def __init__(self, key: str, slant: float = 0.0, weight: str = "normal"):
        file, native, _ = FONTS.get(key, FONTS["caveat"])
        self.font_path = os.path.join(FONT_DIR, file)
        probe = ImageFont.truetype(self.font_path, 200)
        x_top = probe.getbbox("x", anchor="ls")[1]
        self.size = max(10, int(round(200 * STD_XH / max(1, -x_top))))
        self.font = ImageFont.truetype(self.font_path, self.size)
        self.shear = math.tan(math.radians(max(-12.0, min(15.0, slant - native))))
        self.weight = weight
        self.cache: dict[str, tuple] = {}

    def get(self, ch: str):
        if ch in self.cache:
            return self.cache[ch]
        if not ch.strip():
            return None
        if ch in "ʻʼ‘’`´":
            self.cache[ch] = self.get("'")
            return self.cache[ch]
        pad = self.size
        img = Image.new("L", (self.size * 2 + pad * 2, self.size * 3), 0)
        base_y = self.size * 2
        ImageDraw.Draw(img).text((pad, base_y), ch, font=self.font, fill=255, anchor="ls")
        arr = np.asarray(img, dtype=np.float32) / 255.0
        if self.shear:
            h, w = arr.shape
            m = np.float32([[1, -self.shear, self.shear * base_y], [0, 1, 0]])
            arr = cv2.warpAffine(arr, m, (w, h), flags=cv2.INTER_LINEAR)
        if self.weight == "bold":
            arr = cv2.dilate(arr, np.ones((3, 3), np.uint8))
        elif self.weight == "thin":
            arr = cv2.erode(arr, np.ones((2, 2), np.uint8))
        ys, xs = np.nonzero(arr > 0.05)
        if not len(xs):
            self.cache[ch] = None
            return None
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        out = (((arr[y0:y1, x0:x1]) * 255).astype(np.uint8), (y0 - base_y) / STD_XH, (y1 - base_y) / STD_XH,
               (x0 - pad) / STD_XH, self.font.getlength(ch) / STD_XH)
        self.cache[ch] = [out]
        return self.cache[ch]


class TemplateSource:
    def __init__(self, glyphs: dict, fallback: FontSource):
        self.glyphs = glyphs
        self.fallback = fallback

    def get(self, ch: str):
        if ch in self.glyphs:
            return self.glyphs[ch]
        alias = ALIASES.get(ch)
        if alias and alias in self.glyphs:
            return self.glyphs[alias]
        if ch.isupper() and ch.lower() in self.glyphs:
            return [(cv2.resize(a, None, fx=1.35, fy=1.35), t * 1.35, b * 1.35) for a, t, b in self.glyphs[ch.lower()]]
        if ch.islower() and ch.upper() in self.glyphs:
            return [(cv2.resize(a, None, fx=0.75, fy=0.75), t * 0.75, b * 0.75) for a, t, b in self.glyphs[ch.upper()]]
        if ch in "\"«»“”„" and "'" in self.glyphs:
            out = []
            for a, t, b in self.glyphs["'"]:
                gap = max(2, a.shape[1] // 3)
                pair = np.zeros((a.shape[0], a.shape[1] * 2 + gap), np.uint8)
                pair[:, :a.shape[1]] = a
                pair[:, a.shape[1] + gap:] = np.maximum(pair[:, a.shape[1] + gap:], a)
                out.append((pair, t, b))
            return out
        return self.fallback.get(ch)


def make_source(pack: dict):
    if pack["kind"] == "template":
        return TemplateSource(pack["glyphs"], FontSource(pack.get("fallback_font", "caveat")))
    return FontSource(pack.get("font", "caveat"), float(pack.get("slant", 0)), pack.get("weight", "normal"))


# ---------------------------------------------------------------- storage

def pack_path(user_id: int) -> str:
    return os.path.join(HAND_DIR, f"{user_id}.pkl.gz")


def save_pack(user_id: int, pack: dict) -> str:
    os.makedirs(HAND_DIR, exist_ok=True)
    pack = dict(pack, saved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    path = pack_path(user_id)
    with gzip.open(path, "wb") as f:
        pickle.dump(pack, f)
    return path


def load_pack(user_id: int) -> dict | None:
    path = pack_path(user_id)
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------- variation

def vary(arr: np.ndarray, top: float, rng: random.Random, strength: float = 1.0):
    """Harf nusxasini tabiiy o'zgartiradi: masshtab, burilish, qiyalik, elastik deformatsiya."""
    src = arr.astype(np.float32) / 255.0 if arr.dtype == np.uint8 else arr
    h, w = src.shape
    pad = int(0.3 * STD_XH)
    canvas = np.zeros((h + 2 * pad, w + 2 * pad), np.float32)
    canvas[pad:pad + h, pad:pad + w] = src
    base_y = pad - top * STD_XH
    cx = pad + w / 2
    sx = 1 + rng.gauss(0, 0.035) * strength
    sy = 1 + rng.gauss(0, 0.04) * strength
    rot = math.radians(rng.gauss(0, 1.8) * strength)
    shear = rng.gauss(0, 0.045) * strength
    c, s = math.cos(rot), math.sin(rot)
    a = np.array([[c, -s], [s, c]]) @ np.array([[1, shear], [0, 1]]) @ np.diag([sx, sy])
    t = np.array([cx, base_y]) - a @ np.array([cx, base_y])
    m = np.hstack([a, t[:, None]]).astype(np.float32)
    hh, ww = canvas.shape
    out = cv2.warpAffine(canvas, m, (ww, hh), flags=cv2.INTER_LINEAR)
    if strength > 0:
        nrng = np.random.default_rng(rng.randrange(1 << 30))
        fields = []
        for _ in range(2):
            f = cv2.GaussianBlur(nrng.random((hh, ww)).astype(np.float32) - 0.5, (0, 0), STD_XH / 5)
            fields.append(f / (f.std() + 1e-6) * 0.9 * strength)
        gy, gx = np.mgrid[0:hh, 0:ww].astype(np.float32)
        out = cv2.remap(out, gx + fields[0], gy + fields[1], cv2.INTER_LINEAR)
    ys, xs = np.nonzero(out > 0.03)
    if not len(xs):
        return src, top, top + h / STD_XH
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return out[y0:y1, x0:x1], (y0 - base_y) / STD_XH, (y1 - base_y) / STD_XH


# ---------------------------------------------------------------- rendering

def _geometry(spec: dict) -> dict:
    w, h = int(spec["w"] * PX_MM), int(spec["h"] * PX_MM)
    left = spec["left"] * PX_MM
    right = w - (23 if spec["margin"] else spec["left"] - 4) * PX_MM
    step = (spec["grid"] or spec["ruled"] or spec["pitch"]) * PX_MM
    first = round(spec["top"] * PX_MM / step) * step
    return {"w": w, "h": h, "left": left, "right": right, "first": first,
            "pitch": spec["pitch"] * PX_MM, "bottom": h - spec["bottom"] * PX_MM, "step": step}


def _paper(spec: dict, geo: dict, rng: random.Random) -> np.ndarray:
    w, h = geo["w"], geo["h"]
    nrng = np.random.default_rng(rng.randrange(1 << 30))
    page = np.empty((h, w, 3), np.float32)
    page[:] = (250, 249, 244)
    page += nrng.normal(0, 2.0, (h, w, 1)).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = rng.random(), rng.random()
    page *= (1 - 0.04 * ((xx / w - cx) ** 2 + (yy / h - cy) ** 2))[..., None]
    img = Image.fromarray(np.clip(page, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img, "RGBA")
    if spec["grid"]:
        step = spec["grid"] * PX_MM
        for y in np.arange(geo["first"] % step, h, step):
            d.line([(0, y), (w, y)], fill=(110, 160, 210, 120), width=1)
        for x in np.arange(geo["left"] % step, w, step):
            d.line([(x, 0), (x, h)], fill=(110, 160, 210, 120), width=1)
    if spec["ruled"]:
        step = spec["ruled"] * PX_MM
        for y in np.arange(geo["first"] % step, h, step):
            d.line([(0, y), (w, y)], fill=(100, 150, 205, 140), width=2)
    if spec["margin"]:
        x = w - 20 * PX_MM
        d.line([(x, 0), (x, h)], fill=(215, 70, 85, 170), width=2)
    return np.asarray(img).astype(np.float32)


def _word(source, word: str, scale: float, spacing: float, rng: random.Random):
    """So'zni harflar ro'yxatiga aylantiradi: [(alpha, dx, top_px)], width."""
    parts, x = [], 0.0
    xh = STD_XH * scale
    for ch in word:
        samples = source.get(ch)
        if not samples:
            continue
        sample = rng.choice(samples)
        arr, top = sample[0], sample[1]
        g, gtop, _ = vary(arr, top, rng)
        if scale != 1:
            g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        if len(sample) > 3:
            lsb, adv = sample[3], sample[4]
            parts.append((g, x + lsb * xh, gtop * xh))
            x += adv * xh * (1 + rng.gauss(0, 0.02)) * (0.92 + 0.08 * spacing)
        else:
            parts.append((g, x, gtop * xh))
            x += g.shape[1] + max(1.0, (0.09 + rng.gauss(0, 0.025)) * xh * spacing)
    return parts, x


def _blit(page: np.ndarray, alpha: np.ndarray, x: float, y: float, color: np.ndarray, opacity: float, rng):
    h, w = alpha.shape
    x0, y0 = int(round(x)), int(round(y))
    px0, py0 = max(0, x0), max(0, y0)
    px1, py1 = min(page.shape[1], x0 + w), min(page.shape[0], y0 + h)
    if px1 <= px0 or py1 <= py0:
        return
    a = alpha[py0 - y0:py1 - y0, px0 - x0:px1 - x0] * opacity
    a = a * (0.9 + 0.1 * np.random.default_rng(rng.randrange(1 << 30)).random(a.shape, dtype=np.float32))
    region = page[py0:py1, px0:px1]
    region *= 1 - a[..., None] * (1 - color / 255.0)


def render(pack: dict, title: str, paragraphs: list[str], paper: str = "katak", ink: str = "auto",
           seed: int | None = None, max_pages: int = 12) -> list[Image.Image]:
    rng = random.Random(seed)
    source = make_source(pack)
    spec = PAPERS.get(paper, PAPERS["katak"])
    geo = _geometry(spec)
    color = np.array(INKS.get(ink) or pack.get("ink") or INKS["blue"], np.float32)
    size = {"small": 0.88, "medium": 1.0, "large": 1.13}.get(pack.get("size", "medium"), 1.0)
    spacing = {"tight": 0.8, "normal": 1.0, "wide": 1.25}.get(pack.get("spacing", "normal"), 1.0)
    xh = spec["xh"] * PX_MM * size
    scale = xh / STD_XH

    pages: list[np.ndarray] = []
    state = {"line": 0, "page": None}

    def new_page():
        state["page"] = _paper(spec, geo, rng)
        pages.append(state["page"])
        state["line"] = 0

    def baseline():
        return geo["first"] + state["line"] * geo["pitch"]

    def next_line():
        state["line"] += 1
        if baseline() > geo["bottom"]:
            if len(pages) >= max_pages:
                raise HandwritingError("Matn juda uzun")
            new_page()

    def draw_line(words: list, start_x: float):
        page = state["page"]
        y_base = baseline() - 1.5
        slope = rng.gauss(0, 0.004)
        x = start_x
        for parts, width in words:
            dy = rng.gauss(0, 0.035) * xh
            opacity = min(1.0, max(0.8, rng.gauss(0.94, 0.04)))
            for g, dx, top_px in parts:
                gx = x + dx
                gy = y_base + (gx - start_x) * slope + dy + top_px + rng.gauss(0, 0.025) * xh
                _blit(page, g, gx, gy, color, opacity, rng)
            x += width + max(xh * 0.35, (0.62 + rng.gauss(0, 0.08)) * xh * spacing)

    def layout(text: str, indent: float, center: bool = False):
        words = [_word(source, w, scale * (1 + rng.gauss(0, 0.02)), spacing, rng) for w in text.split()]
        line, x_used = [], 0.0
        avail = geo["right"] - geo["left"]
        first_line = True
        gap = 0.62 * xh * spacing
        for wd in words:
            limit = avail - (indent if first_line else 0)
            need = wd[1] if not line else x_used + gap + wd[1]
            if line and need > limit:
                start = geo["left"] + (indent if first_line else 0) + rng.gauss(0, 0.4) * PX_MM
                if center:
                    start = geo["left"] + (avail - x_used) / 2
                draw_line(line, start)
                next_line()
                first_line = False
                line, x_used = [], 0.0
                need = wd[1]
            line.append(wd)
            x_used = need
        if line:
            start = geo["left"] + (indent if first_line else 0) + rng.gauss(0, 0.4) * PX_MM
            if center:
                start = geo["left"] + (avail - x_used) / 2
            draw_line(line, start)
            next_line()

    new_page()
    if title:
        layout(title, 0, center=True)
    for para in paragraphs:
        if para.strip():
            layout(para.strip(), 2 * spec.get("grid", 5) * PX_MM if spec["grid"] else 10 * PX_MM)
    if state["line"] == 0 and len(pages) > 1:
        pages.pop()
    return [Image.fromarray(np.clip(p, 0, 255).astype(np.uint8)) for p in pages]


def capacity_words(pack: dict, paper: str) -> int:
    """Bir sahifaga sig'adigan taxminiy so'zlar soni."""
    source = make_source(pack)
    spec = PAPERS.get(paper, PAPERS["katak"])
    geo = _geometry(spec)
    widths = []
    for ch in "aeiounrstlmkdbgyhqzxvjpcf":
        samples = source.get(ch)
        if samples:
            widths.append(np.mean([s[4] if len(s) > 3 else s[0].shape[1] / STD_XH for s in samples]))
    size = {"small": 0.88, "medium": 1.0, "large": 1.13}.get(pack.get("size", "medium"), 1.0)
    xh = spec["xh"] * PX_MM * size
    adv = (np.mean(widths) if widths else 0.75) * 0.82 + 0.09
    chars_per_line = (geo["right"] - geo["left"]) / (adv * xh)
    lines = int((geo["bottom"] - geo["first"]) / geo["pitch"]) + 1
    return max(40, int(lines * chars_per_line / 7.6))


def preview(pack: dict, text: str, ink: str = "auto") -> Image.Image:
    img = render(pack, "", [text], paper="chiziqli", ink=ink, seed=7, max_pages=1)[0]
    geo = _geometry(PAPERS["chiziqli"])
    arr = np.asarray(img)
    dark = np.nonzero((arr.min(axis=2) < 150).sum(axis=1) > 2)[0]
    bottom = int(min(arr.shape[0], (dark.max() if len(dark) else geo["first"]) + geo["pitch"]))
    return img.crop((0, max(0, int(geo["first"] - 2.2 * geo["pitch"])), arr.shape[1], bottom))


def save_outputs(pages: list[Image.Image], workdir: str, stem: str) -> tuple[list[str], str]:
    os.makedirs(workdir, exist_ok=True)
    jpgs = []
    for i, page in enumerate(pages, 1):
        path = os.path.join(workdir, f"{stem}_{i}.jpg")
        page.save(path, "JPEG", quality=90)
        jpgs.append(path)
    pdf = os.path.join(workdir, f"{stem}.pdf")
    pages[0].save(pdf, "PDF", resolution=PX_MM * 25.4, save_all=True, append_images=pages[1:])
    return jpgs, pdf
