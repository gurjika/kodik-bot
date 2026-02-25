"""
Entry point.

Starts:
  - Telegram bot polling (non-blocking async)
  - NUM_WORKERS async worker coroutines draining the Redis job queue

All coroutines share one event loop — no threading, no multiprocessing.
To scale beyond one event loop, run multiple instances of this process
(they all share the same Redis queue and LangGraph Redis checkpointer).
"""

import asyncio
import logging
import sys

import telebot.async_telebot as async_telebot

from config import get_settings
from agent.graph import create_graph
from bot.handlers import register_user_handlers
from bot.admin import register_admin_handlers
from queue.worker import WorkerPool


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def main() -> None:
    settings = get_settings()
    _configure_logging(settings.LOG_LEVEL)
    logger = logging.getLogger(__name__)

    logger.info("Starting kodik-bot")
    logger.info("Workers: %d | Model: %s", settings.NUM_WORKERS, settings.OPENAI_MODEL)

    # Create the LangGraph agent (connects to Redis for checkpointing)
    graph = await create_graph()
    logger.info("LangGraph agent ready")

    # Create the Telegram bot instance
    bot = async_telebot.AsyncTeleBot(settings.TELEGRAM_BOT_TOKEN)

    # Register message handlers
    register_user_handlers(bot)
    register_admin_handlers(bot)

    # Create and start the worker pool
    pool = WorkerPool(graph=graph, bot=bot, num_workers=settings.NUM_WORKERS)
    pool.start()

    try:
        # Bot polling runs concurrently with all workers in the same event loop.
        # non_stop=True ensures polling restarts on Telegram API errors.
        await bot.polling(non_stop=True, timeout=30)
    finally:
        await pool.stop()


if __name__ == "__main__":
    asyncio.run(main())
