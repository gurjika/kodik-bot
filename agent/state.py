from typing import Annotated
from langgraph.graph import MessagesState


class AgentState(MessagesState):
    user_chat_id: int
    user_id: int
    thread_id: str
