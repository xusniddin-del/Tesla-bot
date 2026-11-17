# Tesla Savdo Boti

Telegram bot: avtomobillar katalogi, ro'yxatdan o'tish, buyurtma / bron / test-drayv, yangiliklar va chegirmalar, admin panel.

## Ishga tushirish
1. `pip install -r requirements.txt`
2. `.env.example` faylidan nusxa olib `.env` nomi bilan saqlang va to'ldiring (BOT_TOKEN, ADMIN_IDS ...).
3. `python tesla_bot.py`

Serverda (Railway/Render) `.env` o'rniga shu qiymatlarni **Variables** bo'limiga kiriting. Start buyrug'i: `python tesla_bot.py`.

## Muhim
- `.env` va `*.db` fayllarni GitHub'ga yuklamang (`.gitignore` ularni chiqarib tashlaydi).
- Admin panel: botda `/admin`.
