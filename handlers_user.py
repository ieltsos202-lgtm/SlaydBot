import asyncio
import html
import logging
import os
import shutil
import uuid

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

import config
import db
import docs
import gemini
import images
import keyboards as kb
import slides
import wiki

router = Router()
log = logging.getLogger(__name__)
jobs = asyncio.Semaphore(config.MAX_PARALLEL_JOBS)


class Order(StatesGroup):
    topic = State()
    size = State()
    lang = State()
    theme = State()


class Pay(StatesGroup):
    receipt = State()


def money(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def balance_of(user: dict) -> str:
    return "♾ Cheksiz" if user["id"] in config.UNLIMITED_IDS else f"{user['credits']} ta"


def name_of(user) -> str:
    return html.escape(user.full_name or str(user.id))


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    ref = None
    if command.args and command.args.startswith("ref_") and command.args[4:].isdigit():
        ref = int(command.args[4:])
    user, is_new = await db.get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.full_name, ref)
    gift = (f"\n\n🎁 Sizga <b>{config.FREE_CREDITS} ta bepul</b> hujjat sovg'a qilindi!"
            if is_new and config.FREE_CREDITS else "")
    await message.answer(
        f"Assalomu alaykum, <b>{name_of(message.from_user)}</b>! 👋\n\n"
        "Men sun'iy intellekt yordamida <b>1-2 daqiqada</b> tayyorlab beraman:\n"
        "📊 <b>Taqdimot</b> (PowerPoint, .pptx) — 8-15 slayd, chiroyli dizayn\n"
        "📄 <b>Referat</b> (Word, .docx) — titul varag'i, reja, kirish, xulosa, adabiyotlar\n"
        "✍️ <b>Yozma ish</b> — <b>sizning qo'lyozmangizda</b>, daftar varag'iga yozilgan holda\n\n"
        "🇺🇿 O'zbek, 🇷🇺 rus va 🇬🇧 ingliz tillarida."
        f"{gift}\n\nBalansingiz: <b>{balance_of(user)}</b> hujjat.",
        reply_markup=kb.main_menu,
    )


@router.message(F.text == kb.BTN_CANCEL)
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=kb.main_menu)


@router.message(F.text == kb.BTN_HELP)
async def help_(message: Message):
    prices = "\n".join(f"• {c} ta hujjat — {money(p)} so'm" for c, p in sorted(config.PACKAGES.items()))
    await message.answer(
        "ℹ️ <b>Qanday ishlaydi?</b>\n\n"
        "1. «Taqdimot», «Referat» yoki «Yozma ish» ni bosing\n"
        "2. Mavzuni yozing\n"
        "3. Hajm, til (va dizayn) ni tanlang\n"
        "4. 1-2 daqiqada tayyor faylni olasiz\n\n"
        f"💵 <b>Narxlar</b> (1 hujjat = 1 kredit):\n{prices}\n\n"
        "Savol bo'lsa adminga yozing.",
        reply_markup=kb.main_menu,
    )


@router.message(F.text == kb.BTN_BALANCE)
async def balance(message: Message):
    user, _ = await db.get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer(
        f"💰 Balansingiz: <b>{balance_of(user)}</b> hujjat\n"
        f"🆔 ID: <code>{user['id']}</code>",
        reply_markup=kb.main_menu,
    )


@router.message(F.text == kb.BTN_REF)
async def referral(message: Message, bot: Bot):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    count = await db.referral_count(message.from_user.id)
    await message.answer(
        "🎁 <b>Do'stlaringizni taklif qiling!</b>\n\n"
        f"Taklif qilgan do'stingiz birinchi marta balans to'ldirganda sizga "
        f"<b>{config.REFERRAL_BONUS} ta bepul</b> hujjat beriladi.\n\n"
        f"Sizning havolangiz:\n{link}\n\n"
        f"Taklif qilganlaringiz: <b>{count}</b> ta",
        disable_web_page_preview=True,
    )


@router.message(F.text.in_({kb.BTN_PPTX, kb.BTN_DOCX}))
async def order_start(message: Message, state: FSMContext):
    kind = "pptx" if message.text == kb.BTN_PPTX else "docx"
    await state.clear()
    await state.update_data(kind=kind)
    await state.set_state(Order.topic)
    what = "taqdimot" if kind == "pptx" else "referat"
    await message.answer(
        f"✍️ {what.capitalize()} mavzusini yozing.\n\n"
        "<i>Masalan: «Amir Temur davlatining boshqaruv tizimi»</i>",
        reply_markup=kb.cancel_menu,
    )


@router.message(Order.topic, F.text)
async def order_topic(message: Message, state: FSMContext):
    topic = message.text.strip()
    if len(topic) < 3 or len(topic) > 300:
        await message.answer("Mavzu 3 dan 300 belgigacha bo'lishi kerak. Qaytadan yozing:")
        return
    await state.update_data(topic=topic)
    data = await state.get_data()
    await state.set_state(Order.size)
    if data["kind"] == "pptx":
        await message.answer("📏 Nechta slayd bo'lsin?", reply_markup=kb.slides_kb())
    else:
        await message.answer("📏 Necha bet bo'lsin?", reply_markup=kb.pages_kb())


