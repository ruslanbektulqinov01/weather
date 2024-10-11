import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters.command import Command
from aiogram.enums import ChatAction, ParseMode
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from get_emoji import get_weather_emoji
from regions import UZBEKISTAN_REGIONS
import pytz
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
WEATHER_API_KEY = os.getenv('WEATHER_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
Base = declarative_base()
engine = create_async_engine(DATABASE_URL, echo=True)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


class UserState:
    def __init__(self):
        self.locations = {}


user_state = UserState()


class WeatherLog(Base):
    __tablename__ = 'weather_logs'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    location = Column(String, nullable=False)
    temperature = Column(Float, nullable=False)
    weather_desc = Column(String, nullable=False)
    request_time = Column(DateTime, default=datetime.utcnow)


class DatabaseManager:
    @staticmethod
    async def init_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    @staticmethod
    async def save_weather_log(user_id: int, location: str, temperature: float, weather_desc: str):
        async with async_session() as session:
            async with session.begin():
                weather_log = WeatherLog(
                    user_id=user_id,
                    location=location,
                    temperature=temperature,
                    weather_desc=weather_desc
                )
                session.add(weather_log)
            await session.commit()


class WeatherService:
    @staticmethod
    async def fetch_weather(location: str, forecast_type: str = 'current') -> Optional[dict]:
        base_url = "https://api.weatherapi.com/v1"

        if forecast_type in ['hourly', 'weekly']:
            endpoint = f"{base_url}/forecast.json"
            params = {
                'key': WEATHER_API_KEY,
                'q': location,
                'days': 7 if forecast_type == 'weekly' else 2,
                'aqi': 'no'
            }
        else:
            endpoint = f"{base_url}/current.json"
            params = {
                'key': WEATHER_API_KEY,
                'q': location,
                'aqi': 'no'
            }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    logger.error(f"API Error: {resp.status} - {await resp.text()}")
                    return None
        except Exception as e:
            logger.error(f"Error fetching weather data: {e}")
            return None


def get_regions_keyboard():
    builder = ReplyKeyboardBuilder()
    for region in UZBEKISTAN_REGIONS.keys():
        builder.add(KeyboardButton(text=f"🏠 {region}"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_districts_keyboard(region: str):
    builder = ReplyKeyboardBuilder()
    districts = UZBEKISTAN_REGIONS.get(region.replace("🏠 ", ""), [])
    for district in districts:
        builder.add(KeyboardButton(text=f"🏘 {district}"))
    builder.add(KeyboardButton(text="🔙 Orqaga"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🏠 Viloyatlar"),
        KeyboardButton(text="🌤 Ob-havo tekshirish")
    )  # Viloyatlar va Ob-havo tekshirish yuqorida
    builder.row(KeyboardButton(text="📅 Vaqt tanlash"))  # Vaqt tanlash o'rtada
    builder.row(
        KeyboardButton(text="📞 Aloqa"),
        KeyboardButton(text="ℹ️ Yordam")
    )  # Aloqa va Yordam pastda
    return builder.as_markup(resize_keyboard=True)


@dp.message(F.text == "🌤 Ob-havo tekshirish")
async def weather_command(message: types.Message):
    user_id = message.from_user.id
    location = user_state.locations.get(user_id)

    if not location:
        await message.answer(
            "Iltimos, avval viloyat va tumanni tanlang!",
            reply_markup=get_regions_keyboard()
        )
        return

    await send_current_weather(message, location)


@dp.message(F.text == "📅 Vaqt tanlash")
async def forecast_options_command(message: types.Message):
    user_id = message.from_user.id
    location = user_state.locations.get(user_id)

    if not location:
        await message.answer(
            "Iltimos, avval viloyat va tumanni tanlang!",
            reply_markup=get_regions_keyboard()
        )
        return

    await message.answer(
        f"{location} uchun qaysi vaqt oralig'idagi ob-havo ma'lumotini ko'rmoqchisiz?",
        reply_markup=get_forecast_keyboard(location)
    )


@dp.callback_query(F.data.startswith("forecast:"))
async def handle_forecast_callback(callback: types.CallbackQuery):
    _, forecast_type, location = callback.data.split(":")

    if forecast_type == "today":
        await send_current_weather(callback.message, location)
    elif forecast_type == "hourly":
        await send_hourly_forecast(callback.message, location)
    elif forecast_type == "weekly":
        await send_weekly_forecast(callback.message, location)

    await callback.answer()


# Keyboardlarni yangilash


def get_forecast_keyboard(location: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🕒 Hozirgi", callback_data=f"forecast:today:{location}"),
            InlineKeyboardButton(text="⏱ Soatlik", callback_data=f"forecast:hourly:{location}")
        ],
        [
            InlineKeyboardButton(text="📅 Haftalik", callback_data=f"forecast:weekly:{location}")
        ]
    ])
    return keyboard


@dp.message(F.text == "🏠 Viloyatlar")
async def show_regions(message: types.Message):
    await message.answer("Viloyatni tanlang:", reply_markup=get_regions_keyboard())


@dp.message(F.text.startswith("🏠 "))
async def show_districts(message: types.Message):
    region = message.text
    await message.answer(f"{region} tumanlari:", reply_markup=get_districts_keyboard(region))


@dp.message(F.text.startswith("🏘 "))
async def handle_district_selection(message: types.Message):
    district = message.text.replace("🏘 ", "")
    user_id = message.from_user.id

    # Verify that the selected district is valid
    is_valid_district = any(
        district in districts
        for districts in UZBEKISTAN_REGIONS.values()
    )

    if is_valid_district:
        user_state.locations[user_id] = district
        await message.answer(
            f"Sizning tanlovingiz: <b>{district}</b>\n\n",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_keyboard()
        )

        # Hozirgi ob-havo ma'lumotlarini ko'rsatish
        await send_current_weather(message, district)

    else:
        await message.answer(
            "Iltimos, ob-havo ma'lumotlarini olish uchun ro'yxatdan tumanlardan birini tanlang.",
            reply_markup=get_regions_keyboard()
        )


@dp.message(F.text == "🔙 Orqaga")
async def go_back(message: types.Message):
    await message.answer("Asosiy menyu:", reply_markup=get_main_keyboard())


@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        f"Assalomu alaykum, {message.from_user.first_name}! 🌤️\n"
        "Men ob-havo ma'lumotlarini beruvchi botman.\n"
        "Ob-havo ma'lumotlarini olish uchun quyidagi tugmalardan foydalaning:\n\n"
        "1. 🏠 Viloyatlar - Viloyat va tumanini tanlash\n"
        "2. 🌤 Ob-havo tekshirish - Tanlangan hudud uchun ob-havo\n"
        "3. 📅 Vaqt tanlash - Turli vaqt uchun ob-havo",
        reply_markup=get_main_keyboard()
    )


@dp.message(F.text == "ℹ️ Yordam")
async def help_command(message: types.Message):
    help_text = (
        "Bot dan foydalanish bo'yicha yordam:\n\n"
        "1. 🏠 <b>Viloyatlar</b> - Viloyat va tumanini tanlash\n"
        "2. 🌤 <b>Ob-havo tekshirish</b> - Tanlangan hudud uchun ob-havo ma'lumoti\n"
        "3. 📅 <b>Vaqt tanlash</b> - Turli vaqt oralig'i uchun ob-havo\n\n"
        "<i>Eslatma: Ob-havo ma'lumotlarini olish uchun avval viloyat va "
        "tumanni tanlash kerak!</i>"
    )
    await message.answer(help_text, parse_mode=ParseMode.HTML)


@dp.message(F.text == "📞 Aloqa")
async def contact_handler(message: types.Message):
    await message.answer(
        "😊 Assalomu alaykum! 🤖 Men bilan bog'lanishni xohlaysizmi?\n"
        "👏 Ajoyib! Fikr-mulohazalaringiz, takliflaringiz yoki savollar bilan bemalol murojaat qiling!\n"
        "👨‍💻 Sizning xabarlaringiz biz uchun juda muhim va botni yanada yaxshilashga yordam beradi!\n"
        "Quyidagi usullar orqali bog'lanishingiz mumkin:\n\n"
        "📩 Telegram orqali: @ruslanbektulqinov\n\n"
        "💡 Eslatma: Agar bot sizga yoqqan bo'lsa, uni do'stlaringizga ham ulashing! 😉\n"
        "🔗 Doimiy ob-havo ma'lumotlari uchun quyidagi ko'k yozuv ustiga bosing:\n"
        "👉 <a href='https://t.me/weather_ob_havobot'>Ob-havo</a>",
        reply_markup=get_main_keyboard()  # Asosiy klaviaturaga qaytish
    )


@dp.message(F.text.in_(["🌤 Ob-havo tekshirish", "📅 Vaqt tanlash"]))
async def weather_command(message: types.Message):
    user_id = message.from_user.id
    location = user_state.locations.get(user_id)

    if not location:
        await message.answer(
            "Iltimos, avval viloyat va tumanni tanlang!",
            reply_markup=get_regions_keyboard()
        )
        return

    if message.text == "🌤 Ob-havo tekshirish":
        await send_current_weather(message, location)
    else:
        await message.answer(
            f"{location} uchun qaysi vaqt oralig'idagi ob-havo ma'lumotini ko'rmoqchisiz?",
            reply_markup=get_forecast_keyboard(location)
        )


@dp.message(F.text)
async def handle_text(message: types.Message):
    # Instead of processing any text as a location, we'll guide users to use buttons
    if message.text not in ["🌤 Ob-havo tekshirish", "📅 Vaqt tanlash", "ℹ️ Yordam", "🏠 Viloyatlar", "🔙 Orqaga"]:
        await message.answer(
            "Iltimos, ob-havo ma'lumotlarini olish uchun quyidagi tugmalardan foydalaning:",
            reply_markup=get_main_keyboard()
        )


@dp.callback_query(F.data.startswith("update_weather:"))
async def update_weather_callback(callback_query: types.CallbackQuery):
    _, action, location = callback_query.data.split(":")

    if action == "current":
        await send_current_weather(callback_query.message, location)
    elif action == "hourly":
        await send_hourly_forecast(callback_query.message, location)
    elif action == "weekly":
        await send_weekly_forecast(callback_query.message, location)

    await callback_query.answer()


async def main():
    logger.info("Bot ishga tushirilmoqda...")
    try:
        await DatabaseManager.init_db()
        logger.info("Bot ishga tushdi...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Xatolik yuz berdi: {e}")
    finally:
        await bot.session.close()


async def send_current_weather(message: types.Message, location: str):
    try:
        weather_data = await WeatherService.fetch_weather(location)
        if weather_data and 'current' in weather_data:
            current = weather_data['current']

            # Get forecast data separately to handle potential missing data
            forecast_data = await WeatherService.fetch_weather(location, 'weekly')
            astro = None
            if forecast_data and 'forecast' in forecast_data:
                try:
                    astro = forecast_data['forecast']['forecastday'][0]['astro']
                except (KeyError, IndexError):
                    astro = None
            uz_time = datetime.now(pytz.timezone('Asia/Tashkent'))

            response = [
                f"📅 Bugun, {uz_time.strftime('%A')}, {uz_time.strftime('%d-%B')}",
                f"📍 {location}\n",
                f"🌡 Hozirgi ob-havo:",
                f"{get_weather_emoji(current['condition']['text'])} {current['condition']['text']}",
                f"Harorat: {current['temp_c']}°C",
                f"His etilishi: {current['feelslike_c']}°C",
                "———",
                f"Bulutlilik: {current['cloud']}%",
                f"Namlik: {current['humidity']}%",
                f"Shamol: {current['wind_kph']} km/soat",
                f"Bosim: {current['pressure_mb']} mbar"
            ]

            if astro:
                response.extend([
                    f"Quyosh chiqishi: {astro['sunrise']}",
                    f"Quyosh botishi: {astro['sunset']}"
                ])

            response.extend([
                f"\n♻️ So'nggi yangilanish: {uz_time.strftime('%H:%M')}"
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"update_weather:current:{location}"),
                    InlineKeyboardButton(text="📅 Haftalik", callback_data=f"update_weather:weekly:{location}"),
                    InlineKeyboardButton(text="🕒 Soatlik", callback_data=f"update_weather:hourly:{location}")
                ]
            ])

            await message.answer("\n".join(response), reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            await message.answer(
                f"Kechirasiz, {location} uchun ma'lumot topilmadi. Shahar nomini tekshirib, qayta urinib ko'ring.")
    except Exception as e:
        logger.error(f"Error in send_current_weather: {e}")
        await message.answer(
            f"Kechirasiz, ob-havo ma'lumotlarini olishda xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring.")


async def send_weekly_forecast(message: types.Message, location: str):
    weather_data = await WeatherService.fetch_weather(location, 'weekly')
    if weather_data:
        forecast_days = weather_data['forecast']['forecastday']

        response = [f"📅 Haftalik ob-havo\n📍 {location}\n"]

        for day in forecast_days:
            date = datetime.fromisoformat(day['date'])
            day_name = date.strftime('%A')
            day_data = day['day']

            response.append(
                f"\n{day_name}, {date.strftime('%d-%B')}\n"
                f"{get_weather_emoji(day_data['condition']['text'])} "
                f"+{day_data['maxtemp_c']}° ... +{day_data['mintemp_c']}°  {day_data['condition']['text']}\n"
                f"Yog'ingarchilik ehtimoli: {day_data['daily_chance_of_rain']}%"
            )
        uz_time = datetime.now(pytz.timezone('Asia/Tashkent'))
        response.extend([
            f"\n♻️ So'nggi yangilanish: {uz_time.strftime('%H:%M')}"
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"update_weather:weekly:{location}"),
             InlineKeyboardButton(text="🌡 Hozirgi ob-havo", callback_data=f"update_weather:current:{location}")]
        ])

        await message.answer("\n".join(response), reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        await message.answer(
            f"Kechirasiz, {location} uchun ma'lumot topilmadi. Shahar nomini tekshirib, qayta urinib ko'ring.")


async def send_hourly_forecast(message: types.Message, location: str):
    weather_data = await WeatherService.fetch_weather(location, 'hourly')
    if weather_data:
        hourly_forecast = weather_data['forecast']['forecastday'][0]['hour']  # Faqat birinchi 24 soat

        uz_time = datetime.now(pytz.timezone('Asia/Tashkent'))
        start_hour = uz_time.replace(minute=0, second=0, microsecond=0)

        response = [f"🕒 24 soatlik ob-havo\n📍 {location}\n"]  # Sarlavhani o'zgartiring

        for i, hour in enumerate(hourly_forecast[:24]):  # Faqat dastlabki 24 soat
            forecast_time = start_hour + timedelta(hours=i)
            if i == 0:
                response.append("\n🔹 Hozirdan boshlab 24 soat")

            response.append(
                f"{forecast_time.strftime('%H:%M')} — "
                f"{get_weather_emoji(hour['condition']['text'])} {hour['temp_c']}°, "
                f"{hour['condition']['text']}"
            )

        response.extend([
            f"\n♻️ So'nggi yangilanish: {uz_time.strftime('%H:%M')}"
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"update_weather:hourly:{location}"),
             InlineKeyboardButton(text="🌡 Hozirgi ob-havo", callback_data=f"update_weather:current:{location}")]
        ])

        await message.answer("\n".join(response), reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        await message.answer(
            f"Kechirasiz, {location} uchun ma'lumot topilmadi. Shahar nomini tekshirib, qayta urinib ko'ring.")


if __name__ == "__main__":
    asyncio.run(main())
