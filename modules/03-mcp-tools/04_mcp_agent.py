"""Step 4: same agent, tools now come from a separate process over MCP.

Compare this file with 03_langgraph.py side by side. The graph is identical.
The node functions are identical. The prompt is identical. Exactly one line
differs:

    03:   from local_tools import TOOLS
    04:   TOOLS = load_mcp_tools()

That is the entire point of MCP, and it is worth sitting with. In step 3 the
tools were Python functions living inside the agent's own codebase. Here they
live in `mcp_server.py`, a separate program that is started as a subprocess and
talked to over a pipe. It imports no LangChain, no LangGraph, and has never
heard of an LLM.

What that buys, in the order people usually come to care about it:

  * **Other people's tools.** Anything that speaks MCP works with your agent,
    whether or not it is written in Python.
  * **Other agents' use of yours.** Publish once; any MCP client can use it.
  * **A process boundary.** The tools can be restarted, sandboxed, permissioned
    or rate-limited without touching the agent.
  * **A smaller blast radius.** A tool that crashes takes down a subprocess.

Run:  python modules/03-mcp-tools/04_mcp_agent.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from rich.console import Console

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.display import show_messages
from common.metrics import session_report, track

# THE ONLY LINE THAT DIFFERS FROM STEP 3.
from mcp_bridge import load_mcp_tools

console = Console()

console.print("[bold]Starting the MCP server and asking what it can do[/bold]")
console.print(
    "[dim]No tools are defined in this file. We connect to mcp_server.py over\n"
    "stdin/stdout and discover them at runtime — which means the agent does\n"
    "not need to know, at the time it is written, what tools will exist.[/dim]\n"
)

# Discovery. This starts the server subprocess, asks for its tool list, and
# wraps each one as a LangChain tool. See mcp_bridge.py — it is ~30 lines.
TOOLS = load_mcp_tools()

for t in TOOLS:
    console.print(f"  [cyan]{t.name}[/cyan]{tuple(t.args_schema.get('properties', {}))}")
    console.print(f"    [dim]{t.description.splitlines()[0]}[/dim]")

# ---------------------------------------------------------------------------
# Everything below this line is copied unchanged from 03_langgraph.py.
# ---------------------------------------------------------------------------

llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)
llm_with_tools = llm.bind_tools(TOOLS)

SYSTEM = """You are an SRE assistant investigating a production alert.

You have tools for service status, recent deploys, and error logs. Gather \
evidence before concluding: check status, look for recent changes, and read \
logs to confirm your hypothesis.

When you have enough evidence, reply with your conclusion in prose. Do not \
call restart_service."""


def call_model(state: MessagesState) -> dict:
    turn = sum(1 for m in state["messages"] if isinstance(m, AIMessage)) + 1
    with track(f"turn-{turn}") as m:
        reply = llm_with_tools.invoke(state["messages"])
        m.record(reply)
    return {"messages": [reply]}


def should_continue(state: MessagesState) -> str:
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END


builder = StateGraph(MessagesState)
builder.add_node("model", call_model)
builder.add_node("tools", ToolNode(TOOLS))
builder.add_edge(START, "model")
builder.add_conditional_edges("model", should_continue)
builder.add_edge("tools", "model")
graph = builder.compile()

initial = {
    "messages": [
        SystemMessage(SYSTEM),
        HumanMessage(
            "Alert: payment-service error rate jumped from 0.3% to 12.4% over "
            "the last 10 minutes. What is going on?"
        ),
    ]
}

console.print()
show_messages(initial["messages"])

seen = 0
for state in graph.stream(initial, {"recursion_limit": 12}, stream_mode="values"):
    msgs = state["messages"]
    for msg in msgs[seen:]:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for call in msg.tool_calls:
                console.print(
                    f"  [magenta]→ calling[/magenta] [bold]{call['name']}[/bold]"
                    f"({json.dumps(call['args'])})  [dim](over MCP)[/dim]"
                )
        elif isinstance(msg, ToolMessage):
            console.print(f"  [green]← returned[/green] [dim]{str(msg.content)[:130]}[/dim]")
        elif isinstance(msg, AIMessage) and msg.content:
            console.print(f"\n[cyan]{msg.content}[/cyan]\n")
    seen = len(msgs)

console.print(
    "[dim]Every one of those tool calls crossed a process boundary: serialised\n"
    "to JSON-RPC, written to a pipe, executed in another interpreter, and sent\n"
    "back. The model could not tell, the graph could not tell, and the only\n"
    "code that knows is mcp_bridge.py.\n\n"
    "Watch the latency column. Each call carries roughly 350ms of overhead,\n"
    "because our bridge opens a fresh connection per call to keep these scripts\n"
    "synchronous. A real client keeps one session open for the life of the\n"
    "agent — see the note at the top of mcp_bridge.py.[/dim]\n"
)

session_report()
