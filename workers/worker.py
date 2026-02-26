import asyncio
import logging
from typing import Any
from langgraph.graph.state import CompiledStateGraph
from langchain_core.messages import HumanMessage, AIMessage
from storage.redis_store import dequeue_job
from storage.database import get_session, Message

logger = logging.getLogger(__name__)


class WorkerPool:
    """
    Owns the graph reference and spawns NUM_WORKERS async coroutines
    that drain the Redis job queue. All state is instance-level — no globals.
    Bot is accessed via bot.instance singleton set at startup.
    """

    def __init__(self, graph: CompiledStateGraph, num_workers: int) -> None:
        self._graph = graph
        self._num_workers = num_workers
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        """Spawn all worker coroutines as asyncio Tasks."""
        self._tasks = [
            asyncio.create_task(self._worker_loop(i), name=f"worker-{i}")
            for i in range(self._num_workers)
        ]
        logger.info("WorkerPool started (%d workers)", self._num_workers)

    async def stop(self) -> None:
        """Cancel all worker tasks and wait for them to finish."""
        logger.info("WorkerPool shutting down…")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("WorkerPool stopped")

    @staticmethod
    def _make_config(thread_id: str, user_chat_id: int, user_id: int = 0) -> dict:
        return {"configurable": {"thread_id": thread_id, "user_chat_id": user_chat_id, "user_id": user_id}}

    async def _handle_new(self, job: dict[str, Any]) -> None:
        """Process a brand-new message from a user."""
        from bot.instance import bot

        chat_id: int = job["chat_id"]
        thread_id: str = job["thread_id"]
        text: str = job["text"]
        config = self._make_config(thread_id, chat_id, user_id=job["user_id"])

        initial_state = {
            "messages": [HumanMessage(content=text)],
            "user_chat_id": chat_id,
            "user_id": job["user_id"],
            "thread_id": thread_id,
            "is_admin_chat": job.get("is_admin_chat", False),
        }

        final_ai_message: str | None = None

        result = await self._graph.ainvoke(initial_state, config)
        messages = result.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and not msg.tool_calls:
                final_ai_message = msg.content
                break

        async with get_session() as session:
            session.add(Message(
                user_id=job["user_id"],
                chat_id=chat_id,
                thread_id=thread_id,
                user_text=text,
                ai_response=final_ai_message,
            ))
            await session.commit()

        if final_ai_message:
            reply_to_id: int | None = job.get("reply_message_id")
            await bot.send_message(
                chat_id,
                final_ai_message,
                reply_to_message_id=reply_to_id,
                parse_mode="Markdown",
            )
        else:
            logger.warning("No AI response produced for thread %s", thread_id)

    async def _handle_admin_reply(self, job: dict[str, Any]) -> None:
        """Re-invoke the agent with the admin's answer so it relays it to the user."""
        from bot.instance import bot

        chat_id: int = job["chat_id"]
        thread_id: str = job["thread_id"]
        user_id: int = job["user_id"]
        admin_reply: str = job["admin_reply"]
        config = self._make_config(thread_id, chat_id, user_id=user_id)

        injected = HumanMessage(
            content=(
                f"[Ответ администратора на эскалированный вопрос]:\n"
                f"{admin_reply}\n\n"
                f"Передай этот ответ пользователю в понятной форме."
            )
        )
        state = {
            "messages": [injected],
            "user_chat_id": chat_id,
            "user_id": user_id,
            "thread_id": thread_id,
            "is_admin_chat": False,
        }

        final_ai_message: str | None = None
        result = await self._graph.ainvoke(state, config)
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and not msg.tool_calls:
                final_ai_message = msg.content
                break

        if final_ai_message:
            await bot.send_message(chat_id, f"✅ Ответ команды поддержки:\n━━━━━━━━━━━━━━━━━━━━\n{final_ai_message}", parse_mode="Markdown")
        else:
            logger.warning("No AI response for admin_reply, thread %s", thread_id)

    async def _worker_loop(self, worker_id: int) -> None:
        """Single worker coroutine. Runs forever until cancelled."""
        logger.info("Worker %d started", worker_id)
        while True:
            try:
                job = await dequeue_job(timeout=5)
                if job is None:
                    continue

                job_type = job.get("type")
                if job_type == "new":
                    await self._handle_new(job)
                elif job_type == "admin_reply":
                    await self._handle_admin_reply(job)
                else:
                    logger.warning("Worker %d: unknown job type %r", worker_id, job_type)

            except asyncio.CancelledError:
                logger.info("Worker %d cancelled", worker_id)
                break
            except Exception:
                logger.exception("Worker %d: unhandled error processing job", worker_id)