@router.callback_query(Order.size, F.data.startswith("size:"))
async def order_size(call: CallbackQuery, state: FSMContext):
    await state.update_data(size=int(call.data.split(":")[1]))
    await state.set_state(Order.lang)
    await call.message.edit_text("🌐 Qaysi tilda?", reply_markup=kb.lang_kb())
    await call.answer()


@router.callback_query(Order.lang, F.data.startswith("lang:"))
async def order_lang(call: CallbackQuery, state: FSMContext, bot: Bot):
    await state.update_data(lang=call.data.split(":")[1])
    data = await state.get_data()
    if data["kind"] == "pptx":
        await state.set_state(Order.theme)
        await call.message.edit_text("🎨 Dizaynni tanlang:", reply_markup=kb.theme_kb())
        await call.answer()
        return
    await call.answer()
    await call.message.delete()
    await generate(call.message, call.from_user.id, state, bot)


@router.callback_query(Order.theme, F.data.startswith("theme:"))
async def order_theme(call: CallbackQuery, state: FSMContext, bot: Bot):
    await state.update_data(theme=call.data.split(":")[1])
    await call.answer()
    await call.message.delete()
    await generate(call.message, call.from_user.id, state, bot)


async def generate(message: Message, user_id: int, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    kind, topic, size, lang = data["kind"], data["topic"], data["size"], data["lang"]

    unlimited = user_id in config.UNLIMITED_IDS
    if not unlimited and not await db.spend_credit(user_id):
        await message.answer(
            "😔 Balansingizda hujjat qolmagan.\n\nBalansni to'ldiring — 1 hujjat atigi "
            f"{money(min(config.PACKAGES.values()))} so'm.",
            reply_markup=kb.packages_kb(),
        )
        await message.answer("Paketni tanlang 👆", reply_markup=kb.main_menu)
        return

    wait = await message.answer("⏳ Navbatga qo'yildi...")

    async def stage(text: str):
        try:
            await wait.edit_text(text)
            await bot.send_chat_action(user_id, "upload_document")
        except Exception:
            pass

    workdir = os.path.join(config.TMP_DIR, uuid.uuid4().hex)
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, f"result.{kind}")
    try:
        async with jobs:
            await stage("🔎 1/3 — Mavzu bo'yicha ishonchli manbalar o'rganilmoqda...")
            source = await wiki.context(topic, lang)
            await stage("✍️ 2/3 — Mutaxassis darajasida matn yozilmoqda...")
            if kind == "pptx":
                content = await gemini.presentation(topic, size, lang, source)
                await stage("🎨 3/3 — Rasmlar tanlanib, dizayn yig'ilmoqda...")
                pics = await images.fetch(slides.image_queries(content, topic), workdir,
                                          str(content.get("image_query") or ""))
                await asyncio.to_thread(slides.build_pptx, content, data.get("theme", "ocean"), lang, path, pics)
            else:
                content = await gemini.referat(topic, size, lang, source)
                await stage("📐 3/3 — Hujjat rasmiylashtirilmoqda...")
                await asyncio.to_thread(docs.build_docx, content, lang, path)
        user = await db.get_user(user_id)
        await bot.send_document(
            user_id,
            FSInputFile(path, filename=docs.safe_filename(topic, kind)),
            caption=f"✅ Tayyor!\n\n📌 {html.escape(topic)}\n💰 Qolgan balans: <b>{balance_of(user)}</b>",
            reply_markup=kb.main_menu,
        )
        await db.log_order(user_id, kind, topic, size, lang, "done")
    except Exception:
        log.exception("Generation failed for %s", user_id)
        if not unlimited:
            await db.add_credits(user_id, 1)
        await db.log_order(user_id, kind, topic, size, lang, "failed")
        await message.answer("❗ Xatolik yuz berdi, kredit qaytarildi. Birozdan so'ng qayta urinib ko'ring.",
                             reply_markup=kb.main_menu)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        try:
            await wait.delete()
        except Exception:
            pass


