from pydantic_settings import BaseSettings
from functools import lru_cache
import os
from dotenv import load_dotenv

load_dotenv(override=True)

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ADMIN_GROUP_ID: int = int(os.getenv("ADMIN_GROUP_ID", "0"))
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    QUEUE_REDIS_URL: str = os.getenv("QUEUE_REDIS_URL", "redis://localhost:6379/2")
    NUM_WORKERS: int = os.getenv("NUM_WORKERS", 10)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "mysql+aiomysql://root:secretpass@localhost:3390/kodik-bot")
    KB_PATH: str = os.getenv("KB_PATH", "")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

@lru_cache
def get_settings() -> Settings:
    return Settings()
