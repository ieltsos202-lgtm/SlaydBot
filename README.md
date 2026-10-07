# SlaydMaster — pullik AI taqdimot va referat boti

Telegram bot: foydalanuvchi mavzu yozadi → 1-2 daqiqada tayyor **PowerPoint taqdimot (.pptx)**
yoki **Word referat (.docx)** oladi. Har bir hujjat uchun pul to'laydi.

## Pul qanday tushadi
- Yangi foydalanuvchi 1 ta bepul hujjat oladi (sinab ko'radi).
- Keyin paket sotib oladi: 1 ta — 5 000, 3 ta — 12 000, 10 ta — 35 000 so'm (`.env` da o'zgartiriladi).
- To'lov to'g'ridan-to'g'ri **sizning kartangizga** o'tadi. Foydalanuvchi chek skrinshotini yuboradi,
  Gemini chekni tekshiradi (summa, karta oxirgi 4 raqami) → avtomatik kredit beriladi.
  Shubhali chek admin (siz)ga ✅/❌ tugmalari bilan keladi.
- Referal: do'sti birinchi to'lov qilganda taklif qilgan odamga bonus — bot o'zini o'zi tarqatadi.
- Xarajat: Gemini API (bepul limit katta), server — sizning kompyuteringiz yoki VPS.

## Ishga tushirish
1. @BotFather → `/newbot` → token oling.
2. https://aistudio.google.com/apikey → Gemini kalit oling.
3. `.env.example` ni `.env` ga nusxalang va to'ldiring (`BOT_TOKEN`, `GEMINI_API_KEY`, `ADMIN_IDS`, `CARD_NUMBER`, `CARD_OWNER`).
   Telegram ID ni @userinfobot dan bilib olasiz.
4. Ishga tushiring:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe main.py
   ```
   24/7 (yiqilsa o'zi qayta turadi): `powershell -ExecutionPolicy Bypass -File run_bot.ps1`

## Admin buyruqlari
- `/admin` — foydalanuvchilar, daromad, buyurtmalar statistikasi
- `/add user_id soni` — qo'lda kredit qo'shish
- `/broadcast` — biror xabarga reply qilib yozing, hamma foydalanuvchiga tarqatiladi

## Mijoz topish (birinchi pul uchun)
- Bot nomini SEO uchun qo'ying: masalan «Slayd | Taqdimot | Referat AI».
- Talabalar/maktab guruhlari va kanallarga reklama (eng yaxshi mavsum: sentyabr–dekabr, fevral–may).
- Tayyor taqdimot namunasini skrinshot qilib post qiling + bot havolasi.

## Testlar
```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
