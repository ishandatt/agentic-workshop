"""Step 3: the same loop as a LangGraph graph — and what that buys.

The previous script's `while` loop works. So why introduce a framework?

Because the loop is about to stop being simple. Later we will want to pause it
mid-run and wait for a human, resume it hours later, persist its state, and
inspect where it got to. Those are all things you bolt onto a `while` loop
badly, and they are what LangGraph is actually for.

The behaviour below is identical to 02_the_loop.py. Only the shape changes:

    while-loop version          graph version
    ------------------          -------------
    the messages list           the STATE
    the body of the loop        NODES
    `if not reply.tool_calls`   a CONDITIONAL EDGE
    `break`                     an edge to END

Run:  python modules/03-mcp-tools/03_langgraph.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama

# StateGraph builds the graph. START and END are the two built-in nodes marking
# where execution enters and leaves.
from langgraph.graph import END, START, MessagesState, StateGraph

# ToolNode is LangGraph's ready-made version of the "run every requested tool
# and append the results" block we wrote by hand last time.
from langgraph.prebuilt import ToolNode
from rich.console import Console

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.display import show_messages
from common.metrics import session_report, track

from local_tools import TOOLS

console = Console()

llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)
llm_with_tools = llm.bind_tools(TOOLS)

SYSTEM = """You are an SRE assistant investigating a production alert.

You have tools for service status, recent deploys, and error logs. Gather \
evidence before concluding: check status, look for recent changes, and read \
logs to confirm your hypothesis.

When you have enough evidence, reply with your conclusion in prose. Do not \
call restart_service."""


# --- The state ---------------------------------------------------------------
# `MessagesState` is a ready-made state containing one field, `messages`, with
# a special property: when a node returns messages, they are APPENDED to the
# list rather than replacing it. That behaviour is called a reducer, and it is
# why nodes can return just their own contribution instead of the whole history.
#
# We use it as-is. A custom state would be a TypedDict — a dict whose keys are
# declared up front — and later modules will need one.


# --- The nodes ---------------------------------------------------------------
# A node is an ordinary function: it takes the current state and returns the
# part of the state it wants to change.
def call_model(state: MessagesState) -> dict:
    """Ask the model what to do next. This is the body of the old while loop."""
    turn = sum(1 for m in state["messages"] if isinstance(m, AIMessage)) + 1
    with track(f"turn-{turn}") as m:
        reply = llm_with_tools.invoke(state["messages"])
        m.record(reply)
    # Returning {"messages": [reply]} appends one message, thanks to the reducer.
    return {"messages": [reply]}


# The tool-running node. `ToolNode(TOOLS)` reads the tool calls off the last
# message, runs each one, and appends a ToolMessage per result — precisely the
# inner `for call in reply.tool_calls:` block from the previous script.
tool_node = ToolNode(TOOLS)


# --- The conditional edge ----------------------------------------------------
# This replaces `if not reply.tool_calls: break`. It returns the NAME of the
# next node, and LangGraph routes accordingly.
def should_continue(state: MessagesState) -> str:
    """Tools if the model asked for any, otherwise stop."""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    return END


# --- Wiring it together ------------------------------------------------------
builder = StateGraph(MessagesState)
builder.add_node("model", call_model)
builder.add_node("tools", tool_node)

builder.add_edge(START, "model")           # entry point
builder.add_conditional_edges("model", should_continue)   # model -> tools, or END
builder.add_edge("tools", "model")         # <- THE CYCLE. This is the loop.

# `.compile()` validates the graph and returns something invokable. The cycle
# above is what makes this an agent rather than a pipeline: control can go round
# again, an unbounded number of times, decided at runtime.
graph = builder.compile()

console.print("[bold]The graph[/bold]")
console.print(
    "[dim]  START → model → (tool_calls? → tools → model) ... → END\n"
    "  The edge from tools back to model is the loop. Everything else is\n"
    "  plumbing.[/dim]\n"
)

initial = {
    "messages": [
        SystemMessage(SYSTEM),
        HumanMessage(
            "Alert: payment-service error rate jumped from 0.3% to 12.4% over "
            "the last 10 minutes. What is going on?"
        ),
    ]
}
show_messages(initial["messages"])

# `.stream()` runs the graph and yields after each node, so we can watch it
# work instead of waiting for a final answer. `stream_mode="values"` yields the
# whole state each time; we print whatever is new since the last yield.
seen = 0
for state in graph.stream(initial, {"recursion_limit": 12}, stream_mode="values"):
    msgs = state["messages"]
    for msg in msgs[seen:]:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for call in msg.tool_calls:
                console.print(
                    f"  [magenta]→ calling[/magenta] [bold]{call['name']}[/bold]"
                    f"({json.dumps(call['args'])})"
                )
        elif isinstance(msg, ToolMessage):
            console.print(f"  [green]← returned[/green] [dim]{str(msg.content)[:130]}[/dim]")
        elif isinstance(msg, AIMessage) and msg.content:
            console.print(f"\n[cyan]{msg.content}[/cyan]\n")
    seen = len(msgs)

console.print(
    "[dim]Identical behaviour to the hand-written loop — same tools, same\n"
    "order, same answer, same cost. What changed is that the loop is now DATA:\n"
    "a graph you can inspect, extend with new nodes, and (later) interrupt\n"
    "partway through and resume.\n\n"
    "Note `recursion_limit` in the stream call. It is LangGraph's version of\n"
    "MAX_TURNS — a cycle needs a bound, whoever writes it.[/dim]\n"
)

session_report()
