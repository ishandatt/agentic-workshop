"""Bonus 1, step 1: "memory" is four different things wearing one word.

Ask a team whether their agent has memory and you will get four answers, all
correct, all about different mechanisms with different costs and failure modes.

This script builds three of them against the same conversation so the
differences are concrete rather than definitional:

    none       every call is independent — the model has no idea who you are
    buffer     resend the whole history — perfect recall, unbounded cost
    summary    compress old turns into a paragraph — bounded cost, lossy

The fourth, retrieval over past episodes, is the next script.

Run:  python modules/10-memory/01_kinds_of_memory.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from rich.console import Console
from rich.table import Table

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

console = Console()

llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)

SYSTEM = "You are an SRE assistant. Answer briefly."

# A conversation where the last question is only answerable if you remember the
# first. That is the whole test — everything else is bookkeeping.
TURNS = [
    "We're seeing 5xx errors on payment-service, about 12% of requests.",
    "The settlement worker pool looks like the problem.",
    "A deploy went out 25 minutes ago that changed pool sizing.",
    "Remind me — which service were we talking about, and what was the error rate?",
]


def ask(messages) -> tuple[str, int]:
    with track("turn", quiet=True) as m:
        reply = llm.invoke(messages)
        m.record(reply)
    return reply.content.strip(), m.metrics.total_tokens


# --- 1. No memory -------------------------------------------------------------
console.rule("[bold]No memory[/bold]")
console.print("[dim]Each call gets only the latest message. This is what a bare "
              "llm.invoke(question) does.[/dim]\n")

stateless_tokens = 0
for turn in TURNS:
    answer, tokens = ask([SystemMessage(SYSTEM), HumanMessage(turn)])
    stateless_tokens += tokens
console.print(f"[bold]Q:[/bold] {TURNS[-1]}")
console.print(f"[red]A:[/red] {answer[:220]}\n")
console.print("[dim]It cannot answer. There is nothing wrong with the model — we "
              "simply never told it.[/dim]\n")

# --- 2. Buffer memory ---------------------------------------------------------
console.rule("[bold]Buffer memory[/bold]")
console.print("[dim]Keep every message and resend the lot. This is what modules "
              "3 and 9 do inside their loops.[/dim]\n")

history = [SystemMessage(SYSTEM)]
buffer_tokens = 0
growth = []
for turn in TURNS:
    history.append(HumanMessage(turn))
    answer, tokens = ask(history)
    history.append(AIMessage(answer))
    buffer_tokens += tokens
    growth.append(tokens)

console.print(f"[bold]Q:[/bold] {TURNS[-1]}")
console.print(f"[green]A:[/green] {answer[:220]}\n")
console.print(f"[dim]Perfect recall. Note the per-turn cost: {growth} — it climbs "
              f"every turn,\nbecause each call resends everything said so far.[/dim]\n")

# --- 3. Summary memory --------------------------------------------------------
console.rule("[bold]Summary memory[/bold]")
console.print("[dim]After each turn, compress the conversation into a paragraph "
              "and carry only that.[/dim]\n")

summary = ""
summary_tokens = 0
for turn in TURNS:
    # The prompt is the summary plus the new message — never the full history.
    context = [SystemMessage(SYSTEM + (f"\n\nConversation so far: {summary}" if summary else "")),
               HumanMessage(turn)]
    answer, tokens = ask(context)
    summary_tokens += tokens

    # Compressing costs a model call of its own. Summary memory is not free —
    # it trades a growing prompt for an extra call per turn.
    with track("summarise", quiet=True) as m:
        s = llm.invoke([
            SystemMessage("Compress this into two sentences that preserve every "
                          "specific fact: service names, numbers, times."),
            HumanMessage(f"Previous summary: {summary}\nNew exchange:\nUser: {turn}\n"
                         f"Assistant: {answer}"),
        ])
        m.record(s)
    summary = s.content.strip()
    summary_tokens += m.metrics.total_tokens

console.print(f"[bold]Q:[/bold] {TURNS[-1]}")
console.print(f"[yellow]A:[/yellow] {answer[:220]}\n")
console.print(f"[bold]The summary being carried:[/bold]\n[dim]{summary[:300]}[/dim]\n")

# --- The comparison -----------------------------------------------------------
table = Table(title="Four turns, three strategies")
table.add_column("strategy")
table.add_column("tokens", justify="right")
table.add_column("recalled the answer?")
table.add_column("cost shape")
table.add_row("none", str(stateless_tokens), "[red]no[/red]", "flat")
table.add_row("buffer", str(buffer_tokens), "[green]yes[/green]", "grows every turn")
table.add_row("summary", str(summary_tokens), "[yellow]check above[/yellow]",
              "flat-ish, +1 call/turn")
console.print(table)

console.print(
    "\n[dim]Read the numbers rather than the labels. Buffer memory looks cheap\n"
    "over four turns and is the one that eventually breaks: cost grows with the\n"
    "square of conversation length, and at some point you hit the context\n"
    "window and the whole call fails.\n\n"
    "Summary memory pays an extra call per turn to keep the prompt flat. On a\n"
    "short conversation that is a bad trade. On a hundred-turn one it is the\n"
    "only thing that works.\n\n"
    "And summary memory LOSES things. Check whether the exact error rate\n"
    "survived compression above — the summariser was explicitly told to keep\n"
    "numbers, and small models still drop them. What you compress, you may not\n"
    "get back.[/dim]\n"
)

console.print(
    "[bold]Which to use[/bold]\n"
    "[dim]  short task, one sitting        buffer, and stop worrying\n"
    "  long conversation              buffer recent turns + summary of older ones\n"
    "  facts needed across sessions   store them somewhere and retrieve — next script\n"
    "  resumable workflow state       a checkpointer, which module 8 already built[/dim]\n"
)

session_report()
