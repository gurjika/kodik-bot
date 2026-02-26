import json
import logging
from typing import Any

import redis.asyncio as aioredis
from config import get_settings

logger = logging.getLogger(__name__)

QUEUE_KEY = "job_queue"
ADMIN_PENDING_PREFIX = "admin_pending:"
ADMIN_PENDING_TTL = 60 * 60 * 72  # 72 hours — drop stale escalations


def _client() -> aioredis.Redis:
    """Return a module-level shared async Redis client (connection pooled)."""
    return aioredis.from_url(get_settings().QUEUE_REDIS_URL, decode_responses=True)

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = _client()
    return _redis

async def enqueue_new_message(
    user_id: int,
    chat_id: int,
    thread_id: str,
    text: str,
    reply_message_id: int | None = None,
    is_admin_chat: bool = False,
) -> None:
    job: dict = {
        "type": "new",
        "user_id": user_id,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "text": text,
    }
    if reply_message_id is not None:
        job["reply_message_id"] = reply_message_id
    if is_admin_chat:
        job["is_admin_chat"] = True
    await get_redis().rpush(QUEUE_KEY, json.dumps(job))
    logger.debug("Enqueued new message job for thread %s", thread_id)


async def enqueue_resume(
    thread_id: str, user_chat_id: int, human_reply: str
) -> None:
    job = {
        "type": "resume",
        "thread_id": thread_id,
        "user_chat_id": user_chat_id,
        "human_reply": human_reply,
    }
    await get_redis().rpush(QUEUE_KEY, json.dumps(job))
    logger.debug("Enqueued resume job for thread %s", thread_id)


async def dequeue_job(timeout: int = 5) -> dict[str, Any] | None:
    """
    Blocking left-pop with timeout. Returns parsed job dict or None on timeout.
    Uses BLPOP which yields the CPU to the event loop while waiting.
    """
    result = await get_redis().blpop(QUEUE_KEY, timeout=timeout)
    if result is None:
        return None
    _, raw = result
    return json.loads(raw)

GROUP_CHATS_KEY = "group_chats"
GROUP_BUFFER_PREFIX = "group_buffer:"
GROUP_SEEN_PREFIX = "group_seen:"
GROUP_SEEN_TTL = 60 * 60 * 24
GROUP_BUFFER_MAX = 200
GROUP_BUFFER_TTL = 60 * 60 * 24


async def group_buffer_push(
    chat_id: int, message_id: int, user_id: int, text: str
) -> None:
    """Append a message to the chat's pending buffer and register the chat.

    Caps the list at GROUP_BUFFER_MAX entries (oldest dropped first) and
    resets a 24-hour TTL so buffers for quiet chats don't linger forever.
    """
    key = GROUP_BUFFER_PREFIX + str(chat_id)
    payload = json.dumps({"message_id": message_id, "user_id": user_id, "text": text})
    pipe = get_redis().pipeline()
    pipe.rpush(key, payload)
    pipe.ltrim(key, -GROUP_BUFFER_MAX, -1)
    pipe.expire(key, GROUP_BUFFER_TTL)
    pipe.sadd(GROUP_CHATS_KEY, str(chat_id))
    await pipe.execute()


async def group_buffer_pop_batch(chat_id: int, size: int) -> list[dict]:
    """Atomically take up to `size` items from the front of the buffer.

    Removes the chat from the group_chats registry when the buffer is fully
    drained so the scheduler skips dead chats on the next cycle.
    """
    r = get_redis()
    key = GROUP_BUFFER_PREFIX + str(chat_id)
    pipe = r.pipeline()
    pipe.lrange(key, 0, size - 1)
    pipe.llen(key)
    raw_items, total = await pipe.execute()
    if raw_items:
        remaining = total - len(raw_items)
        if remaining <= 0:
            await r.srem(GROUP_CHATS_KEY, str(chat_id))
        else:
            await r.ltrim(key, len(raw_items), -1)
    return [json.loads(item) for item in raw_items]


async def group_seen_check(chat_id: int, message_id: int) -> bool:
    """Return True if this message_id has already been processed."""
    return bool(
        await get_redis().sismember(GROUP_SEEN_PREFIX + str(chat_id), message_id)
    )


async def group_seen_add(chat_id: int, message_id: int) -> None:
    """Mark a message_id as seen; refreshes the 24-hour TTL."""
    key = GROUP_SEEN_PREFIX + str(chat_id)
    r = get_redis()
    await r.sadd(key, message_id)
    await r.expire(key, GROUP_SEEN_TTL)


async def group_get_chat_ids() -> list[int]:
    """Return all chat IDs that have ever had a message buffered."""
    members = await get_redis().smembers(GROUP_CHATS_KEY)
    return [int(m) for m in members]


async def enqueue_admin_reply(
    thread_id: str, user_chat_id: int, user_id: int, admin_reply_text: str
) -> None:
    """Push an admin_reply job onto the worker queue."""
    job = {
        "type": "admin_reply",
        "thread_id": thread_id,
        "chat_id": user_chat_id,
        "user_id": user_id,
        "admin_reply": admin_reply_text,
    }
    await get_redis().rpush(QUEUE_KEY, json.dumps(job))
    logger.debug("Enqueued admin_reply job for thread %s", thread_id)


async def set_admin_pending(
    admin_msg_id: int, thread_id: str, user_chat_id: int, user_id: int, escalation_question: str
) -> None:
    """Store the mapping from admin group message_id → thread context."""
    key = ADMIN_PENDING_PREFIX + str(admin_msg_id)
    data = {
        "thread_id": thread_id,
        "user_chat_id": str(user_chat_id),
        "user_id": str(user_id),
        "question": escalation_question,
    }
    r = get_redis()
    await r.hset(key, mapping=data)
    await r.expire(key, ADMIN_PENDING_TTL)
    logger.debug("Stored admin pending for msg_id=%s thread=%s", admin_msg_id, thread_id)


async def get_and_delete_admin_pending(
    admin_msg_id: int,
) -> dict[str, str] | None:
    """
    Retrieve and atomically delete the pending entry for admin_msg_id.
    Returns None if the key doesn't exist (e.g. already handled or expired).
    """
    key = ADMIN_PENDING_PREFIX + str(admin_msg_id)
    r = get_redis()
    data = await r.hgetall(key)
    if not data:
        return None
    await r.delete(key)
    return data
