"""Step 5: the same alert, triaged blind and triaged with tools. Side by side.

In module 2 the model was handed one paragraph and asked what was wrong. It
produced fluent, structurally perfect, entirely unfounded guesses — "possibly
backend resource constraints, configuration issues, or internal service
failures" — because guessing was the only thing available to it.

This script runs that same alert twice: once blind, once with the MCP tools.
Then it prints both answers and what each one cost.

Run:  python modules/03-mcp-tools/05_triage_with_tools.py
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
from rich.panel import Panel

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

from mcp_bridge import load_mcp_tools

console = Console()

# The alert file is shared across modules — it is the same payload module 2's
# API received. `parents[2]` is the project root.
ALERT_PATH = Path(__file__).resolve().parents[2] / "data" / "sample_alerts" / "payment_error_spike.json"
alert = json.loads(ALERT_PATH.read_text())

ALERT_TEXT = (
    f"Service: {alert['service']}\n"
    f"Reported severity: {alert['severity']}\n"
    f"Metric: {alert['metric']} = {alert['value']}\n"
    f"Context: {alert['description']}"
)

QUESTION = "What is the root cause? Answer in two sentences, naming specific evidence."

llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)

# ---------------------------------------------------------------------------
# Path A — blind. One call, no tools. This is module 2's situation.
# ---------------------------------------------------------------------------
console.rule("[bold]Path A — no tools (what module 2 could do)[/bold]")

with track("blind-triage") as m:
    blind = llm.invoke([
        SystemMessage("You are an experienced site reliability engineer."),
        HumanMessage(f"{ALERT_TEXT}\n\n{QUESTION}"),
    ])
    m.record(blind)

console.print(Panel(blind.content.strip(), border_style="yellow",
                    title="[dim]blind hypothesis[/dim]", expand=False))

# ---------------------------------------------------------------------------
# Path B — the same alert, with tools.
# ---------------------------------------------------------------------------
console.rule("[bold]Path B — with MCP tools[/bold]")

TOOLS = load_mcp_tools()
llm_with_tools = llm.bind_tools(TOOLS)

SYSTEM = """You are an SRE assistant investigating a production alert.

Gather evidence with your tools before concluding: check service status, look \
for recent deploys, and read error logs. Then answer the question using the \
evidence you found. Do not call restart_service."""


def call_model(state: MessagesState) -> dict:
    turn = sum(1 for msg in state["messages"] if isinstance(msg, AIMessage)) + 1
    with track(f"tools-turn-{turn}") as m:
        reply = llm_with_tools.invoke(state["messages"])
        m.record(reply)
    return {"messages": [reply]}


def should_continue(state: MessagesState) -> str:
    return "tools" if getattr(state["messages"][-1], "tool_calls", None) else END


builder = StateGraph(MessagesState)
builder.add_node("model", call_model)
builder.add_node("tools", ToolNode(TOOLS))
builder.add_edge(START, "model")
builder.add_conditional_edges("model", should_continue)
builder.add_edge("tools", "model")
graph = builder.compile()

initial = {"messages": [SystemMessage(SYSTEM),
                        HumanMessage(f"{ALERT_TEXT}\n\n{QUESTION}")]}

# Collect the evidence trail as we go, so we can show what it actually looked at.
evidence, grounded, seen = [], None, 0
for state in graph.stream(initial, {"recursion_limit": 12}, stream_mode="values"):
    msgs = state["messages"]
    for msg in msgs[seen:]:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for call in msg.tool_calls:
                evidence.append(f"{call['name']}({json.dumps(call['args'])})")
                console.print(f"  [magenta]→[/magenta] {call['name']}({json.dumps(call['args'])})")
        elif isinstance(msg, ToolMessage):
            console.print(f"  [green]←[/green] [dim]{str(msg.content)[:110]}[/dim]")
        elif isinstance(msg, AIMessage) and msg.content:
            grounded = msg.content
    seen = len(msgs)

console.print()
console.print(Panel((grounded or "(no conclusion reached)").strip(),
                    border_style="green", title="[dim]grounded hypothesis[/dim]",
                    expand=False))

# ---------------------------------------------------------------------------
console.rule("[bold]The comparison[/bold]")

console.print(f"[bold]Path A[/bold] looked at nothing. 1 model call.")
console.print(f"[bold]Path B[/bold] gathered {len(evidence)} pieces of evidence:")
for e in evidence:
    console.print(f"    [dim]{e}[/dim]")

console.print(
    "\n[dim]Read the two answers again. Path A is not badly written — it is\n"
    "plausible, well-structured and confident. It is also a guess, and it names\n"
    "no evidence because it has none. Path B can point at commit 9f2a41c and a\n"
    "log line saying the pool is exhausted.\n\n"
    "Now look at the cost table. Path B is several times more expensive: more\n"
    "calls, and each one carries the whole accumulated transcript. That is the\n"
    "real trade — tools buy grounding, and you pay per lookup.\n\n"
    "One thing tools cannot fix: the agent still only knows what these four\n"
    "tools expose. Nothing here tells it what your team's policy is, whether\n"
    "this service is safe to restart, or what someone wrote in a runbook two\n"
    "years ago.[/dim]\n"
)

session_report()
