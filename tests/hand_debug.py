"""Shablon o'qilishini tekshirish.

python tests/hand_debug.py              -> sun'iy holatlar
python tests/hand_debug.py rasm.jpg [0] -> haqiqiy rasm (varaq raqami 0/1/2)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import handwriting as hw  # noqa: E402
from test_handwriting import fake_template_photo as fake  # noqa: E402

PENCIL = (105, 105, 110)
CASES = {
    "blue pen": {},
    "pencil": {"ink": PENCIL},
    "pencil+vignette": {"ink": PENCIL, "vignette": True},
    "pencil+vig+tg1280": {"ink": PENCIL, "vignette": True, "max_side": 1280},
    "pencil+vig+tg+dark grid": {"ink": PENCIL, "grid": (140, 170, 205), "vignette": True, "max_side": 1280},
    "tight step2 pencil tg": {"ink": PENCIL, "step": 2, "vignette": True, "max_side": 1280},
    "cursive pencil tg": {"font_file": "MarckScript.ttf", "ink": PENCIL, "vignette": True, "max_side": 1280},
    "warp pencil tg": {"ink": PENCIL, "vignette": True, "max_side": 1280, "warp": True},
    "warp pencil thick grid tg": {"ink": PENCIL, "grid": (120, 150, 200), "grid_width": 4, "vignette": True,
                                  "max_side": 1280, "warp": True},
    "warp blue pen thick grid tg": {"grid": (120, 150, 200), "grid_width": 4, "vignette": True,
                                    "max_side": 1280, "warp": True},
}


def report(name: str, data: bytes, page: int) -> None:
    expected = {c for line in hw.TEMPLATE_PAGES[page]["lines"] for c in line.split()}
    try:
        g, info = hw.extract_template(data, page)
        print(f"{name:28s} {len(set(g) & expected)}/{len(expected)}  rows={info['rows']} bad={info['bad']}"
              f"  missing={''.join(info['missing'])}")
    except Exception:
        import traceback
        print(f"{name:28s} ERROR")
        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg in CASES:
            report(arg, fake(0, **CASES[arg]), 0)
        else:
            report(os.path.basename(arg), open(arg, "rb").read(),
                   int(sys.argv[2]) if len(sys.argv) > 2 else 0)
    else:
        for name, kw in CASES.items():
            report(name, fake(0, **kw), 0)
