"""Step 1: the same question, answered with and without the runbook.

Retrieval exists. Now use it, and measure what it bought.

Each question below is answered twice — once from the model's own knowledge,
once with runbook chunks pasted into the prompt. The questions are chosen so
that the difference is checkable: they have specific, invented answers that
exist only in our document.

Run:  python modules/05-rag-vs-norag/01_compare.py
      (run module 4's 04_ingest.py first)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

from rag import format_context, get_store, retrieve, usable

console = Console()

llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)
store = get_store()

# Without the runbook the model has no way to know any of these. With it, the
# answer is a short quote away — which makes grading easy by eye.
QUESTIONS = [
    ("can I restart payment-service at 15:00 IST?", "14:00-16:00 IST window"),
    ("what should I check first for a payment-service 5xx spike?", "Redis connection pool"),
    ("what is the safe minimum settlement pool size?", "40"),
    ("who do I page for a suspected duplicate charge?", "Tom Oyelaran, Sev-1"),
    ("how do I rotate the TLS certificate?", "NOT in the runbook"),
]

NO_RAG_SYSTEM = (
    "You are an SRE assistant for a payments platform. Answer in two sentences."
)

# The RAG prompt does three things beyond pasting text: it scopes the model to
# the provided context, it tells it to cite, and — most importantly — it gives
# explicit permission to say it does not know. Without that last sentence a
# model will always find something to say.
RAG_SYSTEM = """You are an SRE assistant for a payments platform.

Answer ONLY from the runbook extracts provided. Cite the section you used.

If the extracts do not contain the answer, say exactly: "Not covered in the \
runbook." Do not use general knowledge to fill gaps."""

results = []

for question, expected in QUESTIONS:
    console.rule(f"[bold]{question}[/bold]")
    console.print(f"[dim]the runbook says: {expected}[/dim]\n")

    # --- Path A: no retrieval -------------------------------------------------
    with track("no-rag", quiet=True) as m:
        plain = llm.invoke([SystemMessage(NO_RAG_SYSTEM), HumanMessage(question)])
        m.record(plain)
    no_rag_tokens = m.metrics.total_tokens

    # --- Path B: retrieve, filter, then answer --------------------------------
    hits = retrieve(store, question, k=4)
    kept = usable(hits)

    context = format_context(kept)
    with track("rag", quiet=True) as m:
        grounded = llm.invoke([
            SystemMessage(RAG_SYSTEM),
            HumanMessage(f"Runbook extracts:\n\n{context}\n\nQuestion: {question}"),
        ])
        m.record(grounded)
    rag_tokens = m.metrics.total_tokens

    console.print(Panel(plain.content.strip(), title="[yellow]without runbook[/yellow]",
                        border_style="yellow", expand=False))
    console.print(Panel(grounded.content.strip(), title="[green]with runbook[/green]",
                        border_style="green", expand=False))

    # Show what was retrieved and what survived the threshold, so the room can
    # see the filter working rather than trusting it.
    console.print(f"[dim]retrieved {len(hits)}, kept {len(kept)} after the "
                  f"distance filter:[/dim]")
    for doc, dist in hits:
        mark = "[green]keep[/green]" if dist <= 0.45 else "[red]drop[/red]"
        console.print(f"  {mark} {dist:.3f}  [cyan]{doc.metadata['section'][:46]}[/cyan]")
    console.print()

    results.append((question, expected, no_rag_tokens, rag_tokens, len(kept)))

# --- The bill ----------------------------------------------------------------
table = Table(title="What grounding cost")
table.add_column("question")
table.add_column("no-RAG tok", justify="right")
table.add_column("RAG tok", justify="right")
table.add_column("×", justify="right")
table.add_column("chunks", justify="right")

for question, _, no_rag_tok, rag_tok, kept_n in results:
    ratio = rag_tok / no_rag_tok if no_rag_tok else 0
    table.add_row(question[:40], str(no_rag_tok), str(rag_tok), f"{ratio:.1f}×", str(kept_n))
console.print(table)

console.print(
    "\n[dim]Two things to take from that table.\n\n"
    "The answers changed from plausible to specific. Without the runbook the\n"
    "model gives sensible general SRE advice that is not our policy. With it,\n"
    "it names the window, the pool, the number, the person.\n\n"
    "And it costs multiples of the tokens, on every single call, forever. The\n"
    "retrieved context is the dominant term — the question itself is rounding\n"
    "error. That is the trade: you are paying per answer for facts that would\n"
    "otherwise be wrong.[/dim]\n"
)

console.print(
    "[bold]The TLS question is the one to dwell on.[/bold]\n"
    "[dim]Nothing in the runbook covers it. The distance filter dropped every\n"
    "hit, the prompt said what to do when that happens, and the model should\n"
    "have said 'Not covered in the runbook.' Check whether it did — that is\n"
    "the difference between a system that knows its limits and one that\n"
    "improvises.[/dim]\n"
)

session_report()
