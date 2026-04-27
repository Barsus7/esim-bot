import os
import asyncio
import threading
import aiohttp
from http.server import HTTPServer, BaseHTTPRequestHandler

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# -----------------------------
# KEEP-ALIVE (для хостинга Render и т.п.)
# Вынесен сюда чтобы не запускался при импорте модуля
# -----------------------------
def _start_keepalive():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format, *args):
            pass  # заглушаем логи keepalive-сервера

    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


# -----------------------------
# CONFIG
# -----------------------------
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# -----------------------------
# FSM — состояния пользователя
# -----------------------------
class Order(StatesGroup):
    choosing_country = State()
    choosing_custom = State()
    choosing_plan = State()
    choosing_payment = State()


# -----------------------------
# КУРС ВАЛЮТ
# -----------------------------
_usd_rate: float = 90.0
_usd_rate_lock = asyncio.Lock()


async def update_usd_rate():
    global _usd_rate
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://www.cbr-xml-daily.ru/daily_json.js"
                ) as resp:
                    data = await resp.json(content_type=None)
                    rate = float(data["Valute"]["USD"]["Value"])
                    if rate > 0:
                        async with _usd_rate_lock:
                            _usd_rate = rate
        except Exception:
            pass
        await asyncio.sleep(3600)


async def get_usd_rate() -> float:
    async with _usd_rate_lock:
        return _usd_rate


def cents_to_usd(cents: int) -> str:
    return f"${cents / 100:.2f}"


async def cents_to_rub(cents: int) -> int:
    rate = await get_usd_rate()
    return round(cents / 100 * rate)


def cents_to_usdt(cents: int) -> str:
    return f"{cents / 100:.2f} USDT"


# -----------------------------
# DATA — обычные страны
# (название, срок, скорость/покрытие, цена_центы)
# -----------------------------
COUNTRIES: dict[str, list[tuple]] = {
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

# -----------------------------
# DATA — бандлы
# -----------------------------
BUNDLES: dict[str, list[tuple]] = {
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
        [KeyboardButton(text="💸 Цены"), KeyboardButton(text="🛠 Поддержка")],
    ],
    resize_keyboard=True,
)

payment_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💳 Перевод СБП")],
        [KeyboardButton(text="💰 USDT (сеть TRC20)")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True,
)


