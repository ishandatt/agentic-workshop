"""Step 6: query the store, and learn to distrust it a little.

Retrieval always returns something. That is the property to internalise before
you build anything on top of it: ask a vector store about the weather and it
will hand you the nearest runbook section with a straight face, because "top 3
by distance" has no concept of "nothing here is relevant".

This script runs a spread of questions — ones the runbook answers well, ones it
answers badly, and one it cannot answer at all — so you can see the difference
in the scores rather than take it on faith.

Run:  python modules/04-rag-ingestion/06_retrieve.py
      (run 04_ingest.py first)
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_ollama import OllamaEmbeddings
from langchain_postgres import PGVector
from rich.console import Console

from common.config import DATABASE_URL, EMBED_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

logging.getLogger("langchain_postgres").setLevel(logging.WARNING)
console = Console()

COLLECTION = "runbook"
CONNECTION = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

embeddings = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)

# Connect to the EXISTING collection. Note there is no `pre_delete_collection`
# here — that flag belongs to ingestion, and passing it by accident would erase
# everything you just built.
store = PGVector(
    embeddings=embeddings,
    connection=CONNECTION,
    collection_name=COLLECTION,
)

QUESTIONS = [
    # Answered directly and well.
    ("can I restart payment-service right now?", "well answered"),
    ("what should I check first for payment 5xx errors?", "well answered"),
    ("who do I escalate a suspected duplicate charge to?", "well answered"),
    # In the document, but phrased far from how the runbook phrases it.
    ("the checkout page is slow, is that an incident?", "obliquely phrased"),
    # Not in the document at all.
    ("how do I rotate the TLS certificate?", "NOT in the runbook"),
]

for question, expectation in QUESTIONS:
    console.rule(f"[bold]{question}[/bold]")
    console.print(f"[dim]expectation: {expectation}[/dim]\n")

    with track("retrieve", quiet=True) as m:
        # k=3 asks for the three nearest chunks. There is no threshold and no
        # "no results" case — it returns three, always.
        hits = store.similarity_search_with_score(question, k=3)
        m.record_raw(input_tokens=len(question) // 4, output_tokens=0)

    for doc, distance in hits:
        # Lower distance = closer. Colour the good ones so the pattern is
        # visible at a glance from the back of a room.
        colour = "green" if distance < 0.30 else "yellow" if distance < 0.40 else "red"
        console.print(
            f"  [{colour}]{distance:.4f}[/{colour}]  "
            f"[cyan]{doc.metadata['section'][:48]}[/cyan]"
        )
        # `" ".join(text.split())` collapses all whitespace, so a multi-line
        # markdown chunk prints as one readable line.
        flat = " ".join(doc.page_content.split())
        console.print(f"          [dim]{flat[:96]}…[/dim]")
    console.print()

console.print(
    "[bold]Read the last one again.[/bold]\n"
    "[dim]There is nothing about TLS certificates in the runbook, and retrieval\n"
    "returned three sections anyway. The distances are worse than for the good\n"
    "questions — which is the only signal you get. Nothing in the store says\n"
    "'I do not know'.\n\n"
    "If you pipe those chunks into a prompt without looking at the scores, the\n"
    "model will do its best with irrelevant material, and produce a confident\n"
    "answer about certificate rotation assembled from an escalation policy.\n"
    "This is how RAG systems hallucinate: not because retrieval failed loudly,\n"
    "but because it failed quietly.[/dim]\n"
)

console.print(
    "[bold]Two defences, neither free:[/bold]\n"
    "[dim]  * a distance threshold — drop hits worse than X, and accept that X\n"
    "    is a magic number you tuned on today's questions\n"
    "  * make the model cite — if it must quote the chunk it used, a bad\n"
    "    retrieval becomes visible in the answer instead of invisible\n\n"
    "Both are downstream of a harder question: how would you even know your\n"
    "retrieval is bad, at scale, without reading every answer? That question\n"
    "gets its own module.[/dim]\n"
)

session_report()
