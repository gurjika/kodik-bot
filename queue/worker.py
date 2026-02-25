"""
Async worker pool.

Each worker is a long-running coroutine that:
  1. Blocks on Redis BLPOP (yields CPU while waiting — no spin loop)
  2. Deserializes the job
  3. Dispatches to _handle_new or _handle_resume
  4. Loops back immediately

All workers run concurrently in the same event loop as the bot polling.
There is no thread overhead — pure async I/O multiplexing.

Job dispatch
------------
"new"    → run graph from the beginning for this thread
"resume" → resume a suspended graph (ask_human interrupt) with admin answer
"""

import asyncio
import logging
from typing import Any

from langgraph.graph.state import CompiledStateGraph
import telebot.async_telebot as async_telebot
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.types import Command

from storage.redis_store import dequeue_job, set_admin_pending
from config import get_settings

logger = logging.getLogger(__name__)


class WorkerPool:
    """
    Owns the graph + bot references and spawns NUM_WORKERS async coroutines
    that drain the Redis job queue. All state is instance-level — no globals.
    """

    def __init__(self, graph: CompiledStateGraph, bot: async_telebot.AsyncTeleBot, num_workers: int) -> None:
        self._graph = graph
        self._bot = bot
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
    def _make_config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    async def _handle_new(self, job: dict[str, Any]) -> None:
        """Process a brand-new message from a user."""
        chat_id: int = job["chat_id"]
        thread_id: str = job["thread_id"]
        text: str = job["text"]
        config = self._make_config(thread_id)

        initial_state = {
            "messages": [HumanMessage(content=text)],
            "user_chat_id": chat_id,
            "user_id": job["user_id"],
            "thread_id": thread_id,
        }

        final_ai_message: str | None = None

        result = await self._graph.ainvoke(initial_state, config)
        messages = result.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and not msg.tool_calls:
                final_ai_message = msg.content
                break

        # Check if the graph was interrupted (ask_human was called)
        state = await self._graph.aget_state(config)
        if state.next:
            interrupted_question: str | None = None
            for task in state.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    interrupted_question = task.interrupts[0].value
                    break

            if interrupted_question:
                await self._notify_admin_escalation(
                    thread_id=thread_id,
                    user_chat_id=chat_id,
                    question=interrupted_question,
                )
                await self._bot.send_message(
                    chat_id,
                    "Your question requires input from our team. "
                    "I'll get back to you shortly! ⏳",
                )
                return

        if final_ai_message:
            await self._bot.send_message(chat_id, final_ai_message)
        else:
            logger.warning("No AI response produced for thread %s", thread_id)

    async def _handle_resume(self, job: dict[str, Any]) -> None:
        """Resume a suspended graph with the admin's answer."""
        thread_id: str = job["thread_id"]
        user_chat_id: int = job["user_chat_id"]
        human_reply: str = job["human_reply"]
        config = self._make_config(thread_id)

        final_ai_message: str | None = None

        result = await self._graph.ainvoke(Command(resume=human_reply), config)
        messages = result.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and not msg.tool_calls:
                final_ai_message = msg.content
                break

        if final_ai_message:
            await self._bot.send_message(user_chat_id, final_ai_message)
        else:
            logger.warning("No AI response after resume for thread %s", thread_id)

    async def _notify_admin_escalation(
        self, thread_id: str, user_chat_id: int, question: str
    ) -> None:
        """Send the escalation message to the admin group and store the mapping."""
        settings = get_settings()
        text = (
            f"🔔 *Admin input needed*\n\n"
            f"*Thread:* `{thread_id}`\n\n"
            f"*Question from agent:*\n{question}\n\n"
            f"_Reply to this message to send your answer back to the user._"
        )
        sent = await self._bot.send_message(
            settings.ADMIN_GROUP_ID,
            text,
            parse_mode="Markdown",
        )
        await set_admin_pending(
            admin_msg_id=sent.message_id,
            thread_id=thread_id,
            user_chat_id=user_chat_id,
            escalation_question=question,
        )
        logger.info(
            "Escalation sent to admin group, msg_id=%s thread=%s",
            sent.message_id,
            thread_id,
        )

    async def _worker_loop(self, worker_id: int) -> None:
        """Single worker coroutine. Runs forever until cancelled."""
        logger.info("Worker %d started", worker_id)
        while True:
            try:
                job = await dequeue_job(timeout=5)
                if job is None:
                    continue  # BLPOP timeout — loop back

                job_type = job.get("type")
                if job_type == "new":
                    await self._handle_new(job)
                elif job_type == "resume":
                    await self._handle_resume(job)
                else:
                    logger.warning("Worker %d: unknown job type %r", worker_id, job_type)

            except asyncio.CancelledError:
                logger.info("Worker %d cancelled", worker_id)
                break
            except Exception:
                # Log and continue — a single bad job must not kill the worker
                logger.exception("Worker %d: unhandled error processing job", worker_id)
