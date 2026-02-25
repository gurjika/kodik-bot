import logging
from datetime import datetime, timezone

import telebot.async_telebot as async_telebot
from sqlalchemy import select

from config import get_settings
from storage.redis_store import get_and_delete_admin_pending
from storage.database import get_session, Escalation
from bot.instance import bot

logger = logging.getLogger(__name__)


def register_admin_handlers(bot_instance: async_telebot.AsyncTeleBot) -> None:
    settings = get_settings()
    admin_gid = settings.ADMIN_GROUP_ID

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

        await bot.send_message(
            user_chat_id, 
            f"*Support team reply:*\n{message.text.strip()}",
            parse_mode="Markdown",
        )
        logger.info("Admin reply forwarded to user_chat=%s", user_chat_id)

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

        await bot_instance.reply_to(message, "✓ Reply sent to user.")
