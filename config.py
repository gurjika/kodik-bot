import os
from pydantic_settings import BaseSettings
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv(override=True)

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN")
    ADMIN_GROUP_ID: int = os.environ.get("ADMIN_GROUP_ID")
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    QUEUE_REDIS_URL: str = os.environ.get("QUEUE_REDIS_URL", "redis://localhost:6379/2")
    NUM_WORKERS: int = int(os.environ.get("NUM_WORKERS", 10))
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "mysql+aiomysql://root:password@localhost:3306/kodik_bot")
    KB_PATH: str = os.environ.get("KB_PATH", "knowledge_base/data/knowledge.json")
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

@lru_cache
def get_settings() -> Settings:
    return Settings()
