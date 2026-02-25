import logging
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)


@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the knowledge base for information relevant to the user's question.
    Use this tool first before escalating to a human admin.
    Returns the most relevant entries found, or a 'not found' message.
    """
    from knowledge_base.retriever import search_kb

    logger.debug("KB search: %r", query)
    result = search_kb(query)
    logger.debug("KB result: %r", result)
    return result


@tool
async def ask_human(question: str, config: RunnableConfig) -> str:
    """
    Escalate a question to a human admin when the knowledge base does not
    contain sufficient information and you cannot confidently answer.
    Provide a clear, self-contained question for the admin.
    The user will be notified that their question has been escalated.
    """
    from storage.redis_store import set_admin_pending
    from config import get_settings
    from bot.instance import bot

    settings = get_settings()
    configurable = config.get("configurable", {})
    thread_id: str = configurable["thread_id"]
    user_chat_id: int = configurable["user_chat_id"]

    text = (
        f"🔔 *Admin input needed*\n\n"
        f"*Thread:* `{thread_id}`\n\n"
        f"*Question from agent:*\n{question}\n\n"
        f"_Reply to this message to send your answer directly to the user._"
    )
    sent = await bot.send_message(
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

    from storage.database import get_session, Escalation

    async with get_session() as session:
        session.add(Escalation(
            thread_id=thread_id,
            user_chat_id=user_chat_id,
            question=question,
            admin_msg_id=sent.message_id,
        ))
        await session.commit()

    logger.info(
        "Escalation sent to admin group, msg_id=%s thread=%s",
        sent.message_id,
        thread_id,
    )
    return (
        "The question has been escalated to our support team. "
        "They will reply to the user directly and shortly."
    )
