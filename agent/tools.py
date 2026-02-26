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
    Provide a clear, self-contained question for the admin,
    written in the same language the user is speaking.
    The user will be notified that their question has been escalated.
    """
    from storage.redis_store import set_admin_pending
    from config import get_settings
    from bot.instance import bot

    settings = get_settings()
    configurable = config.get("configurable", {})
    thread_id: str = configurable["thread_id"]
    user_chat_id: int = configurable["user_chat_id"]
    user_id: int = configurable.get("user_id", 0)

    text = (
        f"🔔 *ТРЕБУЕТСЯ ОТВЕТ АДМИНИСТРАТОРА* 🔔"
        f"\n━━━━━━━━━━━━━━━━━━━━"
        f"\n*Тред:* `{thread_id}`"
        f"\n━━━━━━━━━━━━━━━━━━━━"
        f"\n*💬 Вопрос от агента:*"
        f"\n{question}"
        f"\n━━━━━━━━━━━━━━━━━━━━"
        f"\n_Ответьте на это сообщение, чтобы отправить ответ пользователю._"
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
        user_id=user_id,
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
        "Ваш вопрос передан команде поддержки. "
        "Они ответят вам напрямов в ближайшее время."
    )
