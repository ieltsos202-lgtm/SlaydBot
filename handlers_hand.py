import asyncio
import html
import io
import logging
import os
import shutil
import uuid

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, FSInputFile, InputMediaPhoto,
                           Message)

import config
import db
import docs
import gemini
import handwriting as hw
import keyboards as kb
import wiki
from handlers_user import balance_of, jobs, money

router = Router()
log = logging.getLogger(__name__)
pending: dict[int, dict] = {}

PREVIEW_TEXT = ("Bu mening yozuvim namunasi. Assalomu alaykum, do'stlar! "
                "O'zbekiston - go'zal yurt, 2026-yil. Bilim - kuch!")
SIZE_NAMES = {"small": "kichik", "medium": "o'rtacha", "large": "katta"}
MENU_TEXTS = {kb.BTN_PPTX, kb.BTN_DOCX, kb.BTN_HAND, kb.BTN_BALANCE, kb.BTN_TOPUP, kb.BTN_REF, kb.BTN_HELP}


class Hand(StatesGroup):
    method = State()
    style_photos = State()
    tpl_photo = State()
    confirm = State()
    topic = State()
    pages = State()
    lang = State()
    paper = State()
    ink = State()


@router.message(F.text == kb.BTN_CANCEL)
async def cancel(message: Message, state: FSMContext):
    pending.pop(message.from_user.id, None)
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=kb.main_menu)


@router.message(F.text == kb.BTN_HAND)
async def hand_start(message: Message, state: FSMContext):
    await state.clear()
    pending.pop(message.from_user.id, None)
    has_pack = await db.load_hand_pack(message.from_user.id) is not None
    await state.set_state(Hand.method)
    await message.answer(
        "✍️ <b>Yozma ish — sizning yozuvingizda</b>\n\n"
        "Men yozuvingizni klonlab, istalgan mavzuda yozma ishni daftar varag'iga "
        "<b>xuddi siz yozgandek</b> yozib beraman. Har bir harf har safar biroz boshqacha chiqadi — "
        "tirik qo'lyozmadek.\n\n"
        "⚡ <b>Tez usul</b> — daftaringizdagi istalgan yozuv rasmini yuborasiz, AI uslubingizni "
        "aniqlab, unga eng yaqin yozuvni moslaydi (o'xshash).\n"
        "🎯 <b>Aniq klon</b> — shablonni daftarga yozib, rasmini yuborasiz. Bot har bir harfingizning "
        "<b>haqiqiy shaklini</b> oladi (deyarli 100% sizning yozuvingiz).\n\n"
        "Yozuvingiz saqlanadi — keyingi safar qayta yuklash shart emas.",
        reply_markup=kb.cancel_menu,
    )
    await message.answer("Usulni tanlang 👇", reply_markup=kb.hand_method_kb(has_pack))


async def ask_topic(message: Message, state: FSMContext):
    await state.set_state(Hand.topic)
    await message.answer(
        "📝 Yozma ish mavzusini yozing.\n\n<i>Masalan: «Mening sevimli kasbim» yoki «Kuz fasli»</i>",
        reply_markup=kb.cancel_menu)


