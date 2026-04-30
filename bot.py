import os
import threading
import logging
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice
)
from aiogram.filters import Command
import asyncio
import aiohttp

from supabase import create_client   # 👈 ВОТ ЭТО ДОБАВЬ
# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------
# HTTP HEALTH CHECK SERVER
# -----------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK")
    
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
    
    def log_message(self, format, *args):
        pass


def start_health_check_server():
    """Запускает HTTP сервер для health check"""
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Health check server started on port {port}")
    server.serve_forever()


# Запускаем HTTP сервер в отдельном потоке
threading.Thread(target=start_health_check_server, daemon=True).start()

# -----------------------------
# CONFIG
# -----------------------------
TOKEN = os.getenv("BOT_TOKEN")
CRYPTO_API_TOKEN = os.getenv("CRYPTO_API_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# -----------------------------
# SUPABASE CONFIG (1 раз при запуске)
# -----------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# -----------------------------
# STATE с автоочисткой
# -----------------------------
USER_STATE: dict[int, dict] = {}
USER_TIMESTAMPS: dict[int, float] = {}


async def cleanup_old_states():
    """Очищает состояния пользователей старше 1 часа"""
    while True:
        try:
            await asyncio.sleep(300)  # Проверяем каждые 5 минут
            now = time.time()
            expired = [uid for uid, ts in USER_TIMESTAMPS.items() if now - ts > 3600]
            
            if expired:
                for uid in expired:
                    USER_STATE.pop(uid, None)
                    USER_TIMESTAMPS.pop(uid, None)
                logger.info(f"Cleaned up {len(expired)} old user states")
        except Exception as e:
            logger.error(f"Error in cleanup_old_states: {e}")


def update_user_timestamp(user_id: int):
    """Обновляет время последней активности пользователя"""
    USER_TIMESTAMPS[user_id] = time.time()


# -----------------------------
# CRYPTO BOT API
# -----------------------------
CRYPTO_API_URL = "https://pay.crypt.bot/api"


async def create_invoice(amount_usdt: float, user_id: int):
    url = f"{CRYPTO_API_URL}/createInvoice"
    headers = {
        "Crypto-Pay-API-Token": CRYPTO_API_TOKEN
    }
    data = {
        "asset": "USDT",
        "amount": amount_usdt,
        "description": f"eSIM для user {user_id}"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                return await resp.json()
    except Exception as e:
        logger.error(f"Failed to create invoice: {e}")
        return {"ok": False, "error": str(e)}


async def check_invoice(invoice_id: int):
    url = f"{CRYPTO_API_URL}/getInvoices"
    headers = {
        "Crypto-Pay-API-Token": CRYPTO_API_TOKEN
    }
    params = {
        "invoice_ids": str(invoice_id)
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                return await resp.json()
    except Exception as e:
        logger.error(f"Failed to check invoice: {e}")
        return {"ok": False, "error": str(e)}


async def wait_for_payment(user_id: int, invoice_id: int):
    for attempt in range(60):  # ~5 минут
        await asyncio.sleep(5)

        result = await check_invoice(invoice_id)

        if not result.get("ok"):
            logger.warning(f"CHECK ERROR for invoice {invoice_id}: {result}")
            continue

        invoices = result.get("result", {}).get("items", [])
        if not invoices:
            continue

        status = invoices[0].get("status")
        logger.info(f"Invoice {invoice_id} status: {status}")

        if status == "paid":
            try:
                await bot.send_message(
                    user_id,
                    "✅ Оплата получена!\n\n📦 Готовим твою eSIM..."
                )
            except Exception as e:
                logger.error(f"Failed to send payment notification: {e}")
            
            USER_STATE.get(user_id, {}).pop("invoice_id", None)
            return

        if status == "expired":
            try:
                await bot.send_message(
                    user_id,
                    "⌛ Счёт истёк. Создай новый при необходимости"
                )
            except Exception as e:
                logger.error(f"Failed to send expiry notification: {e}")
            
            USER_STATE.get(user_id, {}).pop("invoice_id", None)
            return

# -----------------------------
# КУРС ВАЛЮТ
# -----------------------------
USD_RATE = {"value": 90.0}


async def update_usd_rate():
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://www.cbr-xml-daily.ru/daily_json.js"
                ) as resp:
                    data = await resp.json(content_type=None)
                    rate = float(data["Valute"]["USD"]["Value"])
                    if rate > 0:
                        USD_RATE["value"] = rate
                        logger.info(f"USD rate updated: {rate}")
        except Exception as e:
            logger.warning(f"Failed to update USD rate: {e}")
        
        await asyncio.sleep(3600)


def cents_to_usd(cents: int) -> str:
    return f"${cents / 100:.2f}"


def cents_to_rub(cents: int) -> int:
    rate = USD_RATE.get("value", 90.0)
    return round(cents / 100 * rate)


def cents_to_usdt(cents: int) -> str:
    return f"{cents / 100:.2f} USDT"

# -----------------------------
# DATA — обычные страны
# -----------------------------
COUNTRIES = {
    "🇹🇷 Турция": [
        ("2 GB", "7 дней", "без ограничений", 159),
        ("6 GB", "15 дней", "без ограничений", 283),
        ("10 GB", "15 дней", "без ограничений", 449),
        ("Безлимит 1 день", "1 день", "10GB/день → 0.5 Mbps", 299),
        ("Безлимит 3 дня", "3 дня", "5GB/день → 0.5 Mbps", 698),
        ("Безлимит 7 дней", "7 дней", "5GB/день → 0.5 Mbps", 1299),
    ],
    "🇦🇪 ОАЭ": [
        ("1 GB", "7 дней", "без ограничений", 229),
        ("3 GB", "15 дней", "без ограничений", 553),
        ("8 GB", "30 дней", "без ограничений", 1459),
        ("Безлимит 1 день", "1 день", "3GB/день → 0.384 Mbps", 449),
        ("Безлимит 3 дня", "3 дня", "3GB/день → 0.384 Mbps", 1289),
        ("Безлимит 7 дней", "7 дней", "2GB/день → 0.384 Mbps", 2499),
    ],
    "🇪🇬 Египет": [
        ("1 GB", "7 дней", "без ограничений", 179),
        ("5 GB", "15 дней", "без ограничений", 419),
        ("10 GB", "30 дней", "без ограничений", 819),
        ("Безлимит 1 день", "1 день", "2GB/день → 0.5 Mbps", 399),
        ("Безлимит 3 дня", "3 дня", "2GB/день → 0.5 Mbps", 899),
        ("Безлимит 7 дней", "7 дней", "1GB/день → 0.5 Mbps", 1999),
    ],
    "🇹🇭 Таиланд": [
        ("5 GB", "10 дней", "без ограничений", 800),
        ("15 GB", "30 дней", "без ограничений", 1800),
    ],
    "🇨🇳 Китай": [
        ("3 GB", "7 дней", "без ограничений", 600),
        ("10 GB", "30 дней", "без ограничений", 1500),
    ],
    "🇻🇳 Вьетнам": [
        ("3 GB", "7 дней", "без ограничений", 500),
        ("10 GB", "30 дней", "без ограничений", 1200),
    ],
    "🇮🇩 Индонезия (Бали)": [
        ("5 GB", "10 дней", "без ограничений", 900),
        ("15 GB", "30 дней", "без ограничений", 2200),
    ],
    "🇯🇵 Япония": [
        ("5 GB", "10 дней", "без ограничений", 1200),
        ("15 GB", "30 дней", "без ограничений", 2800),
    ],
}

#------------------------
# ФОТО СТРАН 
#-------------------------

COUNTRY_MEDIA = {
    "🇹🇷 Турция": "https://rbipbflfopqnpwcykwil.supabase.co/storage/v1/object/public/Country%20photo/blog_41_240228092633_a-quick-guide-to-fethiye-turkey.jpg",
    "🇦🇪 ОАЭ": "https://rbipbflfopqnpwcykwil.supabase.co/storage/v1/object/public/Country%20photo/uae-la-gi-gom-nhung-nuoc-nao-va-cac-diem-1649926781.jpg",
    "🇹🇭 Таиланд": "https://rbipbflfopqnpwcykwil.supabase.co/storage/v1/object/public/Country%20photo/thailand-tour-package-1.jpg",
    "🇪🇬 Египет": "https://rbipbflfopqnpwcykwil.supabase.co/storage/v1/object/public/Country%20photo/Egypt-Holiday-Packages-Adeli-Kenya-Safaris-best-Africa-sustainable-safari-tour-company-in-Kenya.jpg",
}


# -----------------------------
# DATA — бандлы
# -----------------------------
BUNDLES = {
    "🌏 Трип по Азии": [
        ("5 GB", "15 дней", "Таиланд, Вьетнам, Индонезия", 1490),
        ("10 GB", "30 дней", "Таиланд, Вьетнам, Индонезия, Япония", 2490),
        ("20 GB", "30 дней", "Вся Азия (10 стран)", 3990),
    ],
    "🌍 Трип по Европе": [
        ("5 GB", "15 дней", "Германия, Франция, Италия", 1890),
        ("10 GB", "30 дней", "Германия, Франция, Италия, Испания", 2990),
        ("20 GB", "30 дней", "Вся Европа (30 стран)", 4990),
    ],
}

# -----------------------------
# DATA — страны по запросу
# -----------------------------
CUSTOM_COUNTRIES = [
    "🇦🇺 Австралия", "🇦🇹 Австрия", "🇦🇿 Азербайджан", "🇦🇱 Албания", "🇩🇿 Алжир",
    "🇦🇷 Аргентина", "🇦🇲 Армения", "🇧🇩 Бангладеш", "🇧🇭 Бахрейн", "🇧🇪 Бельгия",
    "🇧🇬 Болгария", "🇧🇴 Боливия", "🇧🇦 Босния", "🇧🇷 Бразилия", "🇬🇧 Великобритания",
    "🇭🇺 Венгрия", "🇻🇪 Венесуэла", "🇬🇭 Гана", "🇩🇪 Германия", "🇭🇰 Гонконг",
    "🇬🇷 Греция", "🇬🇪 Грузия", "🇩🇰 Дания", "🇮🇱 Израиль", "🇮🇳 Индия",
    "🇮🇩 Индонезия", "🇯🇴 Иордания", "🇮🇶 Ирак", "🇮🇷 Иран", "🇮🇪 Ирландия",
    "🇮🇸 Исландия", "🇪🇸 Испания", "🇮🇹 Италия", "🇾🇪 Йемен", "🇰🇿 Казахстан",
    "🇰🇭 Камбоджа", "🇨🇲 Камерун", "🇨🇦 Канада", "🇶🇦 Катар", "🇰🇪 Кения",
    "🇨🇾 Кипр", "🇨🇴 Колумбия", "🇰🇷 Корея", "🇨🇷 Коста-Рика", "🇨🇮 Кот-д'Ивуар",
    "🇰🇼 Кувейт", "🇱🇦 Лаос", "🇱🇻 Латвия", "🇱🇹 Литва", "🇲🇺 Маврикий",
    "🇲🇴 Макао", "🇲🇰 Македония", "🇲🇾 Малайзия", "🇲🇻 Мальдивы", "🇲🇹 Мальта",
    "🇲🇦 Марокко", "🇲🇽 Мексика", "🇲🇿 Мозамбик", "🇲🇲 Мьянма", "🇳🇵 Непал",
    "🇳🇬 Нигерия", "🇳🇱 Нидерланды", "🇳🇿 Новая Зеландия", "🇳🇴 Норвегия", "🇴🇲 Оман",
    "🇵🇰 Пакистан", "🇵🇦 Панама", "🇵🇬 Папуа Новая Гвинея", "🇵🇾 Парагвай", "🇵🇪 Перу",
    "🇵🇱 Польша", "🇵🇹 Португалия", "🇷🇴 Румыния", "🇺🇸 США", "🇸🇦 Саудовская Аравия",
    "🇸🇳 Сенегал", "🇷🇸 Сербия", "🇸🇬 Сингапур", "🇸🇰 Словакия", "🇸🇮 Словения",
    "🇹🇼 Тайвань", "🇹🇿 Танзания", "🇹🇳 Тунис", "🇺🇬 Уганда", "🇺🇿 Узбекистан",
    "🇺🇾 Уругвай", "🇫🇯 Фиджи", "🇵🇭 Филиппины", "🇫🇮 Финляндия", "🇫🇷 Франция",
    "🇭🇷 Хорватия", "🇲🇪 Черногория", "🇨🇿 Чехия", "🇨🇱 Чили", "🇨🇭 Швейцария",
    "🇸🇪 Швеция", "🇱🇰 Шри-Ланка", "🇪🇨 Эквадор", "🇪🇹 Эфиопия", "🇿🇦 ЮАР",
]

# -----------------------------
# KEYBOARDS
# -----------------------------
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌍 Купить eSIM")],
        [KeyboardButton(text="💸 Цены"), KeyboardButton(text="🛠 Поддержка")]
    ],
    resize_keyboard=True
)

