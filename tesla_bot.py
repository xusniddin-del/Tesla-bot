"""
Tesla Savdo Boti (Telegram) — bitta fayl.

Imkoniyatlar:
  * Ro'yxatdan o'tish (ism + telefon)
  * Avtomobillar katalogi (narx, chegirma, xususiyatlar, rang, rasm)
  * Buyurtma berish / Bron qilish / Test-drayv
  * Chegirmalar va yangiliklar (admin qo'shadi, hammaga yuboriladi)
  * Mijoz "Buyurtmalarim" bo'limi
  * Admin panel: avto qo'shish/tahrirlash, chegirma %, buyurtmalar, xabar yuborish, statistika

Ishga tushirish:
  pip install aiogram
  export BOT_TOKEN="123456:ABC..."        # @BotFather'dan
  export ADMIN_IDS="123456789,987654321"  # admin Telegram ID lari (@userinfobot orqali bilib olinadi)
  python tesla_bot.py
"""
import asyncio
import html
import logging
import os
import re
import sqlite3
from contextlib import closing

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, KeyboardButton, Message,
                           ReplyKeyboardRemove)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ----------------------------------------------------------------- SOZLAMALAR
def _load_env():
    """Shu papkadagi .env faylidan sozlamalarni o'qiydi (qo'shimcha kutubxonasiz)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
DB_PATH = os.getenv("DB_PATH", "tesla_bot.db")
COMPANY = os.getenv("COMPANY_NAME", "Tesla Uzbekistan Dealer")
CONTACT_PHONE = os.getenv("CONTACT_PHONE", "+998 90 000 00 00")
CONTACT_ADDRESS = os.getenv("CONTACT_ADDRESS", "Toshkent shahri")
CONTACT_HOURS = os.getenv("CONTACT_HOURS", "Har kuni 09:00 – 19:00")

BTN_CARS = "🚗 Avtomobillar"
BTN_DISC = "🔥 Chegirmalar"
BTN_NEWS = "📰 Yangiliklar"
BTN_ORDERS = "📦 Buyurtmalarim"
BTN_CONTACT = "☎️ Aloqa"
BTN_ADMIN = "🛠 Admin panel"
MENU_BUTTONS = {BTN_CARS, BTN_DISC, BTN_NEWS, BTN_ORDERS, BTN_CONTACT, BTN_ADMIN}

KINDS = {"buy": "🛒 Sotib olish", "reserve": "🔒 Bron qilish", "test": "🚘 Test-drayv"}
STATUSES = {"new": "🆕 Yangi", "confirmed": "✅ Tasdiqlandi", "done": "🏁 Yakunlandi", "cancelled": "❌ Bekor qilindi"}
PAYMENTS = {"cash": "💵 Naqd", "credit": "🏦 Kredit", "lease": "📄 Lizing"}

# --------------------------------------------------------------------- BAZA
def db(sql, args=(), one=False, commit=False):
    with closing(sqlite3.connect(DB_PATH)) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute(sql, args)
        if commit:
            con.commit()
            return cur.lastrowid
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows


SAMPLE_CARS = [
    # name, price USD, range km, 0-100, top speed, colors, description, stock
    ("Tesla Model 3", 42000, 510, "6.1 s", 200, "Oq,Qora,Kulrang,Qizil", "Kompakt va tejamkor elektr sedan. Shahar va uzoq yo'l uchun ideal.", 5),
    ("Tesla Model Y", 48000, 530, "5.9 s", 217, "Oq,Qora,Kulrang,Ko'k", "Oilaviy elektr krossover: keng salon va katta yuk bo'limi.", 5),
    ("Tesla Model S", 90000, 650, "3.2 s", 250, "Oq,Qora,Kulrang", "Flagman elektr sedan: yuqori tezlik va hashamat.", 2),
    ("Tesla Model X", 100000, 560, "3.9 s", 250, "Oq,Qora,Kulrang", "Falcon-wing eshikli premium elektr SUV, 7 o'rinli.", 2),
]


def init_db():
    db("""CREATE TABLE IF NOT EXISTS users(
        tg_id INTEGER PRIMARY KEY, name TEXT, phone TEXT, username TEXT,
        created TEXT DEFAULT CURRENT_TIMESTAMP)""", commit=True)
    db("""CREATE TABLE IF NOT EXISTS cars(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER, range_km INTEGER,
        accel TEXT, top_speed INTEGER, colors TEXT, descr TEXT, photo TEXT,
        stock INTEGER DEFAULT 1, discount INTEGER DEFAULT 0, active INTEGER DEFAULT 1)""", commit=True)
    db("""CREATE TABLE IF NOT EXISTS posts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, title TEXT, body TEXT, photo TEXT,
        created TEXT DEFAULT CURRENT_TIMESTAMP)""", commit=True)
    db("""CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, car_id INTEGER, car_name TEXT,
        kind TEXT, color TEXT, payment TEXT, city TEXT, price INTEGER,
        status TEXT DEFAULT 'new', created TEXT DEFAULT CURRENT_TIMESTAMP)""", commit=True)
    if not db("SELECT 1 FROM cars LIMIT 1", one=True):
        for c in SAMPLE_CARS:
            db("INSERT INTO cars(name,price,range_km,accel,top_speed,colors,descr,stock) VALUES(?,?,?,?,?,?,?,?)",
               c, commit=True)


# ------------------------------------------------------------------ YORDAMCHI
def e(x):
    return html.escape(str(x or ""))


def money(x):
    return f"${int(x):,}".replace(",", " ")


def final_price(c):
    return round(c["price"] * (100 - (c["discount"] or 0)) / 100)


def car_text(c):
    if c["discount"]:
        price = f"<s>{money(c['price'])}</s> → <b>{money(final_price(c))}</b> (−{c['discount']}% 🔥)"
    else:
        price = f"<b>{money(c['price'])}</b>"
    stock = f"✅ Omborda: {c['stock']} ta" if c["stock"] > 0 else "⏳ Buyurtma asosida (muddati menejer bilan kelishiladi)"
    return (f"🚗 <b>{e(c['name'])}</b>\n\n💰 Narx: {price}\n"
            f"🔋 Yurish masofasi: ~{c['range_km']} km\n⚡️ 0–100 km/soat: {e(c['accel'])}\n"
            f"🏎 Maks. tezlik: {c['top_speed']} km/soat\n🎨 Ranglar: {e(c['colors'].replace(',', ', '))}\n\n"
            f"{e(c['descr'])}\n\n{stock}")


def post_text(p):
    icon = "🔥" if p["kind"] == "discount" else "📰"
    return f"{icon} <b>{e(p['title'])}</b>\n\n{e(p['body'])}"


def main_menu(uid):
    kb = ReplyKeyboardBuilder()
    for b in (BTN_CARS, BTN_DISC, BTN_NEWS, BTN_ORDERS, BTN_CONTACT):
        kb.button(text=b)
    if uid in ADMIN_IDS:
        kb.button(text=BTN_ADMIN)
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)


async def send_post(bot, chat_id, text, photo=None, markup=None):
    if photo:
        await bot.send_photo(chat_id, photo, caption=text, reply_markup=markup)
    else:
        await bot.send_message(chat_id, text, reply_markup=markup)


async def broadcast(bot, text, photo=None):
    ok = 0
    for u in db("SELECT tg_id FROM users"):
        try:
            await send_post(bot, u["tg_id"], text, photo)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    return ok


# ------------------------------------------------------------------- HOLATLAR
class Reg(StatesGroup):
    name = State()
    phone = State()


class Order(StatesGroup):
    color = State()
    payment = State()
    city = State()
    confirm = State()


class AddCar(StatesGroup):
    name = State(); price = State(); range_km = State(); accel = State()
    top_speed = State(); colors = State(); descr = State(); stock = State(); photo = State()


class EditCar(StatesGroup):
    value = State()


class AddPost(StatesGroup):
    title = State(); body = State(); photo = State(); confirm = State()


class Mass(StatesGroup):
    text = State()


menu_r, user_r, admin_r = Router(), Router(), Router()
admin_r.message.filter(F.from_user.id.in_(ADMIN_IDS))
admin_r.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))


# ================================================================ RO'YXATDAN O'TISH
async def start_registration(msg: Message, state: FSMContext):
    await state.set_state(Reg.name)
    await msg.answer(f"👋 Assalomu alaykum! <b>{e(COMPANY)}</b> botiga xush kelibsiz!\n\n"
                     "Boshlash uchun ro'yxatdan o'ting.\n✍️ Ism-familiyangizni yozing:",
                     reply_markup=ReplyKeyboardRemove())


@menu_r.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    if db("SELECT 1 FROM users WHERE tg_id=?", (msg.from_user.id,), one=True):
        await msg.answer("🏠 Asosiy menyu", reply_markup=main_menu(msg.from_user.id))
    else:
        await start_registration(msg, state)


@user_r.message(Reg.name, F.text)
async def reg_name(msg: Message, state: FSMContext):
    if len(msg.text.strip()) < 3:
        return await msg.answer("Iltimos, to'liq ism-familiyangizni yozing.")
    await state.update_data(name=msg.text.strip())
    await state.set_state(Reg.phone)
    kb = ReplyKeyboardBuilder()
    kb.add(KeyboardButton(text="📱 Raqamni yuborish", request_contact=True))
    await msg.answer("📞 Telefon raqamingizni yuboring (tugma orqali yoki +998901234567 ko'rinishida yozing):",
                     reply_markup=kb.as_markup(resize_keyboard=True, one_time_keyboard=True))


@user_r.message(Reg.phone)
async def reg_phone(msg: Message, state: FSMContext):
    phone = msg.contact.phone_number if msg.contact else (msg.text or "").strip()
    phone = re.sub(r"[^\d+]", "", phone)
    if len(re.sub(r"\D", "", phone)) < 9:
        return await msg.answer("❗️ Raqam noto'g'ri. Masalan: +998901234567")
    if not phone.startswith("+"):
        phone = "+" + phone
    data = await state.get_data()
    db("INSERT OR REPLACE INTO users(tg_id,name,phone,username) VALUES(?,?,?,?)",
       (msg.from_user.id, data["name"], phone, msg.from_user.username or ""), commit=True)
    await state.clear()
    await msg.answer("✅ Ro'yxatdan o'tdingiz! Marhamat, tanlang 👇", reply_markup=main_menu(msg.from_user.id))


async def need_reg(msg: Message, state: FSMContext) -> bool:
    if db("SELECT 1 FROM users WHERE tg_id=?", (msg.from_user.id,), one=True):
        return False
    await start_registration(msg, state)
    return True


# ======================================================================= MENYU
@menu_r.message(F.text == BTN_CARS)
async def show_cars(msg: Message, state: FSMContext):
    await state.clear()
    if await need_reg(msg, state):
        return
    cars = db("SELECT * FROM cars WHERE active=1")
    if not cars:
        return await msg.answer("Hozircha avtomobillar yo'q.")
    kb = InlineKeyboardBuilder()
    for c in cars:
        tag = " 🔥" if c["discount"] else ""
        kb.button(text=f"{c['name']} — {money(final_price(c))}{tag}", callback_data=f"car:{c['id']}")
    kb.adjust(1)
    await msg.answer("🚗 <b>Avtomobilni tanlang:</b>", reply_markup=kb.as_markup())


@menu_r.message(F.text.in_({BTN_DISC, BTN_NEWS}))
async def show_posts(msg: Message, state: FSMContext):
    await state.clear()
    if await need_reg(msg, state):
        return
    kind = "discount" if msg.text == BTN_DISC else "news"
    posts = db("SELECT * FROM posts WHERE kind=? ORDER BY id DESC LIMIT 5", (kind,))
    cars_disc = db("SELECT * FROM cars WHERE active=1 AND discount>0") if kind == "discount" else []
    if not posts and not cars_disc:
        return await msg.answer("Hozircha e'lon yo'q. Yangiliklarni kuzatib boring! 🔔")
    for c in cars_disc:
        await send_post(msg.bot, msg.chat.id, "🔥 <b>Chegirma!</b>\n\n" + car_text(c), c["photo"])
    for p in posts:
        await send_post(msg.bot, msg.chat.id, post_text(p), p["photo"])


@menu_r.message(F.text == BTN_ORDERS)
async def my_orders(msg: Message, state: FSMContext):
    await state.clear()
    if await need_reg(msg, state):
        return
    rows = db("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (msg.from_user.id,))
    if not rows:
        return await msg.answer("Sizda hali buyurtma yo'q. «🚗 Avtomobillar» bo'limidan tanlang.")
    out = []
    for o in rows:
        out.append(f"<b>#{o['id']}</b> • {e(o['car_name'])}\n{KINDS[o['kind']]} • {e(o['color'])}\n"
                   f"Holat: {STATUSES[o['status']]}")
    await msg.answer("📦 <b>Buyurtmalaringiz:</b>\n\n" + "\n\n".join(out))


@menu_r.message(F.text == BTN_CONTACT)
async def contact(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(f"☎️ <b>{e(COMPANY)}</b>\n\n📞 {e(CONTACT_PHONE)}\n📍 {e(CONTACT_ADDRESS)}\n🕒 {e(CONTACT_HOURS)}")


# ============================================================ AVTO KARTOCHKASI
@user_r.callback_query(F.data.startswith("car:"))
async def car_card(cb: CallbackQuery):
    c = db("SELECT * FROM cars WHERE id=? AND active=1", (int(cb.data[4:]),), one=True)
    if not c:
        return await cb.answer("Avtomobil topilmadi", show_alert=True)
    kb = InlineKeyboardBuilder()
    kb.button(text=KINDS["buy"], callback_data=f"ord:buy:{c['id']}")
    kb.button(text=KINDS["reserve"], callback_data=f"ord:reserve:{c['id']}")
    kb.button(text=KINDS["test"], callback_data=f"ord:test:{c['id']}")
    kb.adjust(1)
    await send_post(cb.bot, cb.message.chat.id, car_text(c), c["photo"], kb.as_markup())
    await cb.answer()


# =============================================================== BUYURTMA OQIMI
@user_r.callback_query(F.data.startswith("ord:"))
async def order_start(cb: CallbackQuery, state: FSMContext):
    _, kind, car_id = cb.data.split(":")
    c = db("SELECT * FROM cars WHERE id=? AND active=1", (int(car_id),), one=True)
    if not c:
        return await cb.answer("Avtomobil topilmadi", show_alert=True)
    if not db("SELECT 1 FROM users WHERE tg_id=?", (cb.from_user.id,), one=True):
        await cb.answer()
        return await start_registration(cb.message, state)
    colors = [x.strip() for x in c["colors"].split(",") if x.strip()]
    await state.set_state(Order.color)
    await state.update_data(car_id=c["id"], kind=kind, colors=colors)
    kb = InlineKeyboardBuilder()
    for i, col in enumerate(colors):
        kb.button(text=col, callback_data=f"col:{i}")
    kb.button(text="✖️ Bekor qilish", callback_data="ocancel")
    kb.adjust(2)
    await cb.message.answer(f"{KINDS[kind]} — <b>{e(c['name'])}</b>\n\n🎨 Rangni tanlang:", reply_markup=kb.as_markup())
    await cb.answer()


@user_r.callback_query(F.data == "ocancel")
async def order_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer("Bekor qilindi.", reply_markup=main_menu(cb.from_user.id))
    await cb.answer()


@user_r.callback_query(Order.color, F.data.startswith("col:"))
async def order_color(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    color = d["colors"][int(cb.data[4:])]
    await state.update_data(color=color)
    await cb.answer()
    if d["kind"] == "test":
        await state.update_data(payment="-")
        await state.set_state(Order.city)
        return await cb.message.answer("📍 Qaysi shahar/tumandasiz?")
    await state.set_state(Order.payment)
    kb = InlineKeyboardBuilder()
    for k, v in PAYMENTS.items():
        kb.button(text=v, callback_data=f"pay:{k}")
    kb.adjust(1)
    await cb.message.answer("💳 To'lov usulini tanlang:", reply_markup=kb.as_markup())


@user_r.callback_query(Order.payment, F.data.startswith("pay:"))
async def order_pay(cb: CallbackQuery, state: FSMContext):
    await state.update_data(payment=cb.data[4:])
    await state.set_state(Order.city)
    await cb.message.answer("📍 Qaysi shahar/tumandasiz?")
    await cb.answer()


@user_r.message(Order.city, F.text)
async def order_city(msg: Message, state: FSMContext):
    await state.update_data(city=msg.text.strip())
    d = await state.get_data()
    c = db("SELECT * FROM cars WHERE id=?", (d["car_id"],), one=True)
    pay = PAYMENTS.get(d["payment"], "—")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tasdiqlash", callback_data="oconfirm")
    kb.button(text="✖️ Bekor qilish", callback_data="ocancel")
    kb.adjust(2)
    await state.set_state(Order.confirm)
    await msg.answer(f"📝 <b>Buyurtmani tekshiring:</b>\n\n🚗 {e(c['name'])}\n{KINDS[d['kind']]}\n🎨 {e(d['color'])}\n"
                     f"💳 {pay}\n📍 {e(d['city'])}\n💰 Narx: <b>{money(final_price(c))}</b>",
                     reply_markup=kb.as_markup())


@user_r.callback_query(Order.confirm, F.data == "oconfirm")
async def order_confirm(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    c = db("SELECT * FROM cars WHERE id=?", (d["car_id"],), one=True)
    u = db("SELECT * FROM users WHERE tg_id=?", (cb.from_user.id,), one=True)
    oid = db("INSERT INTO orders(user_id,car_id,car_name,kind,color,payment,city,price) VALUES(?,?,?,?,?,?,?,?)",
             (cb.from_user.id, c["id"], c["name"], d["kind"], d["color"], d["payment"], d["city"], final_price(c)),
             commit=True)
    await state.clear()
    await cb.message.answer(f"🎉 Buyurtmangiz qabul qilindi! Raqami: <b>#{oid}</b>\n"
                            "Menejerimiz tez orada siz bilan bog'lanadi.", reply_markup=main_menu(cb.from_user.id))
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tasdiqlash", callback_data=f"st:{oid}:confirmed")
    kb.button(text="❌ Bekor", callback_data=f"st:{oid}:cancelled")
    text = (f"🆕 <b>Yangi buyurtma #{oid}</b>\n{KINDS[d['kind']]}\n🚗 {e(c['name'])} • {e(d['color'])}\n"
            f"💳 {PAYMENTS.get(d['payment'], '—')} • 💰 {money(final_price(c))}\n📍 {e(d['city'])}\n\n"
            f"👤 {e(u['name'])}\n📞 {e(u['phone'])}\n"
            f"{'@' + e(u['username']) if u['username'] else ''}")
    for a in ADMIN_IDS:
        try:
            await cb.bot.send_message(a, text, reply_markup=kb.as_markup())
        except Exception:
            logging.warning("Adminga xabar yuborilmadi: %s", a)
    await cb.answer()


# ================================================================== ADMIN PANEL
def admin_menu():
    kb = InlineKeyboardBuilder()
    for t, d in [("➕ Avto qo'shish", "a:addcar"), ("🚗 Avtolarni boshqarish", "a:cars"),
                 ("🔥 Chegirma e'loni", "a:post:discount"), ("📰 Yangilik e'loni", "a:post:news"),
                 ("📦 Yangi buyurtmalar", "a:orders"), ("📣 Hammaga xabar", "a:mass"),
                 ("📊 Statistika", "a:stats")]:
        kb.button(text=t, callback_data=d)
    kb.adjust(2)
    return kb.as_markup()


@menu_r.message(F.text == BTN_ADMIN)
@menu_r.message(Command("admin"))
async def admin_open(msg: Message, state: FSMContext):
    await state.clear()
    if msg.from_user.id not in ADMIN_IDS:
        return
    await msg.answer("🛠 <b>Admin panel</b>", reply_markup=admin_menu())


@admin_r.callback_query(F.data == "a:stats")
async def a_stats(cb: CallbackQuery):
    def n(q):
        return db(q, one=True)[0]
    new_q = "SELECT COUNT(*) FROM orders WHERE status='new'"
    conf_q = "SELECT COUNT(*) FROM orders WHERE status='confirmed'"
    await cb.message.answer(
        f"📊 <b>Statistika</b>\n\n👥 Mijozlar: {n('SELECT COUNT(*) FROM users')}\n"
        f"📦 Buyurtmalar: {n('SELECT COUNT(*) FROM orders')}\n🆕 Yangi: {n(new_q)}\n"
        f"✅ Tasdiqlangan: {n(conf_q)}\n"
        f"🚗 Faol avtolar: {n('SELECT COUNT(*) FROM cars WHERE active=1')}")
    await cb.answer()


# ---- buyurtmalar
@admin_r.callback_query(F.data == "a:orders")
async def a_orders(cb: CallbackQuery):
    rows = db("SELECT o.*, u.name uname, u.phone FROM orders o LEFT JOIN users u ON u.tg_id=o.user_id "
              "WHERE o.status IN ('new','confirmed') ORDER BY o.id DESC LIMIT 10")
    if not rows:
        await cb.message.answer("Faol buyurtma yo'q.")
    for o in rows:
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Tasdiq", callback_data=f"st:{o['id']}:confirmed")
        kb.button(text="🏁 Yakun", callback_data=f"st:{o['id']}:done")
        kb.button(text="❌ Bekor", callback_data=f"st:{o['id']}:cancelled")
        await cb.message.answer(
            f"<b>#{o['id']}</b> {STATUSES[o['status']]}\n{KINDS[o['kind']]} • {e(o['car_name'])} • {e(o['color'])}\n"
            f"💰 {money(o['price'])} • 📍 {e(o['city'])}\n👤 {e(o['uname'])} {e(o['phone'])}",
            reply_markup=kb.as_markup())
    await cb.answer()


@admin_r.callback_query(F.data.startswith("st:"))
async def a_status(cb: CallbackQuery):
    _, oid, st = cb.data.split(":")
    o = db("SELECT * FROM orders WHERE id=?", (int(oid),), one=True)
    if not o:
        return await cb.answer("Topilmadi", show_alert=True)
    db("UPDATE orders SET status=? WHERE id=?", (st, o["id"]), commit=True)
    try:
        await cb.bot.send_message(o["user_id"], f"📦 Buyurtma <b>#{o['id']}</b> ({e(o['car_name'])}): {STATUSES[st]}")
    except Exception:
        pass
    await cb.answer(f"#{oid}: {STATUSES[st]}", show_alert=True)


# ---- avto qo'shish
ADD_STEPS = [("name", "🚗 Avto nomi (masalan: Tesla Model 3):", AddCar.name),
             ("price", "💰 Narxi (USD, faqat raqam):", AddCar.price),
             ("range_km", "🔋 Yurish masofasi (km):", AddCar.range_km),
             ("accel", "⚡️ 0–100 km/soat (masalan: 5.9 s):", AddCar.accel),
             ("top_speed", "🏎 Maks. tezlik (km/soat):", AddCar.top_speed),
             ("colors", "🎨 Ranglar vergul bilan (Oq,Qora,Qizil):", AddCar.colors),
             ("descr", "📝 Qisqa tavsif:", AddCar.descr),
             ("stock", "📦 Omborda nechta (0 = buyurtma asosida):", AddCar.stock),
             ("photo", "🖼 Rasm yuboring (o'tkazish uchun /skip):", AddCar.photo)]
INT_FIELDS = {"price", "range_km", "top_speed", "stock"}


@admin_r.callback_query(F.data == "a:addcar")
async def a_addcar(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AddCar.name)
    await cb.message.answer(ADD_STEPS[0][1])
    await cb.answer()


@admin_r.message(F.text == "/cancel")
async def a_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Bekor qilindi.", reply_markup=main_menu(msg.from_user.id))


@admin_r.message(AddCar.name)
@admin_r.message(AddCar.price)
@admin_r.message(AddCar.range_km)
@admin_r.message(AddCar.accel)
@admin_r.message(AddCar.top_speed)
@admin_r.message(AddCar.colors)
@admin_r.message(AddCar.descr)
@admin_r.message(AddCar.stock)
@admin_r.message(AddCar.photo)
async def a_addcar_step(msg: Message, state: FSMContext):
    cur = (await state.get_state()).split(":")[1]
    idx = next(i for i, s in enumerate(ADD_STEPS) if s[0] == cur)
    if cur == "photo":
        photo = msg.photo[-1].file_id if msg.photo else None
        if not photo and (msg.text or "").strip() != "/skip":
            return await msg.answer("Rasm yuboring yoki /skip yozing.")
        d = await state.get_data()
        db("INSERT INTO cars(name,price,range_km,accel,top_speed,colors,descr,stock,photo) VALUES(?,?,?,?,?,?,?,?,?)",
           (d["name"], d["price"], d["range_km"], d["accel"], d["top_speed"], d["colors"], d["descr"], d["stock"], photo),
           commit=True)
        await state.clear()
        return await msg.answer(f"✅ «{e(d['name'])}» qo'shildi.", reply_markup=main_menu(msg.from_user.id))
    val = (msg.text or "").strip()
    if cur in INT_FIELDS:
        if not val.isdigit():
            return await msg.answer("❗️ Faqat raqam yozing.")
        val = int(val)
    await state.update_data(**{cur: val})
    await state.set_state(ADD_STEPS[idx + 1][2])
    await msg.answer(ADD_STEPS[idx + 1][1])


# ---- avtolarni boshqarish
@admin_r.callback_query(F.data == "a:cars")
async def a_cars(cb: CallbackQuery):
    kb = InlineKeyboardBuilder()
    for c in db("SELECT * FROM cars ORDER BY id"):
        kb.button(text=f"{'🟢' if c['active'] else '⚪️'} {c['name']}", callback_data=f"ac:{c['id']}")
    kb.adjust(1)
    await cb.message.answer("Avtoni tanlang:", reply_markup=kb.as_markup())
    await cb.answer()


@admin_r.callback_query(F.data.startswith("ac:"))
async def a_car(cb: CallbackQuery):
    c = db("SELECT * FROM cars WHERE id=?", (int(cb.data[3:]),), one=True)
    if not c:
        return await cb.answer("Topilmadi", show_alert=True)
    kb = InlineKeyboardBuilder()
    kb.button(text="💰 Narx", callback_data=f"ae:price:{c['id']}")
    kb.button(text="🏷 Chegirma %", callback_data=f"ae:discount:{c['id']}")
    kb.button(text="📦 Soni", callback_data=f"ae:stock:{c['id']}")
    kb.button(text="🖼 Rasm", callback_data=f"ae:photo:{c['id']}")
    kb.button(text="👁 Ko'rsatish/yashirish", callback_data=f"at:{c['id']}")
    kb.button(text="🗑 O'chirish", callback_data=f"ad:{c['id']}")
    kb.adjust(2)
    await send_post(cb.bot, cb.message.chat.id, car_text(c), c["photo"], kb.as_markup())
    await cb.answer()


@admin_r.callback_query(F.data.startswith("at:"))
async def a_toggle(cb: CallbackQuery):
    db("UPDATE cars SET active=1-active WHERE id=?", (int(cb.data[3:]),), commit=True)
    await cb.answer("Holat o'zgardi", show_alert=True)


@admin_r.callback_query(F.data.startswith("ad:"))
async def a_delete(cb: CallbackQuery):
    db("DELETE FROM cars WHERE id=?", (int(cb.data[3:]),), commit=True)
    await cb.answer("O'chirildi", show_alert=True)


@admin_r.callback_query(F.data.startswith("ae:"))
async def a_edit(cb: CallbackQuery, state: FSMContext):
    _, field, cid = cb.data.split(":")
    await state.set_state(EditCar.value)
    await state.update_data(field=field, cid=int(cid))
    ask = {"price": "Yangi narx (USD):", "discount": "Chegirma foizi (0–90, 0 = olib tashlash):",
           "stock": "Omborda nechta:", "photo": "Yangi rasmni yuboring:"}[field]
    await cb.message.answer(ask + "\n(/cancel — bekor qilish)")
    await cb.answer()


@admin_r.message(EditCar.value)
async def a_edit_value(msg: Message, state: FSMContext):
    d = await state.get_data()
    f = d["field"]
    if f == "photo":
        if not msg.photo:
            return await msg.answer("Rasm yuboring.")
        val = msg.photo[-1].file_id
    else:
        t = (msg.text or "").strip()
        if not t.isdigit() or (f == "discount" and int(t) > 90):
            return await msg.answer("❗️ To'g'ri raqam yozing.")
        val = int(t)
    db(f"UPDATE cars SET {f}=? WHERE id=?", (val, d["cid"]), commit=True)  # f — faqat yuqoridagi ro'yxatdan
    await state.clear()
    await msg.answer("✅ Saqlandi.")
    if f == "discount" and val > 0:
        kb = InlineKeyboardBuilder()
        kb.button(text="📣 Hammaga e'lon qilish", callback_data=f"adisc:{d['cid']}")
        await msg.answer("Chegirmani mijozlarga yuboraymi?", reply_markup=kb.as_markup())


@admin_r.callback_query(F.data.startswith("adisc:"))
async def a_disc_broadcast(cb: CallbackQuery):
    c = db("SELECT * FROM cars WHERE id=?", (int(cb.data[6:]),), one=True)
    await cb.answer("Yuborilmoqda…")
    n = await broadcast(cb.bot, "🔥 <b>Chegirma!</b>\n\n" + car_text(c), c["photo"])
    await cb.message.answer(f"📣 {n} ta mijozga yuborildi.")


# ---- yangilik / chegirma e'loni
@admin_r.callback_query(F.data.startswith("a:post:"))
async def a_post(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AddPost.title)
    await state.update_data(kind=cb.data.split(":")[2])
    await cb.message.answer("Sarlavha yozing:\n(/cancel — bekor qilish)")
    await cb.answer()


@admin_r.message(AddPost.title, F.text)
async def a_post_title(msg: Message, state: FSMContext):
    await state.update_data(title=msg.text.strip())
    await state.set_state(AddPost.body)
    await msg.answer("Matnni yozing:")


@admin_r.message(AddPost.body, F.text)
async def a_post_body(msg: Message, state: FSMContext):
    await state.update_data(body=msg.text.strip())
    await state.set_state(AddPost.photo)
    await msg.answer("Rasm yuboring (o'tkazish uchun /skip):")


@admin_r.message(AddPost.photo)
async def a_post_photo(msg: Message, state: FSMContext):
    photo = msg.photo[-1].file_id if msg.photo else None
    if not photo and (msg.text or "").strip() != "/skip":
        return await msg.answer("Rasm yuboring yoki /skip yozing.")
    await state.update_data(photo=photo)
    await state.set_state(AddPost.confirm)
    d = await state.get_data()
    kb = InlineKeyboardBuilder()
    kb.button(text="📣 Saqlash va hammaga yuborish", callback_data="pp:send")
    kb.button(text="💾 Faqat saqlash", callback_data="pp:save")
    kb.adjust(1)
    await send_post(msg.bot, msg.chat.id, post_text(d), photo, kb.as_markup())


@admin_r.callback_query(AddPost.confirm, F.data.startswith("pp:"))
async def a_post_done(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    db("INSERT INTO posts(kind,title,body,photo) VALUES(?,?,?,?)", (d["kind"], d["title"], d["body"], d["photo"]), commit=True)
    await state.clear()
    await cb.answer()
    if cb.data == "pp:send":
        n = await broadcast(cb.bot, post_text(d), d["photo"])
        await cb.message.answer(f"✅ Saqlandi va {n} ta mijozga yuborildi.")
    else:
        await cb.message.answer("✅ Saqlandi.")


# ---- ommaviy xabar
@admin_r.callback_query(F.data == "a:mass")
async def a_mass(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Mass.text)
    await cb.message.answer("Barcha mijozlarga yuboriladigan matnni yozing:\n(/cancel — bekor qilish)")
    await cb.answer()


@admin_r.message(Mass.text, F.text)
async def a_mass_send(msg: Message, state: FSMContext):
    await state.clear()
    n = await broadcast(msg.bot, e(msg.text))
    await msg.answer(f"📣 {n} ta mijozga yuborildi.")


# ===================================================================== ISHGA TUSHIRISH
async def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan. @BotFather'dan token oling va: export BOT_TOKEN=...")
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS bo'sh — admin panel hech kimga ochilmaydi!")
    logging.basicConfig(level=logging.INFO)
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(menu_r, admin_r, user_r)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
