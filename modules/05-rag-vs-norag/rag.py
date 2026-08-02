"""Retrieval helpers shared by this module's scripts and its API.

Small on purpose. The interesting part of RAG is not the code — it is the
prompt you build with what you retrieved, and the honesty of what you do when
you retrieve nothing useful.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_ollama import OllamaEmbeddings
from langchain_postgres import PGVector

from common.config import DATABASE_URL, EMBED_MODEL, OLLAMA_BASE_URL

logging.getLogger("langchain_postgres").setLevel(logging.WARNING)

# Must match the collection module 4 wrote to.
COLLECTION = "runbook"
CONNECTION = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Hits worse than this are treated as "nothing relevant found". Measured in
# module 4: questions the runbook answers scored 0.29-0.34, a question it does
# not answer scored 0.51. 0.45 sits in that gap.
#
# Be honest about what this number is: tuned by hand on a handful of examples,
# on one embedding model, against one small document. It is a starting point,
# not a constant of nature — module 6 is about measuring whether it is any good.
DISTANCE_THRESHOLD = 0.45


def get_store() -> PGVector:
    """Connect to the collection module 4 built. No pre_delete here — ever."""
    return PGVector(
        embeddings=OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL),
        connection=CONNECTION,
        collection_name=COLLECTION,
    )


def retrieve(store: PGVector, query: str, k: int = 4) -> list[tuple]:
    """Fetch the k nearest chunks as (document, distance) pairs."""
    return store.similarity_search_with_score(query, k=k)


def format_context(hits: list[tuple]) -> str:
    """Turn retrieved chunks into the block of text that goes in the prompt.

    Each chunk is labelled with its source and section. That labelling is not
    decoration: it is what lets the model cite, and what lets a human check the
    citation afterwards.
    """
    if not hits:
        return "(no relevant runbook sections found)"

    parts = []
    for i, (doc, distance) in enumerate(hits, start=1):
        source = doc.metadata.get("source", "?")
        section = doc.metadata.get("section", "?")
        parts.append(
            f"[{i}] {source} — {section} (distance {distance:.3f})\n"
            f"{doc.page_content.strip()}"
        )
    # A visible separator helps the model treat these as distinct documents
    # rather than one run-on passage.
    return "\n\n---\n\n".join(parts)


def usable(hits: list[tuple], threshold: float = DISTANCE_THRESHOLD) -> list[tuple]:
    """Drop hits that are too far away to be worth showing the model.

    Without this, a question the runbook cannot answer still arrives with three
    confident-looking passages attached, and the model will use them.
    """
    return [(doc, dist) for doc, dist in hits if dist <= threshold]
