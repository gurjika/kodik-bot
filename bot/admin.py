"""
Admin group reply handler.

Flow:
  1. Agent calls ask_human(question) → sends message to admin group,
     stores admin_msg_id → {thread_id, user_chat_id} in Redis,
     graph suspends via interrupt().
  2. Admin replies to that specific message in the group.
  3. This handler detects the reply, retrieves the stored context,
     enqueues a "resume" job, and deletes the Redis key.
  4. A worker picks up the resume job and calls graph.astream(Command(resume=reply)).

The bot (in bot/main.py) is responsible for sending the escalation message
to the admin group and calling set_admin_pending() after it receives the
message_id from Telegram.
"""

import logging
import telebot.async_telebot as async_telebot
from config import get_settings
from storage.redis_store import get_and_delete_admin_pending, enqueue_resume

logger = logging.getLogger(__name__)


def register_admin_handlers(bot: async_telebot.AsyncTeleBot) -> None:
    settings = get_settings()
    admin_gid = settings.ADMIN_GROUP_ID

    @bot.message_handler(
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
            # Not a tracked escalation — could be a regular group message
            logger.debug("No pending escalation for msg_id=%s", replied_to_id)
            return

        thread_id = pending["thread_id"]
        user_chat_id = int(pending["user_chat_id"])

        await enqueue_resume(
            thread_id=thread_id,
            user_chat_id=user_chat_id,
            human_reply=message.text.strip(),
        )
        logger.info(
            "Queued resume for thread=%s user_chat=%s", thread_id, user_chat_id
        )

        # Optional: confirm to the admin that the reply will be forwarded
        await bot.reply_to(message, "✓ Reply forwarded to user.")
