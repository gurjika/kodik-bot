import asyncio
import logging
import re
import telebot.async_telebot as async_telebot
from config import get_settings
from storage.redis_store import enqueue_new_message
from storage.database import get_or_create_thread_id, reset_thread_id

logger = logging.getLogger(__name__)

_media_groups: dict[str, list] = {}
_media_group_tasks: dict[str, asyncio.Task] = {}


def _is_bot_mentioned(message: async_telebot.types.Message, bot_username: str) -> bool:
    """True if the message @mentions the bot or is a direct reply to the bot."""
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    for entity in entities:
        if entity.type == "mention":
            span = text[entity.offset: entity.offset + entity.length]
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

    async def _process_media_group(media_group_id: str, is_group: bool = False) -> None:
        """Wait briefly for all images in a media group, then enqueue."""
        await asyncio.sleep(1.0)
        messages = _media_groups.pop(media_group_id, [])
        _media_group_tasks.pop(media_group_id, None)
        if not messages:
            return
        messages.sort(key=lambda m: m.message_id)

        caption = None
        for msg in messages:
            if msg.caption:
                cap = msg.caption.strip()
                if is_group and bot_username:
                    cap = _strip_mention(cap, bot_username) or cap
                caption = cap
                break

        file_ids = [msg.photo[-1].file_id for msg in messages if msg.photo]
        if not file_ids:
            return

        first_msg = messages[0]
        user_id = first_msg.from_user.id
        chat_id = first_msg.chat.id
        thread_id = await get_or_create_thread_id(user_id)
        text = caption or "Пользователь отправил изображение(я) без текста."

        logger.info(
            "Media group %s: %d image(s) from user %s (thread=%s)",
            media_group_id, len(file_ids), user_id, thread_id,
        )
        await bot.send_chat_action(chat_id, "typing")
        await enqueue_new_message(
            user_id=user_id,
            chat_id=chat_id,
            thread_id=thread_id,
            text=text,
            image_file_ids=file_ids,
            reply_message_id=first_msg.message_id if is_group else None,
        )

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

    @bot.message_handler(
        content_types=["photo"],
        func=lambda m: m.chat.type == "private",
    )
    async def handle_user_photo(message: async_telebot.types.Message) -> None:
        if message.media_group_id:
            mgid = message.media_group_id
            _media_groups.setdefault(mgid, []).append(message)
            if mgid not in _media_group_tasks:
                _media_group_tasks[mgid] = asyncio.create_task(
                    _process_media_group(mgid, is_group=False)
                )
            return

        user_id = message.from_user.id
        chat_id = message.chat.id
        thread_id = await get_or_create_thread_id(user_id)
        text = (message.caption or "").strip() or "Пользователь отправил изображение без текста."
        file_ids = [message.photo[-1].file_id]

        logger.info("User %s sent photo (thread=%s)", user_id, thread_id)
        await bot.send_chat_action(chat_id, "typing")
        await enqueue_new_message(
            user_id=user_id,
            chat_id=chat_id,
            thread_id=thread_id,
            text=text,
            image_file_ids=file_ids,
        )

    @bot.message_handler(
        content_types=["photo"],
        func=lambda m: (
            m.chat.type in ("group", "supergroup")
            and m.chat.id != admin_gid
        ),
    )
    async def handle_group_photo(message: async_telebot.types.Message) -> None:
        mgid = message.media_group_id
        if mgid and mgid in _media_groups:
            _media_groups[mgid].append(message)
            return

        if not (bot_username and _is_bot_mentioned(message, bot_username)):
            return

        if mgid:
            _media_groups[mgid] = [message]
            _media_group_tasks[mgid] = asyncio.create_task(
                _process_media_group(mgid, is_group=True)
            )
        else:
            user_id = message.from_user.id
            chat_id = message.chat.id
            thread_id = await get_or_create_thread_id(user_id)
            caption = (message.caption or "").strip()
            if bot_username:
                caption = _strip_mention(caption, bot_username) or caption
            text = caption or "Пользователь отправил изображение без текста."

            logger.info(
                "Group photo from user %s in chat %s (thread=%s)",
                user_id, chat_id, thread_id,
            )
            await bot.send_chat_action(chat_id, "typing")
            await enqueue_new_message(
                user_id=user_id,
                chat_id=chat_id,
                thread_id=thread_id,
                text=text,
                image_file_ids=[message.photo[-1].file_id],
                reply_message_id=message.message_id,
            )