payment_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💳 Перевод СБП")],
        [KeyboardButton(text="💰 USDT (сеть TRC20)")],
        [KeyboardButton(text="🏠 Главное меню")],
        [KeyboardButton(text="⬅️ Назад")]
    ],
    resize_keyboard=True
)


def countries_kb() -> ReplyKeyboardMarkup:
    country_list = list(COUNTRIES.keys())
    bundle_list = list(BUNDLES.keys())
    all_items = country_list + bundle_list

    rows = []
    for i in range(0, len(all_items), 2):
        pair = all_items[i:i+2]
        rows.append([KeyboardButton(text=c) for c in pair])

    rows.append([KeyboardButton(text="🔍 Страна по запросу")])
    rows.append([KeyboardButton(text="⬅️ Назад")])

    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def plans_kb(country_name: str, is_bundle: bool = False) -> InlineKeyboardMarkup:
    plans = BUNDLES[country_name] if is_bundle else COUNTRIES[country_name]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{p[0]} — {cents_to_rub(p[3])} ₽ ({cents_to_usd(p[3])})",
                callback_data=f"plan_{i}"
            )]
            for i, p in enumerate(plans)
        ]
    )


def custom_countries_kb() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(CUSTOM_COUNTRIES), 2):
        pair = CUSTOM_COUNTRIES[i:i+2]
        rows.append([
            InlineKeyboardButton(text=c, callback_data=f"custom_{i+j}")
            for j, c in enumerate(pair)
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# -----------------------------
# START
# -----------------------------
@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.chat.id
    update_user_timestamp(user_id)

    # очищаем локальное состояние
    USER_STATE.pop(user_id, None)

    # сохраняем пользователя в Supabase
    try:
        supabase.table("users").upsert({
            "user_id": user_id,
            "username": message.from_user.username or "unknown"
        }).execute()
    except Exception as e:
        # чтобы бот не падал если Supabase временно недоступен
        print(f"Supabase error: {e}")

    await message.answer(
        "👋 На связи! Это eSIM для поездок 🌍\n\n"
        "🌐 Интернет заграницей\n"
        "⚡ Подключение за пару минут\n"
        "📲 Установил eSIM — и ты онлайн\n\n"
        "👇 Выбери страну и подключи интернет",
        reply_markup=main_kb
    )


# -----------------------------
# BUY MENU
# -----------------------------
@dp.message(lambda msg: msg.text == "🌍 Купить eSIM")
async def buy(message: types.Message):
    user_id = message.chat.id
    update_user_timestamp(user_id)
    
    USER_STATE.setdefault(user_id, {})
    USER_STATE[user_id]["step"] = "country"
    await message.answer("🌍 Выбери страну или бандл:", reply_markup=countries_kb())


# -----------------------------
# СТРАНА ПО ЗАПРОСУ
# -----------------------------
@dp.message(lambda msg: msg.text == "🔍 Страна по запросу")
async def custom_country_menu(message: types.Message):
    user_id = message.chat.id
    update_user_timestamp(user_id)
    
    USER_STATE.setdefault(user_id, {})
    USER_STATE[user_id]["step"] = "custom"
    await message.answer(
        "🔍 Выбери страну — мы уточним наличие и пришлём цену:",
        reply_markup=custom_countries_kb()
    )


@dp.callback_query(lambda c: c.data.startswith("custom_"))
async def custom_country_selected(callback: types.CallbackQuery):
    user_id = callback.message.chat.id
    update_user_timestamp(user_id)
    
    await callback.answer()
    index = int(callback.data.split("_")[1])

    if index >= len(CUSTOM_COUNTRIES):
        await callback.message.answer("⚠️ Что-то пошло не так, попробуй снова")
        return

    country_name = CUSTOM_COUNTRIES[index]
    USER_STATE.setdefault(user_id, {})
    USER_STATE[user_id]["step"] = "country"

    country_encoded = country_name.replace(" ", "+")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✉️ Написать в поддержку",
                url=f"https://t.me/Who_let_the_dog_out_woof?text=Хочу+eSIM+для+{country_encoded}"
            )
        ]]
    )

    await callback.message.answer(
        f"🔍 {country_name}\n\n"
        "Этой страны пока нет в каталоге.\n"
        "Нажми кнопку — мы уточним наличие и пришлём цену:",
        reply_markup=kb
    )


