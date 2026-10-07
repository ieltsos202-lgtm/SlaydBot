import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import config
import db

router = Router()
router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(config.ADMIN_IDS))
log = logging.getLogger(__name__)


def money(n: int) -> str:
    return f"{n:,}".replace(",", " ")


@router.message(Command("admin"))
async def admin_stats(message: Message):
    s = await db.stats()
    await message.answer(
        "📈 <b>Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{s['users']}</b> (bugun +{s['users_today']})\n"
        f"💎 To'lov qilganlar: <b>{s['paying_users']}</b>\n"
        f"📄 Tayyorlangan hujjatlar: <b>{s['orders']}</b> (bugun {s['orders_today']})\n"
        f"💰 Daromad: <b>{money(s['revenue'])} so'm</b> (bugun {money(s['revenue_today'])})\n"
        f"⏳ Kutilayotgan to'lovlar: <b>{s['pending']}</b>\n\n"
        "Buyruqlar:\n"
        "/add <code>user_id soni</code> — kredit qo'shish\n"
        "/broadcast — xabarga reply qilib yuboring, hammaga tarqatiladi"
    )


@router.message(Command("add"))
async def admin_add(message: Message, command: CommandObject, bot: Bot):
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].lstrip("-").isdigit():
        await message.answer("Format: /add user_id soni")
        return
    uid, amount = int(parts[0]), int(parts[1])
    if not await db.get_user(uid):
        await message.answer("Foydalanuvchi topilmadi.")
        return
    await db.add_credits(uid, amount)
    user = await db.get_user(uid)
    await message.answer(f"✅ {uid} balansi: {user['credits']}")
    if amount > 0:
        try:
            await bot.send_message(uid, f"🎁 Balansingizga {amount} ta hujjat qo'shildi! Balans: {user['credits']}")
        except Exception:
            pass


@router.message(Command("broadcast"))
async def admin_broadcast(message: Message, bot: Bot):
    src = message.reply_to_message
    if not src:
        await message.answer("Tarqatmoqchi bo'lgan xabarga reply qilib /broadcast yozing.")
        return
    ids = await db.all_user_ids()
    status = await message.answer(f"📤 Yuborilmoqda: 0/{len(ids)}")
    sent = 0
    for i, uid in enumerate(ids, 1):
        try:
            await bot.copy_message(uid, src.chat.id, src.message_id)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramForbiddenError:
            await db.set_blocked(uid, True)
        except Exception:
            log.exception("Broadcast failed for %s", uid)
        if i % 50 == 0:
            await status.edit_text(f"📤 Yuborilmoqda: {i}/{len(ids)}")
        await asyncio.sleep(0.05)
    await status.edit_text(f"✅ Yuborildi: {sent}/{len(ids)}")


@router.callback_query(F.data.startswith("pay_ok:"))
async def pay_ok(call: CallbackQuery, bot: Bot):
    pid = int(call.data.split(":")[1])
    payment, referrer = await db.approve_payment(pid)
    if not payment:
        await call.answer("Allaqachon ko'rib chiqilgan", show_alert=True)
        return
    user = await db.get_user(payment["user_id"])
    try:
        await bot.send_message(
            payment["user_id"],
            f"✅ To'lovingiz tasdiqlandi! +{payment['credits']} ta hujjat.\n💰 Balans: <b>{user['credits']}</b> ta")
    except Exception:
        pass
    if referrer:
        try:
            await bot.send_message(referrer, f"🎁 Do'stingiz to'lov qildi! Sizga {config.REFERRAL_BONUS} ta bepul hujjat qo'shildi.")
        except Exception:
            pass
    await call.message.edit_caption(caption=(call.message.caption or "") + "\n\n✅ TASDIQLANDI")
    await call.answer("Tasdiqlandi")


@router.callback_query(F.data.startswith("pay_no:"))
async def pay_no(call: CallbackQuery, bot: Bot):
    pid = int(call.data.split(":")[1])
    payment = await db.reject_payment(pid, "admin rad etdi")
    if not payment:
        await call.answer("Allaqachon ko'rib chiqilgan", show_alert=True)
        return
    try:
        await bot.send_message(
            payment["user_id"],
            "❌ To'lovingiz tasdiqlanmadi. Chek noto'g'ri yoki summa mos emas. "
            "Muammo bo'lsa adminga murojaat qiling.")
    except Exception:
        pass
    await call.message.edit_caption(caption=(call.message.caption or "") + "\n\n❌ RAD ETILDI")
    await call.answer("Rad etildi")