def countries_kb() -> ReplyKeyboardMarkup:
    all_items = list(COUNTRIES.keys()) + list(BUNDLES.keys())
    rows = []
    for i in range(0, len(all_items), 2):
        pair = all_items[i : i + 2]
        rows.append([KeyboardButton(text=c) for c in pair])
    rows.append([KeyboardButton(text="🔍 Страна по запросу")])
    rows.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def plans_kb(country_name: str, is_bundle: bool = False) -> InlineKeyboardMarkup:
    """Async потому что нужен курс для отображения цены в рублях."""
    plans = BUNDLES[country_name] if is_bundle else COUNTRIES[country_name]
    buttons = []
    for i, p in enumerate(plans):
        rub = await cents_to_rub(p[3])
        buttons.append([
            InlineKeyboardButton(
                text=f"{p[0]} — {rub} ₽ ({cents_to_usd(p[3])})",
                callback_data=f"plan_{i}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def custom_countries_kb() -> InlineKeyboardMarkup:
    """Используем enumerate напрямую — индекс всегда точный."""
    rows = []
    items = list(enumerate(CUSTOM_COUNTRIES))
    for i in range(0, len(items), 2):
        pair = items[i : i + 2]
        rows.append([
            InlineKeyboardButton(text=name, callback_data=f"custom_{idx}")
            for idx, name in pair
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# -----------------------------
# START
# -----------------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 На связи! Это eSIM для поездок 🌍\n\n"
        "🌐 Интернет заграницей\n"
        "⚡ Подключение за пару минут\n"
        "📲 Установил eSIM — и ты онлайн\n\n"
        "👇 Выбери страну и подключи интернет",
        reply_markup=main_kb,
    )


# -----------------------------
# BUY MENU
# -----------------------------
@dp.message(F.text == "🌍 Купить eSIM")
async def cmd_buy(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(Order.choosing_country)
    await message.answer("🌍 Выбери страну или бандл:", reply_markup=countries_kb())


# -----------------------------
# СТРАНА ПО ЗАПРОСУ
# -----------------------------
@dp.message(F.text == "🔍 Страна по запросу")
async def cmd_custom_country_menu(message: types.Message, state: FSMContext):
    await state.set_state(Order.choosing_custom)
    await message.answer(
        "🔍 Выбери страну — мы уточним наличие и пришлём цену:",
        reply_markup=custom_countries_kb(),
    )


@dp.callback_query(Order.choosing_custom, F.data.startswith("custom_"))
async def cb_custom_country_selected(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.split("_")[1])

    if index >= len(CUSTOM_COUNTRIES):
        await callback.message.answer("⚠️ Что-то пошло не так, попробуй снова")
        return

    country_name = CUSTOM_COUNTRIES[index]
    await state.set_state(Order.choosing_country)

    country_encoded = country_name.replace(" ", "+")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✉️ Написать в поддержку",
                url=f"https://t.me/Who_let_the_dog_out_woof?text=Хочу+eSIM+для+{country_encoded}",
            )
        ]]
    )
    await callback.message.answer(
        f"🔍 {country_name}\n\n"
        "Этой страны пока нет в каталоге.\n"
        "Нажми кнопку — мы уточним наличие и пришлём цену:",
        reply_markup=kb,
    )


# -----------------------------
# ОБЫЧНАЯ СТРАНА
# -----------------------------
@dp.message(Order.choosing_country, F.text.in_(COUNTRIES.keys()))
async def cmd_country_selected(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text, is_bundle=False)
    await state.set_state(Order.choosing_plan)
    kb = await plans_kb(message.text, is_bundle=False)
    await message.answer("📦 Выбери тариф:", reply_markup=kb)


# -----------------------------
# БАНДЛ
# -----------------------------
@dp.message(Order.choosing_country, F.text.in_(BUNDLES.keys()))
async def cmd_bundle_selected(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text, is_bundle=True)
    await state.set_state(Order.choosing_plan)
    kb = await plans_kb(message.text, is_bundle=True)
    await message.answer("📦 Выбери тариф:", reply_markup=kb)


# -----------------------------
# ВЫБОР ТАРИФА
# -----------------------------
@dp.callback_query(Order.choosing_plan, F.data.startswith("plan_"))
async def cb_plan_selected(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    data = await state.get_data()
    country_name = data.get("country")
    is_bundle = data.get("is_bundle", False)

    if not country_name:
        await callback.message.answer("⚠️ Что-то пошло не так, начни заново", reply_markup=main_kb)
        await state.clear()
        return

    index = int(callback.data.split("_")[1])
    plans = BUNDLES[country_name] if is_bundle else COUNTRIES[country_name]

    if index >= len(plans):
        await callback.message.answer("⚠️ Тариф не найден, попробуй снова")
        return

    selected_plan = plans[index]

    # Сохраняем тариф как список (tuple не сериализуется в FSM storage)
    await state.update_data(plan=list(selected_plan))
    await state.set_state(Order.choosing_payment)

    rub = await cents_to_rub(selected_plan[3])
    usd = cents_to_usd(selected_plan[3])
    usdt = cents_to_usdt(selected_plan[3])

    await callback.message.answer(
        f"📦 {selected_plan[0]}\n"
        f"📅 {selected_plan[1]}\n"
        f"🌍 {selected_plan[2]}\n\n"
        f"💰 {rub} ₽  |  {usd}  |  {usdt}\n\n"
        "Выбери способ оплаты:",
        reply_markup=payment_kb,
    )


# -----------------------------
# КНОПКА НАЗАД
# -----------------------------
@dp.message(F.text == "⬅️ Назад")
async def cmd_back(message: types.Message, state: FSMContext):
    current = await state.get_state()

    if current == Order.choosing_payment:
        data = await state.get_data()
        country_name = data.get("country")
        is_bundle = data.get("is_bundle", False)
        await state.update_data(plan=None)
        await state.set_state(Order.choosing_plan)
        kb = await plans_kb(country_name, is_bundle=is_bundle)
        await message.answer("📦 Выбери тариф:", reply_markup=kb)

    elif current in (Order.choosing_plan, Order.choosing_custom):
        await state.set_state(Order.choosing_country)
        await message.answer("🌍 Выбери страну или бандл:", reply_markup=countries_kb())

    else:
        await state.clear()
        await message.answer("Главное меню", reply_markup=main_kb)


# -----------------------------
# ОПЛАТА: СБП
# -----------------------------
@dp.message(Order.choosing_payment, F.text == "💳 Перевод СБП")
async def cmd_sbp(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan = data.get("plan")

    if not plan:
        await message.answer("⚠️ Сначала выбери тариф", reply_markup=main_kb)
        await state.clear()
        return

    rub = await cents_to_rub(plan[3])
    await message.answer(
        f"💳 Оплата через СБП\n\n"
        f"📦 {plan[0]} — {rub} ₽\n\n"
        "Переведи на номер: +79853808937\n"
        "После оплаты отправь чек: @Who_let_the_dog_out_woof"
    )


# -----------------------------
# ОПЛАТА: USDT
# -----------------------------
@dp.message(Order.choosing_payment, F.text == "💰 USDT (сеть TRC20)")
async def cmd_usdt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan = data.get("plan")

    if not plan:
        await message.answer("⚠️ Сначала выбери тариф", reply_markup=main_kb)
        await state.clear()
        return

    usdt = cents_to_usdt(plan[3])
    wallet = "TSP8CN1WjbKeukmToJYpXD5PDSBYFNDEa1"

    await message.answer(
        f"💰 Оплата через USDT (TRC-20)\n\n"
        f"📦 {plan[0]} — {usdt}\n\n"
        f"Адрес: <code>{wallet}</code>\n\n"
        "После оплаты отправь хэш транзакции: @Who_let_the_dog_out_woof",
        parse_mode="HTML",
    )


# -----------------------------
# МЕНЮ ЦЕН
# -----------------------------
@dp.message(F.text == "💸 Цены")
async def cmd_prices(message: types.Message, state: FSMContext):
    await state.set_state(Order.choosing_country)

    rows = []
    country_list = list(COUNTRIES.keys())
    for i in range(0, len(country_list), 2):
        pair = list(enumerate(country_list))[i : i + 2]
        rows.append([
            InlineKeyboardButton(text=name, callback_data=f"prices_country_{idx}")
            for idx, name in pair
        ])

    bundle_list = list(BUNDLES.keys())
    for i in range(0, len(bundle_list), 2):
        pair = list(enumerate(bundle_list))[i : i + 2]
        rows.append([
            InlineKeyboardButton(text=name, callback_data=f"prices_bundle_{idx}")
            for idx, name in pair
        ])

    rows.append([InlineKeyboardButton(text="🔍 Страна по запросу", callback_data="goto_custom")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer("🌍 Выбери страну чтобы посмотреть тарифы и купить:", reply_markup=kb)


@dp.callback_query(Order.choosing_country, F.data.startswith("prices_country_"))
async def cb_prices_country(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.split("_")[2])
    country_list = list(COUNTRIES.keys())

    if index >= len(country_list):
        await callback.message.answer("⚠️ Что-то пошло не так")
        return

    country_name = country_list[index]
    await state.update_data(country=country_name, is_bundle=False)
    await state.set_state(Order.choosing_plan)
    kb = await plans_kb(country_name, is_bundle=False)
    await callback.message.answer("📦 Выбери тариф:", reply_markup=kb)


@dp.callback_query(Order.choosing_country, F.data.startswith("prices_bundle_"))
async def cb_prices_bundle(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    index = int(callback.data.split("_")[2])
    bundle_list = list(BUNDLES.keys())

    if index >= len(bundle_list):
        await callback.message.answer("⚠️ Что-то пошло не так")
        return

    bundle_name = bundle_list[index]
    await state.update_data(country=bundle_name, is_bundle=True)
    await state.set_state(Order.choosing_plan)
    kb = await plans_kb(bundle_name, is_bundle=True)
    await callback.message.answer("📦 Выбери тариф:", reply_markup=kb)


@dp.callback_query(Order.choosing_country, F.data == "goto_custom")
async def cb_goto_custom(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(Order.choosing_custom)
    await callback.message.answer(
        "🔍 Выбери страну — мы уточним наличие и пришлём цену:",
        reply_markup=custom_countries_kb(),
    )


# -----------------------------
# ПОДДЕРЖКА
# -----------------------------
@dp.message(F.text == "🛠 Поддержка")
async def cmd_support(message: types.Message):
    await message.answer(
        "🛠 Поддержка: @Who_let_the_dog_out_woof\n"
        "Время ответа: обычно до 30 минут в рабочее время,\n"
        "но скорее всего это будет гораздо быстрее\n"
        "Рабочее время по МСК: 10.00-18.00"
    )


# -----------------------------
# FALLBACK — неизвестные сообщения
# -----------------------------
@dp.message()
async def cmd_fallback(message: types.Message):
    await message.answer("Не понял. Используй кнопки 👇", reply_markup=main_kb)


# -----------------------------
# RUN
# -----------------------------
async def main():
    _start_keepalive()
    asyncio.create_task(update_usd_rate())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