# -----------------------------
# ОБЫЧНАЯ СТРАНА
# -----------------------------
@dp.message(lambda msg: msg.text in COUNTRIES)
async def country(message: types.Message):
    user_id = message.chat.id
    update_user_timestamp(user_id)

    USER_STATE.setdefault(user_id, {})
    USER_STATE[user_id]["country"] = message.text
    USER_STATE[user_id]["is_bundle"] = False
    USER_STATE[user_id]["step"] = "plan"

    photo = COUNTRY_MEDIA.get(message.text)

    caption = (
        f"🌍 {message.text}\n\n"
        f"⚡ Стабильный интернет по всей стране\n"
        f"📲 Подключение за 2 минуты\n"
        f"🚀 Без роуминга и SIM-карт\n\n"
        f"📦 Выбери тариф ниже:"
    )

    if photo:
        await message.answer_photo(
            photo=photo,
            caption=caption,
            reply_markup=plans_kb(message.text)
        )
    else:
        await message.answer(
            caption,
            reply_markup=plans_kb(message.text)
        )


# -----------------------------
# БАНДЛ
# -----------------------------
@dp.message(lambda msg: msg.text in BUNDLES)
async def bundle(message: types.Message):
    user_id = message.chat.id
    update_user_timestamp(user_id)
    
    USER_STATE.setdefault(user_id, {})
    USER_STATE[user_id]["country"] = message.text
    USER_STATE[user_id]["is_bundle"] = True
    USER_STATE[user_id]["step"] = "plan"
    await message.answer("📦 Выбери тариф:", reply_markup=plans_kb(message.text, is_bundle=True))


