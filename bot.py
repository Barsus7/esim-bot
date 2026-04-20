import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice
)
from aiogram.filters import Command

# -----------------------------
# CONFIG
# -----------------------------
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

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
        except Exception:
            pass
        await asyncio.sleep(3600)


def cents_to_usd(cents: int) -> str:
    """Центы → строка доллары. 159 → '$1.59'"""
    return f"${cents / 100:.2f}"


def cents_to_rub(cents: int) -> int:
    """Центы → рубли по курсу ЦБ"""
    rate = USD_RATE.get("value", 90.0)
    return round(cents / 100 * rate)


def cents_to_usdt(cents: int) -> str:
    """Центы → строка USDT. 159 → '1.59 USDT'"""
    return f"{cents / 100:.2f} USDT"


# -----------------------------
# STATE
# -----------------------------
USER_STATE: dict[int, dict] = {}

# -----------------------------
# DATA — обычные страны
# Тариф: (название, срок, скорость, цена_центы)
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
# DATA — страны по запросу (100 стран)
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
    USER_STATE.pop(message.chat.id, None)
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
    USER_STATE[message.chat.id] = {"step": "country"}
    await message.answer("🌍 Выбери страну или бандл:", reply_markup=countries_kb())


# -----------------------------
# СТРАНА ПО ЗАПРОСУ
# -----------------------------
@dp.message(lambda msg: msg.text == "🔍 Страна по запросу")
async def custom_country_menu(message: types.Message):
    USER_STATE[message.chat.id] = {"step": "custom"}
    await message.answer(
        "🔍 Выбери страну — мы уточним наличие и пришлём цену:",
        reply_markup=custom_countries_kb()
    )


@dp.callback_query(lambda c: c.data.startswith("custom_"))
async def custom_country_selected(callback: types.CallbackQuery):
    await callback.answer()
    index = int(callback.data.split("_")[1])

    if index >= len(CUSTOM_COUNTRIES):
        await callback.message.answer("⚠️ Что-то пошло не так, попробуй снова")
        return

    country_name = CUSTOM_COUNTRIES[index]
    USER_STATE[callback.message.chat.id] = {"step": "country"}

    await callback.message.answer(
        f"📩 Запрос на {country_name} отправлен!\n\n"
        f"Напиши в поддержку и укажи страну — мы подберём тариф:\n"
        f"👉 @Who_let_the_dog_out_wooft\n\n"
        f"Сообщение: «Хочу eSIM для {country_name}»",
        reply_markup=countries_kb()
    )


# -----------------------------
# ОБЫЧНАЯ СТРАНА
# -----------------------------
@dp.message(lambda msg: msg.text in COUNTRIES)
async def country(message: types.Message):
    USER_STATE[message.chat.id] = {
        "country": message.text,
        "is_bundle": False,
        "step": "plan"
    }
    await message.answer("📦 Выбери тариф:", reply_markup=plans_kb(message.text))


# -----------------------------
# БАНДЛ
# -----------------------------
@dp.message(lambda msg: msg.text in BUNDLES)
async def bundle(message: types.Message):
    USER_STATE[message.chat.id] = {
        "country": message.text,
        "is_bundle": True,
        "step": "plan"
    }
    await message.answer("📦 Выбери тариф:", reply_markup=plans_kb(message.text, is_bundle=True))


# -----------------------------
# PLAN SELECT
# -----------------------------
@dp.callback_query(lambda c: c.data.startswith("plan_"))
async def plan(callback: types.CallbackQuery):
    await callback.answer()

    user_id = callback.message.chat.id
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
    state = USER_STATE.get(message.chat.id, {})
    step = state.get("step")

    if step == "payment":
        country_name = state.get("country")
        is_bundle = state.get("is_bundle", False)
        USER_STATE[message.chat.id]["step"] = "plan"
        USER_STATE[message.chat.id].pop("plan", None)
        await message.answer("📦 Выбери тариф:", reply_markup=plans_kb(country_name, is_bundle=is_bundle))

    elif step in ("plan", "custom"):
        USER_STATE[message.chat.id] = {"step": "country"}
        await message.answer("🌍 Выбери страну или бандл:", reply_markup=countries_kb())

    else:
        USER_STATE.pop(message.chat.id, None)
        await message.answer("Главное меню", reply_markup=main_kb)


# -----------------------------
# ОПЛАТА: СБП
# -----------------------------
@dp.message(lambda msg: msg.text == "💳 Перевод СБП")
async def sbp(message: types.Message):
    state = USER_STATE.get(message.chat.id, {})
    plan = state.get("plan")

    if not plan:
        await message.answer("⚠️ Сначала выбери тариф")
        return

    rub = cents_to_rub(plan[3])

    await message.answer(
        f"💳 Оплата через СБП\n\n"
        f"📦 {plan[0]} — {rub} ₽\n\n"
        "Переведи на номер: +79853808937\n"
        "После оплаты отправь чек: @Who_let_the_dog_out_wooft"
    )


# -----------------------------
# ОПЛАТА: USDT
# -----------------------------
@dp.message(lambda msg: msg.text == "💰 USDT (сеть TRC20)")
async def usdt_pay(message: types.Message):
    state = USER_STATE.get(message.chat.id, {})
    plan = state.get("plan")

    if not plan:
        await message.answer("⚠️ Сначала выбери тариф")
        return

    usdt = cents_to_usdt(plan[3])
    wallet = "TSP8CN1WjbKeukmToJYpXD5PDSBYFNDEa1"

    await message.answer(
        f"💰 Оплата через USDT (TRC-20)\n\n"
        f"📦 {plan[0]} — {usdt}\n\n"
        f"Адрес: <code>{wallet}</code>\n\n"
        "После оплаты отправь хэш транзакции: @Who_let_the_dog_out_wooft",
        parse_mode="HTML"
    )

# -----------------------------
# ПРОЧЕЕ
# -----------------------------
@dp.message(lambda msg: msg.text == "💸 Цены")
async def prices(message: types.Message):
    lines = ["📋 Актуальные тарифы:\n"]
    for country_name, plans in COUNTRIES.items():
        lines.append(f"{country_name}")
        for p in plans:
            rub = cents_to_rub(p[3])
            usd = cents_to_usd(p[3])
            lines.append(f"  • {p[0]} / {p[1]} — {rub} ₽ ({usd})")
        lines.append("")

    lines.append("— Бандлы —\n")
    for bundle_name, plans in BUNDLES.items():
        lines.append(f"{bundle_name}")
        for p in plans:
            rub = cents_to_rub(p[3])
            usd = cents_to_usd(p[3])
            lines.append(f"  • {p[0]} / {p[1]} — {rub} ₽ ({usd})")
        lines.append("")

    await message.answer("\n".join(lines))


@dp.message(lambda msg: msg.text == "🛠 Поддержка")
async def support(message: types.Message):
    await message.answer(
        "🛠 Поддержка: @Who_let_the_dog_out_wooft\n"
        "Время ответа: обычно до 30 минут в рабочее время,\n" 
         "но скорее всего это будет гораздо быстрее\n"
        "Рабочее время по МСК: 10.00-18.00"
    )


# -----------------------------
# RUN
# -----------------------------
async def main():
    asyncio.create_task(update_usd_rate())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
