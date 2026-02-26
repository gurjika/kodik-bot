import asyncio
import logging
import colorlog
from config import get_settings
from agent.graph import create_graph
from bot.handlers import register_user_handlers
from bot.admin import register_admin_handlers
from bot.group import register_group_handlers
from bot.instance import bot
from workers.worker import WorkerPool
from storage.database import init_db
from dotenv import load_dotenv

load_dotenv(override=True)


def _configure_logging(level: str) -> None:
    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        log_colors={
            "DEBUG":    "cyan",
            "INFO":     "green",
            "WARNING":  "yellow",
            "ERROR":    "red",
            "CRITICAL": "red,bg_white",
        },
    ))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[handler],
    )


async def main() -> None:
    settings = get_settings()
    _configure_logging(settings.LOG_LEVEL)
    logger = logging.getLogger(__name__)

    logger.info("Starting kodik-bot")
    logger.info("Workers: %d | Model: %s", settings.NUM_WORKERS, settings.OPENAI_MODEL)

    await init_db()
    logger.info("Database initialized")

    graph = await create_graph()
    logger.info("LangGraph agent ready")

    me = await bot.get_me()
    register_user_handlers(bot, bot_username=me.username)
    register_admin_handlers(bot, bot_username=me.username)

    register_group_handlers(bot, bot_username=me.username)
    logger.info("Group monitor active (@%s)", me.username)

    pool = WorkerPool(graph=graph, num_workers=settings.NUM_WORKERS)
    pool.start()

    try:
        await bot.polling(non_stop=True, timeout=30)
    finally:
        await pool.stop()


if __name__ == "__main__":
    asyncio.run(main())