# -----------------------------
# PLAN SELECT
# -----------------------------
@dp.callback_query(lambda c: c.data.startswith("plan_"))
async def plan(callback: types.CallbackQuery):
    user_id = callback.message.chat.id
    update_user_timestamp(user_id)
    
    await callback.answer()

    state = USER_STATE.get(user_id, {})
    country_name = state.get("country")
    is_bundle = state.get("is_bundle", False)

    if not country_name:
        await callback.message.answer("⚠️ Что-то пошло не так, начни заново", reply_markup=main_kb)
        return

    index = int(callback.data.split("_")[1])
    plans = BUNDLES[country_name] if is_bundle else COUNTRIES[country_name]

    if index >= len(plans):
        await callback.message.answer("⚠️ Тариф не найден, попробуй снова")
        return

    selected_plan = plans[index]
    USER_STATE[user_id]["plan"] = selected_plan
    USER_STATE[user_id]["step"] = "payment"

    rub = cents_to_rub(selected_plan[3])
    usd = cents_to_usd(selected_plan[3])
    usdt = cents_to_usdt(selected_plan[3])

    await callback.message.answer(
        f"📦 {selected_plan[0]}\n"
        f"📅 {selected_plan[1]}\n"
        f"🌍 {selected_plan[2]}\n\n"
        f"💰 {rub} ₽  |  {usd}  |  {usdt}\n\n"
        "Выбери способ оплаты:",
        reply_markup=payment_kb
    )


