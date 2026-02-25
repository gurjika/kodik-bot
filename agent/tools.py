"""
Agent tools:

  search_knowledge_base  — searches the in-memory JSON knowledge base
  ask_human              — escalates a question to the admin Telegram group,
                           then suspends the graph via langgraph interrupt().
                           The graph is resumed by the admin reply handler
                           (see bot/admin.py) which calls Command(resume=answer).
"""

import logging
from langchain_core.tools import tool
from langgraph.types import interrupt

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
def ask_human(question: str) -> str:
    """
    Escalate a question to a human admin when the knowledge base does not
    contain sufficient information and you cannot confidently answer.
    The user will be notified that their question is being looked into.
    Provide a clear, self-contained question for the admin.
    """
    # interrupt() suspends the graph here and saves state to the checkpointer.
    # Execution resumes when Command(resume=<admin_reply>) is pushed by admin.py.
    # The return value of interrupt() is whatever the admin sends back.
    logger.info("Escalating to admin: %r", question)
    admin_answer: str = interrupt(question)
    logger.info("Admin replied: %r", admin_answer)
    return admin_answer
