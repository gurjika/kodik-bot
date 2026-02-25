from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    ADMIN_GROUP_ID: int  # negative number for groups, e.g. -1001234567890

    # OpenAI
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Worker pool
    NUM_WORKERS: int = 20

    # Knowledge base
    KB_PATH: str = "knowledge_base/data/knowledge.json"

    # Logging
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