# -----------------------------
# НАЗАД
# -----------------------------
@dp.message(lambda msg: msg.text == "⬅️ Назад")
async def back(message: types.Message):
    user_id = message.chat.id
    update_user_timestamp(user_id)
    
    state = USER_STATE.get(user_id, {})
    step = state.get("step")

    if step == "payment":
        country_name = state.get("country")
        is_bundle = state.get("is_bundle", False)
        USER_STATE[user_id]["step"] = "plan"
        USER_STATE[user_id].pop("plan", None)
        await message.answer("📦 Выбери тариф:", reply_markup=plans_kb(country_name, is_bundle=is_bundle))

    elif step in ("plan", "custom"):
        USER_STATE.setdefault(user_id, {})
        USER_STATE[user_id]["step"] = "country"
        await message.answer("🌍 Выбери страну или бандл:", reply_markup=countries_kb())

    else:
        USER_STATE.pop(user_id, None)
        USER_TIMESTAMPS.pop(user_id, None)
        await message.answer("Главное меню", reply_markup=main_kb)



# ----------------------------
# Главное меню
# -----------------------------

@dp.message(lambda msg: msg.text and "Главное меню" in msg.text)
async def go_home(message: types.Message):
    USER_STATE.pop(message.chat.id, None)
    await message.answer("🏠 Главное меню", reply_markup=main_kb)
    
