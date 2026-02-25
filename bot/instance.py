import telebot.async_telebot as async_telebot
from config import get_settings

settings = get_settings()
bot = async_telebot.AsyncTeleBot(settings.TELEGRAM_BOT_TOKEN)
