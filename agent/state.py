from typing import Annotated
from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """
    State carried through every node in the LangGraph agent.

    Inherits `messages: Annotated[list, add_messages]` from MessagesState.
    The extra fields are written once at graph entry and never mutated.
    """

    # Telegram chat to reply to when the graph finishes
    user_chat_id: int
    # Telegram user id (used as part of the thread_id key)
    user_id: int
    # LangGraph thread_id (set in config, mirrored here for convenience)
    thread_id: str
