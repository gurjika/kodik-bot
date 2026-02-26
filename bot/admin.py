import logging
import re
from datetime import datetime, timezone
import telebot.async_telebot as async_telebot
from sqlalchemy import select, func
from config import get_settings
from storage.redis_store import get_and_delete_admin_pending, enqueue_new_message, enqueue_admin_reply
from storage.database import get_session, Escalation, get_or_create_thread_id
from bot.instance import bot

logger = logging.getLogger(__name__)


def _is_bot_mentioned(message: async_telebot.types.Message, bot_username: str) -> bool:
    if not message.entities:
        return False
    for entity in message.entities:
        if entity.type == "mention":
            span = message.text[entity.offset: entity.offset + entity.length]
            if span.lstrip("@").lower() == bot_username.lower():
                return True
    return False


def _strip_mention(text: str, bot_username: str) -> str:
    return re.sub(rf"@{re.escape(bot_username)}\s*", "", text, flags=re.IGNORECASE).strip()



def register_admin_handlers(
    bot_instance: async_telebot.AsyncTeleBot,
    bot_username: str = "",
) -> None:
    settings = get_settings()
    admin_gid = settings.ADMIN_GROUP_ID

    @bot_instance.message_handler(
        func=lambda m: (
            m.chat.id == admin_gid
            and m.text is not None
            and bool(bot_username)
            and _is_bot_mentioned(m, bot_username)
        )
    )
    async def handle_admin_mention(message: async_telebot.types.Message) -> None:
        """Admin group @mention — invoke the agent with admin context."""
        user_id = message.from_user.id
        chat_id = message.chat.id
        thread_id = await get_or_create_thread_id(user_id)
        text = _strip_mention(message.text, bot_username) or message.text.strip()

        logger.info(
            "Admin mention from user %s: %r (thread=%s)",
            user_id, text[:80], thread_id,
        )

        await bot_instance.send_chat_action(chat_id, "typing")

        await enqueue_new_message(
            user_id=user_id,
            chat_id=chat_id,
            thread_id=thread_id,
            text=text,
            reply_message_id=message.message_id,
            is_admin_chat=True,
        )

    @bot_instance.message_handler(
        func=lambda m: (
            m.chat.id == admin_gid
            and m.reply_to_message is not None
            and m.text is not None
        )
    )
    async def handle_admin_reply(message: async_telebot.types.Message) -> None:
        replied_to_id = message.reply_to_message.message_id
        logger.info(
            "Admin replied to msg_id=%s: %r", replied_to_id, message.text[:80]
        )

        pending = await get_and_delete_admin_pending(replied_to_id)
        if pending is None:
            logger.debug("No pending escalation for msg_id=%s", replied_to_id)
            return

        user_chat_id = int(pending["user_chat_id"])
        user_id = int(pending.get("user_id", 0))

        await enqueue_admin_reply(
            thread_id=pending["thread_id"],
            user_chat_id=user_chat_id,
            user_id=user_id,
            admin_reply_text=message.text.strip(),
        )
        logger.info("Admin reply enqueued for user_chat=%s thread=%s", user_chat_id, pending["thread_id"])

        async with get_session() as session:
            result = await session.execute(
                select(Escalation).where(
                    Escalation.admin_msg_id == replied_to_id
                )
            )
            esc = result.scalar_one_or_none()
            if esc is not None:
                esc.admin_reply = message.text.strip()
                esc.status = "resolved"
                esc.resolved_at = datetime.now(timezone.utc)
                await session.commit()

        await bot_instance.reply_to(message, "✓ Ответ отправлен пользователю.")
