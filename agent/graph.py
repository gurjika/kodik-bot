import logging
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.graph.state import CompiledStateGraph
from agent.state import AgentState
from agent.tools import search_knowledge_base, ask_human
from config import get_settings

logger = logging.getLogger(__name__)

TOOLS = [search_knowledge_base, ask_human]

SYSTEM_PROMPT = """
You are a helpful support assistant for Kodik.
Kodik is an AI-powered code editor that understands your codebase and 
helps you code faster using natural language.
Simply describe what you want to create or change, 
and Kodik will generate the code for you.

You can download kodik here https://aikodik.ru/ or https://vibekodik.ru/.

When answering user questions:
1. Always try search_knowledge_base first.
2. If the knowledge base doesn't have enough information:
   a. Tell the user honestly that you don't have enough information to answer.
   b. Ask if they would like you to escalate the question to a human support agent.
   c. Only call ask_human if the user explicitly confirms they want to escalate.
   d. If the user says no or changes subject, do not escalate.
3. Always respond in Russian, regardless of the language the user writes in.
4. Be concise and friendly in your final answers.
5. Never fabricate information that isn't in the knowledge base or provided
   by a human admin.

Formatting rules (Telegram Markdown):
- Bold: *text*  (single asterisk, NOT double)
- Italic: _text_  (single underscore, NOT double)
- Inline code: `code`
- Do NOT use **text**, __text__, or any other Markdown variants.
- Do NOT use headers (# H1, ## H2, etc.).
"""

ADMIN_ADDENDUM = """

You are currently responding in the ADMIN group chat.
The people here are the support team / developers of Kodik.
You can be more technical and detailed in your answers.
Do NOT use ask_human here — the admins ARE the humans.
If asked about escalations or tickets, say you don't have direct DB access
and suggest using the /tickets command.
Same formatting rules apply: use Telegram Markdown (*bold*, _italic_, `code`).
"""

MESSAGE_WINDOW = 30


def _build_graph(checkpointer):
    """Build and compile the StateGraph with the given checkpointer."""
    settings = get_settings()

    llm = ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        streaming=False,
    ).bind_tools(TOOLS)

    tool_node = ToolNode(TOOLS)

    def agent_node(state: AgentState):
        messages = state["messages"]
        is_admin = state.get("is_admin_chat", False)

        messages = list(messages)[-MESSAGE_WINDOW:]

        from langchain_core.messages import SystemMessage
        prompt = SYSTEM_PROMPT + (ADMIN_ADDENDUM if is_admin else "")
        messages = [SystemMessage(content=prompt)] + messages

        response = llm.invoke(messages)
        return {"messages": [response]}

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")

    graph = builder.compile(checkpointer=checkpointer)
    logger.info("LangGraph compiled successfully")
    return graph


async def create_graph() -> CompiledStateGraph:
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    from config import get_settings

    settings = get_settings()
    checkpointer_cm = AsyncRedisSaver.from_conn_string(settings.REDIS_URL)
    checkpointer = await checkpointer_cm.__aenter__()
    await checkpointer.asetup()
    
    return _build_graph(checkpointer)
