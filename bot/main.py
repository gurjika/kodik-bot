import asyncio
import logging
import sys
from colorama import Fore, Style, init as colorama_init
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


_LEVEL_COLORS = {
    "DEBUG":    Fore.CYAN,
    "INFO":     Fore.GREEN,
    "WARNING":  Fore.YELLOW,
    "ERROR":    Fore.RED,
    "CRITICAL": Fore.MAGENTA,
}


class _ColorFormatter(logging.Formatter):
    _FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelname, "")
        record.levelname = f"{color}{record.levelname}{Style.RESET_ALL}"
        record.name = f"{Fore.BLUE}{record.name}{Style.RESET_ALL}"
        return super().format(record)


def _configure_logging(level: str) -> None:
    colorama_init(autoreset=True)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ColorFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
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
