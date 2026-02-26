import json
import logging

import telebot.async_telebot as async_telebot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from langchain_openai import ChatOpenAI

from config import get_settings
from storage.redis_store import (
    group_buffer_pop_batch,
    group_buffer_push,
    group_get_chat_ids,
    group_seen_add,
    group_seen_check,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
INTERVAL_SECONDS = 120

_scheduler = AsyncIOScheduler()


def _build_llm() -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(model=s.OPENAI_MODEL, api_key=s.OPENAI_API_KEY, temperature=0)


_SYSTEM = (
    "You are a strict classifier. You will receive a list of Telegram messages from a group chat "
    "about Kodik — an AI code editor.\n\n"
    "Your task is to identify ONLY messages that contain a CLEAR, DETAILED bug description — "
    "meaning the user explicitly describes what went wrong, what they expected, or specific "
    "steps to reproduce the issue.\n\n"
    "DO NOT flag:\n"
    "- Vague statements like 'I found a bug', 'something is broken', 'I know one bug'\n"
    "- Messages that merely mention bugs without describing them\n"
    "- General complaints without specifics\n"
    "- Feature requests or questions\n\n"
    "Respond ONLY with a JSON array containing AT MOST ONE message ID — the single best, "
    "most detailed bug report in the batch. If none qualify, respond with an empty array: [].\n"
    "Messages may be in Russian or English."
)


async def _analyze_batch(messages: list[dict]) -> list[int]:
    """Ask the LLM which message IDs in the batch are bug/support related."""
    formatted = "\n".join(
        f"[id={m['message_id']}] {m['text'][:300]}" for m in messages
    )
    prompt = f"Messages:\n{formatted}\n\nReturn JSON array of qualifying message IDs."

    llm = _build_llm()
    response = await llm.ainvoke([
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": prompt},
    ])

    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return [int(x) for x in result]
    except (json.JSONDecodeError, ValueError):
        logger.warning("LLM returned non-JSON for group analysis: %r", raw[:200])
    return []


_REPLY_SYSTEM = (
    "You are the support bot for Kodik — an AI code editor. "
    "A user posted a bug report in a group chat. Write a short, friendly reply (2-4 sentences) "
    "in Russian regardless of the language the user wrote in. "
    "Briefly acknowledge the specific issue they described, "
    "then invite them to message you directly for more help. "
    "Mention that they can write to @{bot_username}. Do NOT use markdown formatting."
)


async def _generate_reply(bug_text: str, bot_username: str) -> str:
    llm = _build_llm()
    response = await llm.ainvoke([
        {"role": "system", "content": _REPLY_SYSTEM.format(bot_username=bot_username)},
        {"role": "user", "content": bug_text[:500]},
    ])
    return response.content.strip()


async def _scan_and_reply(
    bot_instance: async_telebot.AsyncTeleBot, bot_username: str
) -> None:
    """APScheduler job: pull batches from Redis and reply to bug reports."""
    chat_ids = await group_get_chat_ids()
    for chat_id in chat_ids:
        batch = await group_buffer_pop_batch(chat_id, BATCH_SIZE)
        if not batch:
            continue

        try:
            bug_ids = await _analyze_batch(batch)
        except Exception:
            logger.exception("Error analyzing batch for chat %s", chat_id)
            continue

        if not bug_ids:
            continue

        target_id = bug_ids[0]
        target_msg = next((m for m in batch if m["message_id"] == target_id), None)
        if target_msg is None:
            continue

        try:
            reply = await _generate_reply(target_msg["text"], bot_username)
            await bot_instance.send_message(
                chat_id, reply, reply_to_message_id=target_msg["message_id"]
            )
            logger.info(
                "Replied to bug report msg_id=%s in chat %s",
                target_msg["message_id"], chat_id,
            )
        except Exception:
            logger.exception(
                "Failed to reply to msg %s in chat %s", target_msg["message_id"], chat_id,
            )


def register_group_handlers(
    bot_instance: async_telebot.AsyncTeleBot,
    bot_username: str,
) -> None:
    settings = get_settings()
    admin_gid = settings.ADMIN_GROUP_ID

    @bot_instance.message_handler(
        func=lambda m: (
            m.chat.type in ("group", "supergroup")
            and m.chat.id != admin_gid
            and m.text is not None
            and not any(
                e.type == "mention"
                and m.text[e.offset: e.offset + e.length].lstrip("@").lower() == bot_username.lower()
                for e in (m.entities or [])
            )
        )
    )
    async def collect_group_message(message: async_telebot.types.Message) -> None:
        chat_id = message.chat.id
        msg_id = message.message_id

        if await group_seen_check(chat_id, msg_id):
            return
        await group_seen_add(chat_id, msg_id)
        await group_buffer_push(chat_id, msg_id, message.from_user.id, message.text or "")

    _scheduler.add_job(
        _scan_and_reply,
        "interval",
        seconds=INTERVAL_SECONDS,
        args=[bot_instance, bot_username],
        id="group-monitor",
        replace_existing=True,
    )
    if not _scheduler.running:
        _scheduler.start()
    logger.info("Group monitor started (batch=%d, interval=%ds)", BATCH_SIZE, INTERVAL_SECONDS)
