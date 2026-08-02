"""Bonus 2, step 1: the context window, and why agent loops eat it quadratically.

Every model has a hard limit on how many tokens it can consider at once. Cross
it and the call does not degrade gracefully — it fails, or silently drops the
beginning of your prompt, which is worse.

Agent loops are unusually good at reaching that limit, for a reason that is
obvious in hindsight and surprises everyone the first time: each turn resends
the entire conversation, so total tokens grow with the SQUARE of the number of
turns.

Run:  python modules/11-context/01_the_window.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03-mcp-tools"))

import httpx
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama
from rich.console import Console
from rich.table import Table

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

from local_tools import TOOLS

console = Console()

# --- How big is the window, actually? ----------------------------------------
# Ollama exposes model metadata over HTTP. The context length lives in
# model_info under a key named after the architecture, e.g. "qwen2.contextlength".
info = httpx.post(f"{OLLAMA_BASE_URL}/api/show", json={"model": CHAT_MODEL},
                  timeout=30).json()
model_info = info.get("model_info", {})
ctx_keys = [k for k in model_info if "context_length" in k]
declared = model_info.get(ctx_keys[0]) if ctx_keys else "unknown"

console.print(f"[bold]{CHAT_MODEL}[/bold]")
console.print(f"  declared context length: [cyan]{declared}[/cyan] tokens")

# The number that actually applies is whatever Ollama was told to load with —
# `num_ctx`. It defaults far below the model's maximum, which is the single most
# common surprise here: your model supports 32k and your runtime gave it 4k.
params = info.get("parameters", "")
console.print(f"  runtime parameters: [dim]{params.strip()[:80] or '(defaults)'}[/dim]")
console.print(
    "\n[dim]Two different numbers, and the smaller one wins. A model advertising\n"
    "32k tokens loaded with num_ctx=4096 has a 4096-token window, and nothing\n"
    "warns you — the oldest tokens simply fall off the front.[/dim]\n"
)

# --- Watch a loop eat it ------------------------------------------------------
console.rule("[bold]Token growth in an agent loop[/bold]")

llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)
llm_with_tools = llm.bind_tools(TOOLS)
by_name = {t.name: t for t in TOOLS}

messages = [
    SystemMessage("You are an SRE assistant. Investigate thoroughly using your "
                  "tools: check status, deploys, and logs for payment-service, "
                  "checkout-service and log-aggregator before concluding."),
    HumanMessage("Several services look unhealthy. Investigate all of them."),
]

rows = []
cumulative = 0
for turn in range(1, 9):
    with track(f"turn-{turn}", quiet=True) as m:
        reply = llm_with_tools.invoke(messages)
        m.record(reply)
    messages.append(reply)
    cumulative += m.metrics.total_tokens
    rows.append((turn, m.metrics.input_tokens, m.metrics.output_tokens, cumulative,
                 len(messages)))

    if not reply.tool_calls:
        break
    for call in reply.tool_calls:
        tool = by_name.get(call["name"])
        result = tool.invoke(call["args"]) if tool else {"error": "unknown tool"}
        messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

table = Table(title="Every turn resends everything")
table.add_column("turn", justify="right")
table.add_column("input tok", justify="right")
table.add_column("output tok", justify="right")
table.add_column("cumulative", justify="right")
table.add_column("messages", justify="right")
for turn, inp, out, cum, msgs in rows:
    table.add_row(str(turn), str(inp), str(out), str(cum), str(msgs))
console.print(table)

first_in = rows[0][1]
last_in = rows[-1][1]
console.print(
    f"\n[bold]Input tokens went {first_in} → {last_in} "
    f"({last_in / first_in:.1f}× ) across {len(rows)} turns.[/bold]"
)
console.print(
    "[dim]Output stayed roughly flat. The growth is entirely the conversation\n"
    "being resent — and you pay for it again on every single turn. Ten turns of\n"
    "a chatty tool loop can cost more than the entire rest of your pipeline.[/dim]\n"
)

# --- Where the tokens actually are -------------------------------------------
console.rule("[bold]What is filling the window[/bold]")

sizes = []
for msg in messages:
    kind = type(msg).__name__.replace("Message", "")
    content = str(getattr(msg, "content", ""))
    sizes.append((kind, len(content) // 4))   # ~4 chars per token

by_kind: dict[str, int] = {}
for kind, size in sizes:
    by_kind[kind] = by_kind.get(kind, 0) + size

total = sum(by_kind.values()) or 1
breakdown = Table()
breakdown.add_column("message type")
breakdown.add_column("approx tokens", justify="right")
breakdown.add_column("share", justify="right")
for kind, size in sorted(by_kind.items(), key=lambda kv: -kv[1]):
    breakdown.add_row(kind, str(size), f"{size / total:.0%}")
console.print(breakdown)

console.print(
    "\n[dim]Check which row is largest — it is often not the one people expect.\n"
    "Tool results are the usual suspect, but the model's OWN messages frequently\n"
    "dominate: every turn it restates its reasoning, and all of it is resent\n"
    "forever. You cannot shrink that by capping tool output.\n\n"
    "Tool results are still the easiest win, because nobody needs 40 log lines\n"
    "in the transcript permanently. That is why get_error_logs takes a `limit`,\n"
    "and why module 9 truncates tool output.\n\n"
    "Module 9 also shows the danger of doing it carelessly: truncating the\n"
    "deploy result at 200 characters removed the commit message the diagnosis\n"
    "depended on. Trim tool output at the tool, where you know what the fields\n"
    "mean — not blindly at a character count in the prompt builder.[/dim]\n"
)

session_report()
