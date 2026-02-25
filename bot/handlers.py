"""
User-facing message handler.

Every message from a non-admin chat is pushed onto the Redis job queue
immediately. The handler returns in microseconds; processing happens
in the async worker pool (queue/worker.py).
"""

import logging
import telebot.async_telebot as async_telebot
from config import get_settings
from storage.redis_store import enqueue_new_message

logger = logging.getLogger(__name__)


def register_user_handlers(bot: async_telebot.AsyncTeleBot) -> None:
    settings = get_settings()
    admin_gid = settings.ADMIN_GROUP_ID

    @bot.message_handler(
        func=lambda m: m.chat.id != admin_gid and m.text is not None
    )
    async def handle_user_message(message: async_telebot.types.Message) -> None:
        user_id = message.from_user.id
        chat_id = message.chat.id
        thread_id = f"user_{user_id}"
        text = message.text.strip()

        logger.info("User %s sent: %r", user_id, text[:80])

        # Acknowledge receipt so the user isn't left staring at nothing
        await bot.send_chat_action(chat_id, "typing")

        await enqueue_new_message(
            user_id=user_id,
            chat_id=chat_id,
            thread_id=thread_id,
            text=text,
        )
