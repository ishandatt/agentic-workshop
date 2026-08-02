"""Step 1: a "tool call" is structured output. Nothing more, and nothing runs.

The word "tool" makes it sound as though the model reaches out and does
something. It does not. The model emits a small piece of structured data that
says *"I would like get_service_status called with service='payment-service'"* —
and then it stops and waits, exactly like any other completion.

If you built the structured output in module 2, you have already seen this
mechanism. A tool call is the same trick pointed at a different target: instead
of constraining the model to your result schema, you hand it several schemas
and let it pick one.

Everything else — actually running the function, feeding the answer back,
deciding whether to go round again — is code that YOU write. That code is the
agent, and it does not exist yet in this file.

Run:  python modules/03-mcp-tools/01_tool_calling.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from rich.console import Console

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.display import show_messages
from common.metrics import session_report, track

from local_tools import TOOLS

console = Console()

llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)

# --- What the model is actually told about the tools -------------------------
# `.bind_tools()` attaches tool descriptions to every request. Each tool becomes
# a name, a description and a JSON Schema for its arguments — assembled from
# the function name, its docstring and its type hints.
#
# Unlike the Pydantic field descriptions in module 2, these DO reach the model.
# They have to: it cannot choose a tool it has never been told about. Which
# makes the docstrings in local_tools.py load-bearing prompt text.
llm_with_tools = llm.bind_tools(TOOLS)

console.print("[bold]What the model is told about each tool[/bold]")
for t in TOOLS:
    console.print(f"  [cyan]{t.name}[/cyan]{tuple(t.args)}")
    console.print(f"    [dim]{t.description.splitlines()[0]}[/dim]")

# --- Ask it something that needs a tool --------------------------------------
messages = [
    SystemMessage(
        "You are an SRE assistant with access to ops tools. "
        "Investigate problems by gathering evidence before drawing conclusions."
    ),
    HumanMessage(
        "payment-service is returning 5xx errors. Start by checking its current status."
    ),
]

console.print()
show_messages(messages)

with track("tool-call-request") as m:
    reply = llm_with_tools.invoke(messages)
    m.record(reply)

# --- Look at what came back ---------------------------------------------------
# `.tool_calls` is a list of dicts: {"name", "args", "id"}. It is empty when the
# model chose to answer in prose instead.
console.print("\n[bold]What the model returned[/bold]")
console.print(f"  text content : {reply.content!r}")
console.print(f"  tool_calls   : {json.dumps(reply.tool_calls, indent=2, default=str)}")

console.print(
    "\n[yellow]Notice what did NOT happen.[/yellow]\n"
    "[dim]No function ran. The service was not contacted. `fake_infra` was not\n"
    "touched. The model produced a REQUEST — structured data naming a function\n"
    "and its arguments — and then stopped.\n\n"
    "That request is just constrained generation again, the same machinery as\n"
    "module 2. The only new idea is that we offered several schemas and let the\n"
    "model choose between them.[/dim]\n"
)

# --- Now WE run it ------------------------------------------------------------
if reply.tool_calls:
    call = reply.tool_calls[0]
    console.print(f"[bold]Running it ourselves:[/bold] {call['name']}({call['args']})")

    # Look the tool up by name and invoke it. This two-line lookup is the part
    # people imagine the model is doing. It is not; it is ours.
    by_name = {t.name: t for t in TOOLS}
    result = by_name[call["name"]].invoke(call["args"])

    console.print(f"[green]  result:[/green] {json.dumps(result, default=str)}\n")

    console.print(
        "[dim]The model still has not seen that result. As far as it knows, it\n"
        "asked a question into the void. To make this an agent we must hand the\n"
        "answer back and let it continue — which is the next script.[/dim]\n"
    )
else:
    console.print(
        "[yellow]The model answered in prose instead of calling a tool.[/yellow]\n"
        "[dim]That happens. Small models are inconsistent about tool use — "
        "re-run, or make the request more explicit.[/dim]\n"
    )

session_report()
