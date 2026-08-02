"""Step 4: chunk it, embed it, store it. The ingestion pipeline, end to end.

Three things happen here, and they are the whole of "building a vector store":

    split the document   ->  embed each chunk   ->  write vectors to Postgres

Everything clever about retrieval happens at query time. Ingestion is plumbing —
but plumbing you only get to do once per document version, so it is worth doing
carefully. A bad chunk stored today is a bad answer every day after.

Re-running this script is safe: it deletes the collection first and rebuilds it.

Run:  python modules/04-rag-ingestion/04_ingest.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# A Document is just text plus a metadata dict. The metadata rides along with
# the vector and comes back on retrieval — which is how you cite a source.
import logging

from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rich.console import Console
from rich.table import Table

from common.config import DATABASE_URL, EMBED_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

console = Console()

# langchain-postgres logs "Collection not found" at INFO on the very first run,
# before it creates the collection. Harmless, but it reads like an error in a
# room full of people seeing this for the first time.
logging.getLogger("langchain_postgres").setLevel(logging.WARNING)

RUNBOOK = Path(__file__).resolve().parents[2] / "runbook" / "payment-service-runbook.md"

# A named collection, so several document sets can share one database. Later
# modules retrieve from this exact name — change it here and change it there.
COLLECTION = "runbook"

# langchain-postgres talks to Postgres through SQLAlchemy, which wants the
# driver named in the URL. Our .env holds a plain `postgresql://` URL for
# psycopg's own use, so we adapt it here rather than keeping two URLs in .env.
CONNECTION = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)


def section_of(chunk: str, text: str, position: int) -> str:
    """Which '## ' heading does this chunk belong to?

    Two cases, and getting this wrong is easy — the first version of this
    function was off by one whole section.

    Because we split ON "\n## ", most chunks START with their own heading. So
    check the chunk itself first; only if it has no heading of its own do we
    look backwards for the section it continues.
    """
    stripped = chunk.lstrip()
    if stripped.startswith("## "):
        # `split("\n", 1)[0]` takes just the first line.
        return stripped.split("\n", 1)[0].removeprefix("## ").strip()

    # No heading of its own: inherit the most recent one before it.
    # `rfind` returns the highest index at or before `position`, or -1.
    head = text.rfind("\n## ", 0, position + 1)
    if head == -1:
        return "(preamble)"
    line_end = text.find("\n", head + 1)
    return text[head + 1:line_end].removeprefix("## ").strip()


# --- 1. Split ----------------------------------------------------------------
text = RUNBOOK.read_text()

splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50,
    separators=["\n## ", "\n### ", "\n\n", "\n", ". ", " ", ""],
)
chunks = splitter.split_text(text)

# Attach metadata to each chunk. `cursor` walks forward through the source so
# each chunk is located in the original document — which lets us label it with
# the section it came from.
documents, cursor = [], 0
for i, chunk in enumerate(chunks):
    position = text.find(chunk[:60], cursor)
    if position == -1:
        position = cursor
    cursor = position + 1

    documents.append(
        Document(
            page_content=chunk,
            metadata={
                "source": RUNBOOK.name,
                "section": section_of(chunk, text, position),
                "chunk_index": i,
            },
        )
    )

console.print(f"[bold]1. Split[/bold]  {RUNBOOK.name} → {len(documents)} chunks")

# Show which sections we ended up with, and how many chunks each produced.
counts: dict[str, int] = {}
for d in documents:
    # dict.get(key, 0) + 1 is the idiomatic "increment or start at one".
    counts[d.metadata["section"]] = counts.get(d.metadata["section"], 0) + 1

table = Table(title="Chunks per section")
table.add_column("section")
table.add_column("chunks", justify="right")
for section, n in counts.items():
    table.add_row(section[:56], str(n))
console.print(table)

# --- 2. Embed and 3. Store ----------------------------------------------------
# `PGVector.from_documents` does both: it calls the embedding model once per
# batch of chunks, then writes text, metadata and vector to Postgres.
embeddings = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)

console.print(f"\n[bold]2. Embed[/bold]  {len(documents)} chunks with {EMBED_MODEL}")
console.print(f"[bold]3. Store[/bold]  into collection {COLLECTION!r}\n")

with track("ingest") as m:
    store = PGVector.from_documents(
        documents=documents,
        embedding=embeddings,
        connection=CONNECTION,
        collection_name=COLLECTION,
        # Wipe and rebuild, so re-running does not accumulate duplicates.
        # Without this you would get two copies of every chunk on the second run
        # and retrieval would start returning the same text twice.
        pre_delete_collection=True,
    )
    m.record_raw(input_tokens=sum(len(d.page_content) for d in documents) // 4,
                 output_tokens=0)

console.print("[green]✔ Ingested.[/green]\n")

# --- Prove it landed ----------------------------------------------------------
# A quick search against the store we just built. If this returns something
# sensible, all three stages worked.
probe = "when is it unsafe to restart the payment service?"
hits = store.similarity_search_with_score(probe, k=2)

console.print(f"[bold]Smoke test:[/bold] {probe!r}\n")
for doc, score in hits:
    # PGVector returns a DISTANCE, where lower means closer. That is the
    # opposite convention to the cosine similarity in step 3 — a detail worth
    # noticing before you write a threshold the wrong way round.
    console.print(f"  [dim]distance {score:.4f}[/dim]  "
                  f"[cyan]{doc.metadata['section']}[/cyan]")
    console.print(f"    {doc.page_content[:150].strip()}…\n")

console.print(
    "[dim]The vectors are now rows in a Postgres table, not an abstraction. The\n"
    "next step opens psql and looks at them, because a vector store is much\n"
    "less mysterious once you have seen the table it lives in.[/dim]\n"
)

session_report()
