"""Step 3: embeddings — turning meaning into arithmetic.

Chunking gave us pieces of text. Now we need to find the *relevant* piece when a
question arrives. Keyword search would fail immediately: someone asking "can I
bounce the payments box right now?" shares almost no words with a runbook
section titled "the settlement window".

An **embedding** is a list of numbers representing the meaning of a piece of
text. Texts that mean similar things get vectors that point in similar
directions — which turns "is this relevant?" into a distance calculation.

That is the whole idea. This script makes it concrete on our own runbook.

Run:  python modules/04-rag-ingestion/03_embeddings.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_ollama import OllamaEmbeddings
from rich.console import Console
from rich.table import Table

from common.config import EMBED_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

console = Console()

# A different model from the chat one. Embedding models do not generate text at
# all — you cannot chat with nomic-embed-text. They read text and emit a vector.
embeddings = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)

# --- What does one actually look like? ---------------------------------------
sample = "The payment service must never be restarted between 14:00 and 16:00 IST."

with track("embed-one") as m:
    vector = embeddings.embed_query(sample)
    # Embedding calls report no token usage through LangChain, so we record the
    # input size by hand — roughly 4 characters per token.
    m.record_raw(input_tokens=len(sample) // 4, output_tokens=0)

console.print(f"[bold]Text:[/bold] {sample!r}")
console.print(f"[bold]Vector:[/bold] {len(vector)} dimensions")
# Slicing shows the first five numbers; the rest look exactly the same.
console.print(f"  first 5 values: {[round(v, 4) for v in vector[:5]]}")
console.print(
    f"\n[dim]That is the entire representation. {len(vector)} floating-point\n"
    "numbers, and nothing human-readable in any single one of them. The meaning\n"
    "is in the DIRECTION the whole vector points, not in any individual value.[/dim]\n"
)


# --- Comparing meanings -------------------------------------------------------
def cosine_similarity(a: list[float], b: list[float]) -> float:
    """How aligned are two vectors? 1.0 = identical direction, 0.0 = unrelated.

    The formula is the dot product divided by the product of the lengths.
    Written out with `sum(...)` rather than numpy so you can see every step:

      dot     how much the two vectors agree, component by component
      norm    the length of a vector, by Pythagoras in many dimensions
    """
    # `zip(a, b)` walks two lists in step, yielding pairs.
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5   # ** 0.5 is a square root
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b)


# A question phrased the way a tired engineer would actually type it, plus
# candidate answers. Note the question shares almost no vocabulary with the
# rule that answers it.
QUESTION = "can I bounce the payments box right now?"

CANDIDATES = [
    "The payment service must never be restarted between 14:00 and 16:00 IST.",
    "checkout-service p99 latency rises during marketing campaigns and this is expected.",
    "log-aggregator disk grows at roughly 2% per day against a 14-day retention policy.",
    "Escalate to Priya Raghavan via the payments-platform-primary PagerDuty schedule.",
    "The cat sat on the mat.",
]

with track("embed-batch") as m:
    # embed_documents takes a list and returns a list of vectors — one call
    # instead of five, which matters when you are embedding thousands of chunks.
    candidate_vectors = embeddings.embed_documents(CANDIDATES)
    question_vector = embeddings.embed_query(QUESTION)
    total_chars = sum(len(c) for c in CANDIDATES) + len(QUESTION)
    m.record_raw(input_tokens=total_chars // 4, output_tokens=0)

console.print(f"[bold]Question:[/bold] {QUESTION!r}\n")

# Score every candidate, then sort best-first.
scored = [
    (cosine_similarity(question_vector, vec), text)
    for vec, text in zip(candidate_vectors, CANDIDATES)
]
# `reverse=True` sorts high-to-low. The key picks the number out of each pair.
scored.sort(key=lambda pair: pair[0], reverse=True)

table = Table(title="Similarity to the question")
table.add_column("score", justify="right")
table.add_column("text")
for score, text in scored:
    style = "green" if score == scored[0][0] else "dim"
    table.add_row(f"{score:.3f}", f"[{style}]{text[:78]}[/{style}]")
console.print(table)

console.print(
    "\n[dim]Look at the winner and count the words it shares with the question.\n"
    "'can I bounce the payments box right now?' and 'The payment service must\n"
    "never be restarted between 14:00 and 16:00 IST' have almost nothing in\n"
    "common lexically — 'payment' and little else. Keyword search would rank\n"
    "this no higher than the cat.\n\n"
    "The embedding model has learned that 'bounce a box' and 'restart a\n"
    "service' occupy nearly the same place in meaning-space. That is what makes\n"
    "retrieval work on questions people actually ask.[/dim]\n"
)

console.print(
    "[bold]Two properties worth carrying forward:[/bold]\n"
    "[dim]1. Scores are RELATIVE. There is no absolute threshold above which a\n"
    "   chunk is 'relevant' — even the cat scores well above zero. You take the\n"
    "   top N, and you decide what N is.\n"
    "2. The same model must embed both sides. A vector from nomic-embed-text is\n"
    "   meaningless next to one from another model, so changing the embedding\n"
    "   model means re-embedding everything you have stored.[/dim]\n"
)

session_report()