# -----------------------------
# ОПЛАТА: СБП
# -----------------------------
@dp.message(lambda msg: msg.text == "💳 Перевод СБП")
async def sbp(message: types.Message):
    user_id = message.chat.id
    update_user_timestamp(user_id)
    
    state = USER_STATE.get(user_id, {})
    plan = state.get("plan")

    if not plan:
        await message.answer("⚠️ Сначала выбери тариф")
        return

    rub = cents_to_rub(plan[3])

    await message.answer(
        f"💳 Оплата через СБП\n\n"
        f"📦 {plan[0]} — {rub} ₽\n\n"
        "Переведи на номер: +79853808937\n"
        "После оплаты отправь чек: @Who_let_the_dog_out_woof"
    )

# ----------------------------- 
# ОПЛАТА: USDT 
# -----------------------------
@dp.message(lambda msg: msg.text == "💰 USDT (сеть TRC20)")
async def usdt_pay(message: types.Message):
    user_id = message.chat.id
    update_user_timestamp(user_id)

    if user_id not in USER_STATE:
        await message.answer("⚠️ Сначала выбери тариф")
        return

    plan = USER_STATE[user_id].get("plan")

    if not plan:
        await message.answer("⚠️ Тариф не найден, выбери заново")
        return

    amount_usdt = round(plan[3] / 100, 2)

    invoice = await create_invoice(amount_usdt, user_id)

    if not invoice.get("ok"):
        await message.answer("❌ Ошибка создания оплаты, попробуй позже")
        return

    invoice_data = invoice["result"]
    invoice_id = str(invoice_data["invoice_id"])

    # ✅ СОХРАНЕНИЕ В SUPABASE
    try:
        supabase.table("invoices").insert({
            "invoice_id": invoice_id,
            "user_id": user_id,
            "amount": amount_usdt,
            "currency": "USDT",
            "status": "active",
            "plan": plan[0]
        }).execute()
    except Exception as e:
        print("SUPABASE INSERT ERROR:", e)

    # ✅ сохраняем в память
    USER_STATE[user_id]["invoice_id"] = invoice_id

    # ✅ запускаем проверку оплаты
    asyncio.create_task(
        wait_for_payment(user_id, invoice_id)
    )

    pay_url = invoice_data["pay_url"]

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="💰 Оплатить USDT", url=pay_url)
        ]]
    )

    await message.answer(
        f"💰 Оплата через USDT\n\n"
        f"Сумма: {amount_usdt} USDT\n\n"
        "Нажми кнопку ниже чтобы оплатить 👇",
        reply_markup=kb
    )


