"""Step 2: close the loop by hand. This is an agent, and it is a while loop.

Module 0 defined an agent as "an LLM in a loop that can call tools, observe the
results, and act again". Here is that loop, written out with nothing hidden:

    while True:
        reply = llm.invoke(messages)          # ask
        if not reply.tool_calls:              # done?
            break
        for call in reply.tool_calls:         # act
            messages.append(ToolMessage(...)) # observe
                                              # ...and round again

Twenty lines. No framework. If you take one thing from this module, take this:
an agent is not a special kind of model, it is a control flow you write around
an ordinary one.

Run:  python modules/03-mcp-tools/02_the_loop.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

# ToolMessage is how a tool's OUTPUT goes back into the conversation. It carries
# a tool_call_id so the model can match the answer to the request it made —
# necessary because a model may request several tools in one turn.
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama
from rich.console import Console

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.display import show_messages
from common.metrics import session_report, track

from local_tools import TOOLS

console = Console()

llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)
llm_with_tools = llm.bind_tools(TOOLS)

# A name -> tool lookup, so we can find the function the model asked for.
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SYSTEM = """You are an SRE assistant investigating a production alert.

You have tools for service status, recent deploys, and error logs. Gather \
evidence before concluding: check status, look for recent changes, and read \
logs to confirm your hypothesis.

When you have enough evidence, reply with your conclusion in prose. Do not \
call restart_service."""

messages = [
    SystemMessage(SYSTEM),
    HumanMessage(
        "Alert: payment-service error rate jumped from 0.3% to 12.4% over the "
        "last 10 minutes. What is going on?"
    ),
]

show_messages(messages)

# A cap, for the same reason the retry loop in module 2 had one: a loop whose
# exit depends on a model's judgement is a loop that might not exit.
MAX_TURNS = 6

for turn in range(1, MAX_TURNS + 1):
    console.rule(f"[bold]Turn {turn}[/bold]")

    with track(f"turn-{turn}") as m:
        reply = llm_with_tools.invoke(messages)
        m.record(reply)

    # The model's own message goes back into the history, whether it contains
    # tool calls, prose, or both. Skipping this loses the thread.
    messages.append(reply)

    # No tool calls means the model believes it is finished.
    if not reply.tool_calls:
        console.print("\n[green]No tool calls — the model is done.[/green]\n")
        console.print(f"[cyan]{reply.content}[/cyan]\n")
        break

    # Otherwise: run every tool it asked for, and append each result.
    for call in reply.tool_calls:
        console.print(
            f"  [magenta]→ calling[/magenta] [bold]{call['name']}[/bold]"
            f"({json.dumps(call['args'])})"
        )

        tool = TOOLS_BY_NAME.get(call["name"])
        if tool is None:
            # Models do occasionally invent tool names. Report it as data so
            # the model can recover, rather than crashing the loop.
            result = {"error": f"no such tool {call['name']!r}",
                      "available": list(TOOLS_BY_NAME)}
        else:
            result = tool.invoke(call["args"])

        rendered = json.dumps(result, default=str)
        console.print(f"  [green]← returned[/green] [dim]{rendered[:150]}[/dim]")

        messages.append(
            ToolMessage(
                content=rendered,
                # Without this id the model cannot tell which request this
                # answers, and some providers reject the message outright.
                tool_call_id=call["id"],
            )
        )
else:
    # `for ... else` runs only when the loop was NOT broken out of — so this
    # fires when we exhausted MAX_TURNS without the model finishing.
    console.print(
        f"\n[red]Hit the {MAX_TURNS}-turn cap without a conclusion.[/red]\n"
        "[dim]In production this is a real outcome, not a hypothetical: models "
        "loop, re-call the same tool, or chase their own tail.[/dim]\n"
    )

console.print(
    "[dim]Look back at the turns. The model chose an order — status, then\n"
    "deploys, then logs — and each result changed what it asked for next. That\n"
    "is the whole of 'agency': the control flow above is fixed, but which\n"
    "branch it takes is decided at runtime by a model.\n\n"
    "Also count the LLM calls in the table below. One per turn. An agent that\n"
    "takes four turns costs four times what a single completion costs.[/dim]\n"
)

session_report()
