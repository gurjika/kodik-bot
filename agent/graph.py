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
2. If the knowledge base doesn't have enough information, use ask_human to
   escalate to a human admin — provide a clear, self-contained question.
3. Be concise and friendly in your final answers.
4. Never fabricate information that isn't in the knowledge base or provided
   by a human admin.
"""


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
        if not any(m.type == "system" for m in messages):
            from langchain_core.messages import SystemMessage
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)
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