# -----------------------------
# ПРОЧЕЕ
# -----------------------------
@dp.message(lambda msg: msg.text == "💸 Цены")
async def prices(message: types.Message):
    user_id = message.chat.id
    update_user_timestamp(user_id)
    
    USER_STATE.setdefault(user_id, {})
    USER_STATE[user_id]["step"] = "country"

    country_rows = []
    country_list = list(COUNTRIES.keys())
    
    # ИСПРАВЛЕНО: Правильная индексация для стран
    for i in range(0, len(country_list), 2):
        pair = country_list[i:i+2]
        country_rows.append([
            InlineKeyboardButton(text=c, callback_data=f"country_{i+j}")
            for j, c in enumerate(pair)
        ])

    bundle_list = list(BUNDLES.keys())
    
    # ИСПРАВЛЕНО: Правильная индексация для бандлов
    for i in range(0, len(bundle_list), 2):
        pair = bundle_list[i:i+2]
        country_rows.append([
            InlineKeyboardButton(text=b, callback_data=f"bundle_{i+j}")
            for j, b in enumerate(pair)
        ])

    country_rows.append([
        InlineKeyboardButton(text="🔍 Страна по запросу", callback_data="goto_custom")
    ])

    kb = InlineKeyboardMarkup(inline_keyboard=country_rows)

    await message.answer("🌍 Выбери страну чтобы посмотреть тарифы и купить:", reply_markup=kb)


@dp.callback_query(lambda c: c.data.startswith("country_"))
async def prices_country_selected(callback: types.CallbackQuery):
    user_id = callback.message.chat.id
    update_user_timestamp(user_id)
    
    await callback.answer()
    index = int(callback.data.split("_")[1])
    country_list = list(COUNTRIES.keys())

    if index >= len(country_list):
        await callback.message.answer("⚠️ Что-то пошло не так")
        return

    country_name = country_list[index]
    USER_STATE.setdefault(user_id, {})
    USER_STATE[user_id]["country"] = country_name
    USER_STATE[user_id]["is_bundle"] = False
    USER_STATE[user_id]["step"] = "plan"
    await callback.message.answer("📦 Выбери тариф:", reply_markup=plans_kb(country_name))


@dp.callback_query(lambda c: c.data.startswith("bundle_"))
async def prices_bundle_selected(callback: types.CallbackQuery):
    user_id = callback.message.chat.id
    update_user_timestamp(user_id)
    
    await callback.answer()
    index = int(callback.data.split("_")[1])
    bundle_list = list(BUNDLES.keys())

    if index >= len(bundle_list):
        await callback.message.answer("⚠️ Что-то пошло не так")
        return

    bundle_name = bundle_list[index]
    USER_STATE.setdefault(user_id, {})
    USER_STATE[user_id]["country"] = bundle_name
    USER_STATE[user_id]["is_bundle"] = True
    USER_STATE[user_id]["step"] = "plan"
    await callback.message.answer("📦 Выбери тариф:", reply_markup=plans_kb(bundle_name, is_bundle=True))


@dp.callback_query(lambda c: c.data == "goto_custom")
async def prices_goto_custom(callback: types.CallbackQuery):
    user_id = callback.message.chat.id
    update_user_timestamp(user_id)
    
    await callback.answer()
    USER_STATE.setdefault(user_id, {})
    USER_STATE[user_id]["step"] = "custom"
    await callback.message.answer(
        "🔍 Выбери страну — мы уточним наличие и пришлём цену:",
        reply_markup=custom_countries_kb()
    )


@dp.message(lambda msg: msg.text == "🛠 Поддержка")
async def support(message: types.Message):
    user_id = message.chat.id
    update_user_timestamp(user_id)
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✉️ Написать в поддержку",
                url="https://t.me/Who_let_the_dog_out_woof"
            )
        ]]
    )

    await message.answer(
        "🛠 Поддержка\n\n"
        "Нажми кнопку ниже, чтобы написать 👇",
        reply_markup=kb
    )


# -----------------------------
# RUN
# -----------------------------
async def main():
    asyncio.create_task(update_usd_rate())
    asyncio.create_task(cleanup_old_states())

    while True:
        try:
            logger.info("🚀 Start polling")
            await dp.start_polling(bot)
        except Exception as e:
            logger.error(f"❌ Polling crashed: {e}")
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
