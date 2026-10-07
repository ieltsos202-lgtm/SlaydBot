from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardMarkup)

import config
from handwriting import INKS, PAPERS
from slides import THEMES

BTN_PPTX = "📊 Taqdimot yaratish"
BTN_DOCX = "📄 Referat yaratish"
BTN_HAND = "✍️ Yozma ish (mening yozuvimda)"
BTN_BALANCE = "💰 Balans"
BTN_TOPUP = "💳 Balansni to'ldirish"
BTN_REF = "🎁 Do'st taklif qilish"
BTN_HELP = "ℹ️ Yordam"
BTN_CANCEL = "❌ Bekor qilish"

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_PPTX), KeyboardButton(text=BTN_DOCX)],
        [KeyboardButton(text=BTN_HAND)],
        [KeyboardButton(text=BTN_BALANCE), KeyboardButton(text=BTN_TOPUP)],
        [KeyboardButton(text=BTN_REF), KeyboardButton(text=BTN_HELP)],
    ],
    resize_keyboard=True,
)

cancel_menu = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True)


def _rows(buttons: list[InlineKeyboardButton], per_row: int) -> InlineKeyboardMarkup:
    rows = [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def slides_kb() -> InlineKeyboardMarkup:
    return _rows([InlineKeyboardButton(text=f"{n} slayd", callback_data=f"size:{n}")
                  for n in (8, 10, 12, 15)], 2)


def pages_kb() -> InlineKeyboardMarkup:
    return _rows([InlineKeyboardButton(text=f"{n} bet", callback_data=f"size:{n}")
                  for n in (5, 10, 15)], 3)


def lang_kb() -> InlineKeyboardMarkup:
    return _rows([
        InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data="lang:uz"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en"),
    ], 3)


def theme_kb() -> InlineKeyboardMarkup:
    return _rows([InlineKeyboardButton(text=t["name"], callback_data=f"theme:{k}")
                  for k, t in THEMES.items()], 2)


def packages_kb() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"{c} ta hujjat — {p:,} so'm".replace(",", " "),
                             callback_data=f"pack:{c}")
        for c, p in sorted(config.PACKAGES.items())
    ]
    return _rows(buttons, 1)


def hand_method_kb(has_pack: bool) -> InlineKeyboardMarkup:
    buttons = []
    if has_pack:
        buttons.append(InlineKeyboardButton(text="✅ Saqlangan yozuvim bilan yozish", callback_data="hand:use"))
    buttons += [
        InlineKeyboardButton(text="⚡ Tez: daftarimdagi yozuv rasmi", callback_data="hand:style"),
        InlineKeyboardButton(text="🎯 Aniq klon: shablon bo'yicha", callback_data="hand:tpl"),
    ]
    return _rows(buttons, 1)


def hand_done_kb() -> InlineKeyboardMarkup:
    return _rows([InlineKeyboardButton(text="✅ Tayyor, tahlil qil", callback_data="hand:done")], 1)


def hand_skip_kb() -> InlineKeyboardMarkup:
    return _rows([InlineKeyboardButton(text="⏭ O'tkazib yuborish", callback_data="hand:skip")], 1)


def hand_confirm_kb() -> InlineKeyboardMarkup:
    return _rows([
        InlineKeyboardButton(text="✅ Yoqdi, davom etish", callback_data="hand:ok"),
        InlineKeyboardButton(text="🔄 Qaytadan", callback_data="hand:redo"),
    ], 2)


def hand_pages_kb() -> InlineKeyboardMarkup:
    return _rows([InlineKeyboardButton(text=f"{n} varaq", callback_data=f"hpages:{n}")
                  for n in (1, 2, 3, 4)], 4)


def paper_kb() -> InlineKeyboardMarkup:
    return _rows([InlineKeyboardButton(text=p["name"], callback_data=f"paper:{k}")
                  for k, p in PAPERS.items()], 1)


INK_NAMES = {"auto": "🖊 O'z siyohim rangi", "blue": "🔵 Ko'k", "black": "⚫ Qora", "violet": "🟣 Binafsha"}


def ink_kb() -> InlineKeyboardMarkup:
    return _rows([InlineKeyboardButton(text=INK_NAMES[k], callback_data=f"ink:{k}") for k in INKS], 2)


def admin_payment_kb(payment_id: int) -> InlineKeyboardMarkup:
    return _rows([
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"pay_ok:{payment_id}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"pay_no:{payment_id}"),
    ], 2)
