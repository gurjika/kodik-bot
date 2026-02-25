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
    user_id: int, chat_id: int, thread_id: str, text: str
) -> None:
    job = {
        "type": "new",
        "user_id": user_id,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "text": text,
    }
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

async def set_admin_pending(
    admin_msg_id: int, thread_id: str, user_chat_id: int, escalation_question: str
) -> None:
    """Store the mapping from admin group message_id → thread context."""
    key = ADMIN_PENDING_PREFIX + str(admin_msg_id)
    data = {
        "thread_id": thread_id,
        "user_chat_id": str(user_chat_id),
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