@router.callback_query(Hand.method, F.data == "hand:use")
async def use_saved(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.delete()
    await ask_topic(call.message, state)


@router.callback_query(Hand.method, F.data == "hand:style")
async def style_begin(call: CallbackQuery, state: FSMContext):
    await call.answer()
    pending[call.from_user.id] = {"photos": []}
    await state.set_state(Hand.style_photos)
    await call.message.edit_text(
        "📸 Daftaringizdagi <b>o'zingiz yozgan</b> matnning 1-3 ta rasmini yuboring.\n\n"
        "• Kamida 4-5 qator yozuv ko'rinsin\n"
        "• Tepadan, yorug' joyda, soyasiz suratga oling\n"
        "• Rasm tiniq bo'lsin (xira bo'lmasin)\n\n"
        "Yuborib bo'lgach «✅ Tayyor» ni bosing.")


async def _download(bot: Bot, message: Message) -> bytes | None:
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id = message.document.file_id
    else:
        return None
    buf = await bot.download(file_id)
    return buf.read()


@router.message(Hand.style_photos, F.photo | F.document)
async def style_photo(message: Message, state: FSMContext, bot: Bot):
    data = pending.setdefault(message.from_user.id, {"photos": []})
    raw = await _download(bot, message)
    if not raw:
        await message.answer("Iltimos, rasm yuboring 📸")
        return
    data["photos"].append(raw)
    n = len(data["photos"])
    if n >= 3:
        await analyze_style(message, state, message.from_user.id)
        return
    await message.answer(f"📸 {n}/3 rasm qabul qilindi. Yana yuborishingiz yoki tahlilni boshlashingiz mumkin.",
                         reply_markup=kb.hand_done_kb())


@router.callback_query(Hand.style_photos, F.data == "hand:done")
async def style_done(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await analyze_style(call.message, state, call.from_user.id)


async def analyze_style(message: Message, state: FSMContext, user_id: int):
    photos = pending.get(user_id, {}).get("photos") or []
    if not photos:
        await message.answer("Avval kamida 1 ta rasm yuboring 📸")
        return
    wait = await message.answer("🔍 Yozuvingiz tahlil qilinmoqda...")
    try:
        style = await gemini.analyze_handwriting(photos, hw.FONTS)
        ink = await asyncio.to_thread(hw.photo_ink, photos[0])
    except Exception:
        log.exception("Style analysis failed")
        await wait.edit_text("❗ Tahlil qilib bo'lmadi. Birozdan so'ng qayta urinib ko'ring yoki boshqa rasm yuboring.")
        pending[user_id] = {"photos": []}
        return
    if not style.get("is_handwriting", True):
        pending[user_id] = {"photos": []}
        await wait.edit_text("🤔 Rasmda qo'lyozma topilmadi. Daftaringizdagi yozuv rasmini qaytadan yuboring.")
        return
    pack = {"kind": "font", "font": style["font"], "slant": style["slant"], "size": style["size"],
            "weight": style["weight"], "spacing": style["spacing"], "ink": ink}
    pending[user_id] = {"pack": pack}
    desc = html.escape(str(style.get("description") or ""))
    await wait.delete()
    await send_preview(message, state, pack,
                       f"✨ Yozuvingiz tahlil qilindi!\n{desc}\n"
                       f"📐 Qiyalik: {style['slant']:.0f}°, o'lcham: {SIZE_NAMES[style['size']]}")


@router.callback_query(Hand.method, F.data == "hand:tpl")
async def tpl_begin(call: CallbackQuery, state: FSMContext):
    await call.answer()
    pending[call.from_user.id] = {"glyphs": {}, "ink": None}
    await call.message.edit_text(
        "🎯 <b>Aniq klon</b>\n\n"
        "Men sizga 2 ta (rus tili uchun +1 ixtiyoriy) qisqa shablon beraman. Ularni daftarga "
        "<b>o'z odatiy yozuvingizda</b> ko'chirib, rasmini yuborasiz.\n\n"
        "<b>Qoidalar:</b>\n"
        "• Har bir shablon qatorini daftarning alohida qatoriga yozing, qatorlar orasida <b>bitta qator tashlang</b>\n"
        "• Harflarni bir-biriga <b>ulamang</b>, orasida bo'sh joy qoldiring\n"
        "• Ko'k yoki qora ruchka, odatdagidek yozing\n"
        "• Tepadan, yorug' joyda, soyasiz suratga oling. Eng yaxshisi — 📎 <b>fayl</b> qilib yuborish")
    await send_template_page(call.message, state, 0)


async def send_template_page(message: Message, state: FSMContext, page: int):
    await state.set_state(Hand.tpl_photo)
    await state.update_data(tpl_page=page)
    info = hw.TEMPLATE_PAGES[page]
    await message.answer(
        f"📄 <b>{info['title']}</b>\n\nQuyidagini daftarga ko'chiring va rasmini yuboring:\n\n"
        f"<code>{html.escape(hw.template_text(page))}</code>",
        reply_markup=None if info["required"] else kb.hand_skip_kb())


@router.message(Hand.tpl_photo, F.photo | F.document)
async def tpl_photo(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    raw = await _download(bot, message)
    if not raw:
        await message.answer("Iltimos, rasm yuboring 📸")
        return
    page = (await state.get_data()).get("tpl_page", 0)
    wait = await message.answer("🔍 Harflaringiz ajratib olinmoqda...")
    try:
        glyphs, info = await asyncio.to_thread(hw.extract_template, raw, page)
    except hw.HandwritingError as e:
        await wait.edit_text(f"❗ {e}\n\nShu varaqni qaytadan suratga olib yuboring.")
        return
    except Exception:
        log.exception("Template extraction failed")
        await wait.edit_text("❗ Rasmni o'qib bo'lmadi. Qaytadan, tiniqroq suratga olib yuboring.")
        return
    expected = {c for line in hw.TEMPLATE_PAGES[page]["lines"] for c in line.split()}
    found = expected & set(glyphs)
    if len(found) < 0.7 * len(expected):
        await wait.edit_text(
            f"❗ Faqat {len(found)}/{len(expected)} ta belgi aniqlandi.\n\n"
            "Qatorlarni aniq ko'chirganingizni, harflar ulanmaganini va rasm tepadan, yorug'da "
            "olinganini tekshirib, shu varaqni qaytadan yuboring.")
        return
    data = pending.setdefault(user_id, {"glyphs": {}, "ink": None})
    for ch, samples in glyphs.items():
        data["glyphs"].setdefault(ch, []).extend(samples)
    data["ink"] = data["ink"] or info["ink"]
    missing = ", ".join(sorted(expected - found))
    await wait.edit_text(f"✅ {len(found)}/{len(expected)} ta belgi olindi."
                         + (f"\n<i>Topilmadi: {html.escape(missing)}</i>" if missing else ""))
    if page + 1 < len(hw.TEMPLATE_PAGES):
        await send_template_page(message, state, page + 1)
    else:
        await finish_template(message, state, user_id)


@router.callback_query(Hand.tpl_photo, F.data == "hand:skip")
async def tpl_skip(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await finish_template(call.message, state, call.from_user.id)


async def finish_template(message: Message, state: FSMContext, user_id: int):
    data = pending.get(user_id) or {}
    pack = {"kind": "template", "glyphs": data.get("glyphs", {}), "ink": data.get("ink") or hw.INKS["blue"],
            "fallback_font": "caveat"}
    pending[user_id] = {"pack": pack}
    await send_preview(message, state, pack, "✨ Yozuvingiz klonlandi!")


async def send_preview(message: Message, state: FSMContext, pack: dict, caption: str):
    try:
        img = await asyncio.to_thread(hw.preview, pack, PREVIEW_TEXT)
    except Exception:
        log.exception("Preview failed")
        await message.answer("❗ Namuna yaratib bo'lmadi. Qaytadan urinib ko'ring.", reply_markup=kb.main_menu)
        await state.clear()
        return
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    await state.set_state(Hand.confirm)
    await message.answer_photo(
        BufferedInputFile(buf.getvalue(), "namuna.jpg"),
        caption=f"{caption}\n\n👆 Yozuvingiz namunasi. Yoqdimi?",
        reply_markup=kb.hand_confirm_kb())


@router.callback_query(Hand.confirm, F.data == "hand:ok")
async def confirm_ok(call: CallbackQuery, state: FSMContext):
    pack = (pending.pop(call.from_user.id, None) or {}).get("pack")
    if not pack:
        await call.answer("Sessiya eskirgan, qaytadan boshlang", show_alert=True)
        await state.clear()
        return
    await hw.save_pack(call.from_user.id, pack)
    await call.answer("Saqlandi ✅")
    await call.message.edit_reply_markup(reply_markup=None)
    await ask_topic(call.message, state)


@router.callback_query(Hand.confirm, F.data == "hand:redo")
async def confirm_redo(call: CallbackQuery, state: FSMContext):
    pending.pop(call.from_user.id, None)
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await state.set_state(Hand.method)
    await call.message.answer("Usulni tanlang 👇",
                              reply_markup=kb.hand_method_kb(await db.load_hand_pack(call.from_user.id) is not None))


@router.message(Hand.topic, F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_TEXTS))
async def hand_topic(message: Message, state: FSMContext):
    topic = message.text.strip()
    if len(topic) < 3 or len(topic) > 300:
        await message.answer("Mavzu 3 dan 300 belgigacha bo'lishi kerak. Qaytadan yozing:")
        return
    await state.update_data(topic=topic)
    await state.set_state(Hand.pages)
    await message.answer("📏 Necha varaq bo'lsin?", reply_markup=kb.hand_pages_kb())


@router.callback_query(Hand.pages, F.data.startswith("hpages:"))
async def hand_pages(call: CallbackQuery, state: FSMContext):
    await state.update_data(pages=int(call.data.split(":")[1]))
    await state.set_state(Hand.lang)
    await call.message.edit_text("🌐 Qaysi tilda?", reply_markup=kb.lang_kb())
    await call.answer()


@router.callback_query(Hand.lang, F.data.startswith("lang:"))
async def hand_lang(call: CallbackQuery, state: FSMContext):
    await state.update_data(lang=call.data.split(":")[1])
    await state.set_state(Hand.paper)
    await call.message.edit_text("📄 Qanday varaqqa yozay?", reply_markup=kb.paper_kb())
    await call.answer()


@router.callback_query(Hand.paper, F.data.startswith("paper:"))
async def hand_paper(call: CallbackQuery, state: FSMContext):
    await state.update_data(paper=call.data.split(":")[1])
    await state.set_state(Hand.ink)
    await call.message.edit_text("🖊 Siyoh rangi:", reply_markup=kb.ink_kb())
    await call.answer()


@router.callback_query(Hand.ink, F.data.startswith("ink:"))
async def hand_ink(call: CallbackQuery, state: FSMContext, bot: Bot):
    await state.update_data(ink=call.data.split(":")[1])
    await call.answer()
    await call.message.delete()
    await generate(call.message, call.from_user.id, state, bot)


async def generate(message: Message, user_id: int, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    topic, pages, lang = data["topic"], data["pages"], data["lang"]
    paper, ink = data.get("paper", "katak"), data.get("ink", "auto")
    pack = await hw.load_pack(user_id)
    if not pack:
        await message.answer("Yozuvingiz topilmadi, qaytadan yuklang.", reply_markup=kb.main_menu)
        return

    unlimited = user_id in config.UNLIMITED_IDS
    if not unlimited and not await db.spend_credit(user_id):
        await message.answer(
            "😔 Balansingizda hujjat qolmagan.\n\nBalansni to'ldiring — 1 hujjat atigi "
            f"{money(min(config.PACKAGES.values()))} so'm.",
            reply_markup=kb.packages_kb())
        await message.answer("Paketni tanlang 👆", reply_markup=kb.main_menu)
        return

    wait = await message.answer("⏳ Navbatga qo'yildi...")

    async def stage(text: str):
        try:
            await wait.edit_text(text)
            await bot.send_chat_action(user_id, "upload_photo")
        except Exception:
            pass

    workdir = os.path.join(config.TMP_DIR, uuid.uuid4().hex)
    try:
        async with jobs:
            await stage("🔎 1/3 — Mavzu o'rganilmoqda...")
            source = await wiki.context(topic, lang)
            await stage("✍️ 2/3 — Yozma ish matni yozilmoqda...")
            per_page = await asyncio.to_thread(hw.capacity_words, pack, paper)
            content = await gemini.handwritten(topic, int(per_page * pages * 1.1), lang, source)
            await stage("🖊 3/3 — Sizning yozuvingizda daftarga yozilmoqda...")
            imgs = await asyncio.to_thread(hw.render, pack, content["title"], content["paragraphs"],
                                           paper, ink, None, pages + 1)
            jpgs, pdf = await asyncio.to_thread(hw.save_outputs, imgs, workdir, "yozma_ish")
        await bot.send_media_group(user_id, [InputMediaPhoto(media=FSInputFile(p)) for p in jpgs[:10]])
        user = await db.get_user(user_id)
        await bot.send_document(
            user_id, FSInputFile(pdf, filename=docs.safe_filename(topic, "pdf")),
            caption=f"✅ Tayyor! {len(jpgs)} varaq\n\n📌 {html.escape(topic)}\n"
                    f"💰 Qolgan balans: <b>{balance_of(user)}</b>",
            reply_markup=kb.main_menu)
        await db.log_order(user_id, "hand", topic, pages, lang, "done")
    except Exception:
        log.exception("Handwriting generation failed for %s", user_id)
        if not unlimited:
            await db.add_credits(user_id, 1)
        await db.log_order(user_id, "hand", topic, pages, lang, "failed")
        await message.answer("❗ Xatolik yuz berdi, kredit qaytarildi. Birozdan so'ng qayta urinib ko'ring.",
                             reply_markup=kb.main_menu)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        try:
            await wait.delete()
        except Exception:
            pass


@router.message(Hand.style_photos)
@router.message(Hand.tpl_photo)
async def need_photo(message: Message):
    await message.answer("Iltimos, yozuvingiz rasmini yuboring 📸 yoki «❌ Bekor qilish» ni bosing.")
