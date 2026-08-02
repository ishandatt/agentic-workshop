"""Bonus 1, step 2: memory of things that happened, not of what was said.

The previous script kept a *conversation* in mind. This is a different kind of
memory and arguably the more valuable one for an incident responder: a record of
what has happened before, retrieved when something similar happens again.

The mechanism is module 4's, pointed at a different corpus. Which is worth
noticing on its own — "semantic memory" and "RAG" are the same machinery with
different content and a different name.

What makes it interesting is what past incidents contain that a runbook does
not: what was actually tried, what it cost, and what someone decided not to do.

Run:  python modules/10-memory/02_episodic_memory.py
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_postgres import PGVector
from rich.console import Console
from rich.panel import Panel

from common.config import CHAT_MODEL, DATABASE_URL, EMBED_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

logging.getLogger("langchain_postgres").setLevel(logging.WARNING)
console = Console()

ROOT = Path(__file__).resolve().parents[2]
INCIDENTS = ROOT / "data" / "past_incidents.jsonl"
CONNECTION = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# A SEPARATE collection from the runbook. Mixing them would mean a question
# about policy could retrieve an anecdote, and vice versa — the two have very
# different authority and should not compete for the same slots.
COLLECTION = "incident_memory"

embeddings = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)
llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)

# --- Build the memory ---------------------------------------------------------
records = [json.loads(line) for line in INCIDENTS.read_text().splitlines() if line.strip()]

# What text gets embedded matters enormously. We embed the SYMPTOM most
# prominently, because that is what a new alert resembles — you recognise an
# incident by how it looks, not by how it was fixed.
documents = [
    Document(
        page_content=(
            f"Symptom: {r['symptom']}\n"
            f"Service: {r['service']}\n"
            f"Cause: {r['cause']}\n"
            f"Resolution: {r['resolution']}"
        ),
        metadata={"id": r["id"], "date": r["date"], "service": r["service"],
                  "took_minutes": r["took_minutes"], "decided_by": r["decided_by"]},
    )
    for r in records
]

console.print(f"[bold]Building incident memory[/bold] — {len(documents)} past incidents\n")

with track("ingest-memory", quiet=True) as m:
    store = PGVector.from_documents(
        documents=documents, embedding=embeddings, connection=CONNECTION,
        collection_name=COLLECTION, pre_delete_collection=True,
    )
    m.record_raw(input_tokens=sum(len(d.page_content) for d in documents) // 4, output_tokens=0)

# --- Recall against new alerts ------------------------------------------------
NEW_ALERTS = [
    ("payment-service 5xx at 12.4%, settlement worker pool queue depth climbing, "
     "a deploy went out 25 minutes ago", "should recall INC-2041 (pool deploy)"),
    ("payment-service error rate still elevated 40 seconds after we restarted it",
     "should recall INC-2103 (warm-up, do not restart again)"),
    ("checkout p99 up to 2.9s during a promotion, error rate normal",
     "should recall INC-2088 (expected, no action)"),
]

for alert, expectation in NEW_ALERTS:
    console.rule(f"[bold]{alert[:66]}…[/bold]")
    console.print(f"[dim]{expectation}[/dim]\n")

    with track("recall", quiet=True) as m:
        hits = store.similarity_search_with_score(alert, k=2)
        m.record_raw(input_tokens=len(alert) // 4, output_tokens=0)

    for doc, distance in hits:
        console.print(f"  [cyan]{doc.metadata['id']}[/cyan] "
                      f"[dim]{doc.metadata['date']} · {distance:.3f} · "
                      f"{doc.metadata['took_minutes']}min · "
                      f"{doc.metadata['decided_by']}[/dim]")
        console.print(f"    [dim]{' '.join(doc.page_content.split())[:118]}…[/dim]")
    console.print()

# --- Use it ------------------------------------------------------------------
console.rule("[bold]Answering with memory[/bold]")

alert = NEW_ALERTS[1][0]        # the "still failing after a restart" one
hits = store.similarity_search_with_score(alert, k=2)
memory = "\n\n".join(
    f"[{d.metadata['id']}, {d.metadata['date']}]\n{d.page_content}" for d, _ in hits
)

for label, system, prompt in (
    ("without memory",
     "You are an SRE assistant. Answer in two sentences.",
     alert + "\n\nWhat should we do?"),
    ("with memory",
     "You are an SRE assistant. You are shown similar PAST incidents. Use them, "
     "and cite the incident id you relied on. Answer in two sentences.",
     f"Past incidents:\n\n{memory}\n\nCurrent alert: {alert}\n\nWhat should we do?"),
):
    with track(label, quiet=True) as m:
        reply = llm.invoke([SystemMessage(system), HumanMessage(prompt)])
        m.record(reply)
    colour = "yellow" if label == "without memory" else "green"
    console.print(Panel(reply.content.strip()[:420], title=f"[{colour}]{label}[/{colour}]",
                        border_style=colour, expand=False))

console.print(
    "\n[dim]The second answer can say 'we saw this in INC-2103, it was cache\n"
    "warm-up, restarting again doubled the outage'. That is not in the runbook\n"
    "and it is not in the model — it happened to us, and we wrote it down.\n\n"
    "This is the memory type teams most often lack. Everyone builds RAG over\n"
    "documentation; far fewer store what actually happened, which is where the\n"
    "expensive lessons live.[/dim]\n"
)

console.print(
    "[bold]Three cautions before you build this for real.[/bold]\n"
    "[dim]  * Past incidents are ANECDOTES, not policy. A runbook rule and a\n"
    "    recollection should not carry equal weight in a prompt, which is why\n"
    "    they are separate collections here.\n"
    "  * Memory rots. INC-2041's resolution may be wrong now. Store the date,\n"
    "    show the date, and let recency inform how much to trust it.\n"
    "  * Similar-looking is not the same. Two incidents with identical symptoms\n"
    "    and different causes are exactly how memory misleads — which is why the\n"
    "    prompt asks it to CITE, so a human can check the match.[/dim]\n"
)

session_report()
