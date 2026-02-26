import logging
import re
import telebot.async_telebot as async_telebot
from config import get_settings
from storage.redis_store import enqueue_new_message
from storage.database import get_or_create_thread_id, reset_thread_id

logger = logging.getLogger(__name__)


def _is_bot_mentioned(message: async_telebot.types.Message, bot_username: str) -> bool:
    """True if the message @mentions the bot or is a direct reply to the bot."""
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                span = message.text[entity.offset: entity.offset + entity.length]
                if span.lstrip("@").lower() == bot_username.lower():
                    return True
    if (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.username
        and message.reply_to_message.from_user.username.lower() == bot_username.lower()
    ):
        return True
    return False


def _strip_mention(text: str, bot_username: str) -> str:
    """Remove @BotUsername from message text."""
    return re.sub(rf"@{re.escape(bot_username)}\s*", "", text, flags=re.IGNORECASE).strip()


def register_user_handlers(bot: async_telebot.AsyncTeleBot, bot_username: str = "") -> None:
    settings = get_settings()
    admin_gid = settings.ADMIN_GROUP_ID

    @bot.message_handler(
        commands=["restart"],
        func=lambda m: m.chat.type == "private",
    )
    async def handle_restart(message: async_telebot.types.Message) -> None:
        user_id = message.from_user.id
        await reset_thread_id(user_id)
        logger.info("User %s reset conversation", user_id)
        await bot.reply_to(message, "✅ Разговор сброшен. Начинаем с чистого листа!")

    @bot.message_handler(
        func=lambda m: m.chat.type == "private" and m.text is not None
    )
    async def handle_user_message(message: async_telebot.types.Message) -> None:
        user_id = message.from_user.id
        chat_id = message.chat.id
        thread_id = await get_or_create_thread_id(user_id)
        text = message.text.strip()

        logger.info("User %s sent: %r (thread=%s)", user_id, text[:80], thread_id)

        await bot.send_chat_action(chat_id, "typing")

        await enqueue_new_message(
            user_id=user_id,
            chat_id=chat_id,
            thread_id=thread_id,
            text=text,
        )

    @bot.message_handler(
        func=lambda m: (
            m.chat.type in ("group", "supergroup")
            and m.chat.id != admin_gid
            and m.text is not None
            and bot_username
            and _is_bot_mentioned(m, bot_username)
        )
    )
    async def handle_group_mention(message: async_telebot.types.Message) -> None:
        user_id = message.from_user.id
        chat_id = message.chat.id
        thread_id = await get_or_create_thread_id(user_id)
        text = _strip_mention(message.text, bot_username) or message.text.strip()

        logger.info(
            "Group mention from user %s in chat %s: %r (thread=%s)",
            user_id, chat_id, text[:80], thread_id,
        )

        await bot.send_chat_action(chat_id, "typing")

        await enqueue_new_message(
            user_id=user_id,
            chat_id=chat_id,
            thread_id=thread_id,
            text=text,
            reply_message_id=message.message_id,
        )