@router.message(F.text == kb.BTN_TOPUP)
async def topup(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("💳 Paketni tanlang:", reply_markup=kb.packages_kb())


@router.callback_query(F.data.startswith("pack:"))
async def pack_chosen(call: CallbackQuery, state: FSMContext):
    credits = int(call.data.split(":")[1])
    price = config.PACKAGES.get(credits)
    if not price:
        await call.answer("Paket topilmadi", show_alert=True)
        return
    await state.set_state(Pay.receipt)
    await state.update_data(credits=credits, price=price)
    await call.message.answer(
        f"📦 Paket: <b>{credits} ta hujjat</b>\n"
        f"💵 To'lov: <b>{money(price)} so'm</b>\n\n"
        f"Quyidagi kartaga o'tkazing:\n<code>{config.CARD_NUMBER}</code>\n"
        f"👤 {html.escape(config.CARD_OWNER)}\n\n"
        "To'lovdan so'ng <b>chek skrinshotini</b> shu yerga yuboring 📸",
        reply_markup=kb.cancel_menu,
    )
    await call.answer()


async def notify_admins(bot: Bot, file_id: str, text: str, payment_id: int | None = None):
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_photo(admin_id, file_id, caption=text,
                                 reply_markup=kb.admin_payment_kb(payment_id) if payment_id else None)
        except Exception:
            log.exception("Admin notify failed: %s", admin_id)


def package_for_amount(amount: int | None) -> tuple[int, int] | None:
    fits = [(c, p) for c, p in config.PACKAGES.items() if amount is not None and p <= amount]
    return max(fits, key=lambda cp: cp[1]) if fits else None


@router.message(Pay.receipt, F.photo | F.document)
@router.message(StateFilter(None), F.photo)
async def receipt(message: Message, state: FSMContext, bot: Bot):
    chosen = await state.get_state() == Pay.receipt.state
    if message.photo:
        tg_file = message.photo[-1]
        mime = "image/jpeg"
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        tg_file = message.document
        mime = message.document.mime_type
    else:
        await message.answer("Iltimos, chekni rasm ko'rinishida yuboring 📸")
        return

    data = await state.get_data()
    if chosen:
        credits, price = data["credits"], data["price"]
    else:
        credits, price = min(config.PACKAGES.items(), key=lambda cp: cp[1])
    await state.clear()
    checking = await message.answer("🔎 Chek tekshirilmoqda...", reply_markup=kb.main_menu)

    if await db.receipt_used(tg_file.file_unique_id, None):
        await checking.edit_text("❗ Bu chek avval yuborilgan.")
        return

    result: dict = {}
    try:
        buf = await bot.download(tg_file.file_id)
        result = await gemini.verify_receipt(buf.read(), mime, price, config.CARD_NUMBER, config.CARD_OWNER)
    except Exception as e:
        log.exception("Receipt check failed")
        result = {"error": f"{type(e).__name__}: {e}"[:200]}

    if not chosen:
        if "is_receipt" in result and not result["is_receipt"]:
            await checking.edit_text("Quyidagi menyudan tanlang 👇")
            return
        credits, price = package_for_amount(result.get("amount")) or (credits, price)

    tx = result.get("transaction_id")
    if tx and await db.receipt_used(tg_file.file_unique_id, str(tx)):
        await checking.edit_text("❗ Bu tranzaksiya avval ishlatilgan.")
        return

    note = f"AI: {result.get('reason', 'tekshirilmadi')} | summa={result.get('amount')} | ishonch={result.get('confidence')}"
    if result.get("failed"):
        note += f" | o'tmadi: {', '.join(result['failed'])}"
    if result.get("error"):
        note += f" | XATO: {result['error']}"
    payment_id = await db.create_payment(
        message.from_user.id, price, credits, tg_file.file_id, tg_file.file_unique_id,
        str(tx) if tx else None, note)
    who = f"{name_of(message.from_user)} (<code>{message.from_user.id}</code>)"
    info = f"💳 To'lov #{payment_id}\n👤 {who}\n📦 {credits} ta — {money(price)} so'm\n🤖 {html.escape(note)}"

    if config.AUTO_APPROVE and result.get("ok"):
        payment, referrer = await db.approve_payment(payment_id)
        user = await db.get_user(message.from_user.id)
        await checking.edit_text(
            f"✅ To'lov tasdiqlandi! Balansingizga <b>{credits}</b> ta hujjat qo'shildi.\n"
            f"💰 Balans: <b>{user['credits']}</b> ta")
        await notify_admins(bot, tg_file.file_id, "✅ AVTO-TASDIQLANDI\n" + info)
        if referrer:
            try:
                await bot.send_message(referrer, f"🎁 Do'stingiz to'lov qildi! Sizga {config.REFERRAL_BONUS} ta bepul hujjat qo'shildi.")
            except Exception:
                pass
        return

    await checking.edit_text(
        "🕐 Chek admin tomonidan tekshirilmoqda. Tasdiqlangach sizga xabar keladi (odatda 5-30 daqiqa).")
    await notify_admins(bot, tg_file.file_id, "⏳ TEKSHIRISH KERAK\n" + info, payment_id)


@router.message(Pay.receipt)
async def receipt_wrong(message: Message):
    await message.answer("Iltimos, to'lov chekining rasmini yuboring 📸 yoki «Bekor qilish» ni bosing.")


@router.message(StateFilter(None))
async def fallback(message: Message):
    await message.answer("Quyidagi menyudan tanlang 👇", reply_markup=kb.main_menu)
