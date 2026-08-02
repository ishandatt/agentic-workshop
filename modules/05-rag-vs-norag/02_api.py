"""Step 2: /triage/compare — send an alert, get both answers and both bills.

Module 2 served triage over HTTP. This does the same thing twice per request:
once blind, once grounded in the runbook, and returns them side by side with
the retrieved chunks and the token cost of each path.

Being able to show a stakeholder *this answer, that answer, this is the
difference in pounds* is worth more than any amount of arguing about whether
RAG is worthwhile.

Run it:
    python modules/05-rag-vs-norag/02_api.py

Then:
    curl -s -X POST http://127.0.0.1:8000/triage/compare \\
      -H 'Content-Type: application/json' \\
      -d @data/sample_alerts/payment_error_spike.json | python3 -m json.tool
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from fastapi import FastAPI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field
from rich.console import Console

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.metrics import track

from rag import DISTANCE_THRESHOLD, format_context, get_store, retrieve, usable

console = Console()

app = FastAPI(
    title="Triage comparison",
    description="Answers the same alert with and without runbook grounding.",
    version="0.1.0",
)

llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)
store = get_store()


# The alert contract, carried forward from module 2. Each module folder is a
# self-contained checkpoint, so it is copied rather than imported across.
class Alert(BaseModel):
    service: str
    severity: Literal["info", "warning", "critical"]
    metric: str
    value: float
    description: str
    timestamp: datetime


class PathResult(BaseModel):
    """One answer, plus what it cost to produce."""

    answer: str
    input_tokens: int
    output_tokens: int
    latency_s: float


class RetrievedChunk(BaseModel):
    section: str
    source: str
    distance: float
    kept: bool          # did it survive the distance filter?
    preview: str


class CompareResponse(BaseModel):
    alert: Alert
    question: str
    without_runbook: PathResult
    with_runbook: PathResult
    retrieved: list[RetrievedChunk]
    # A single number a non-engineer can act on.
    token_multiplier: float


NO_RAG_SYSTEM = "You are an SRE assistant for a payments platform. Answer in three sentences."

RAG_SYSTEM = """You are an SRE assistant for a payments platform.

Answer ONLY from the runbook extracts provided, and cite the section you used.

If the extracts do not contain the answer, say exactly: "Not covered in the \
runbook." Do not use general knowledge to fill gaps."""


def alert_question(alert: Alert) -> str:
    """Turn an alert into the question both paths are asked.

    Both paths get the identical question — otherwise the comparison measures
    prompt differences rather than the effect of retrieval.
    """
    return (
        f"Alert on {alert.service}: {alert.metric} = {alert.value} "
        f"(reported {alert.severity}). {alert.description}\n\n"
        f"What is the likely cause, and what should be done about it? "
        f"State any restrictions on what we are allowed to do."
    )


@app.get("/health")
def health():
    return {"status": "ok", "model": CHAT_MODEL, "threshold": DISTANCE_THRESHOLD}


@app.post("/triage/compare", response_model=CompareResponse)
def compare(alert: Alert):
    """Answer one alert twice and return both, with the bill."""
    question = alert_question(alert)
    console.print(f"[bold]→ comparing[/bold] {alert.service} {alert.metric}={alert.value}")

    # --- path A: no retrieval ---
    with track("no-rag", quiet=True) as m:
        plain = llm.invoke([SystemMessage(NO_RAG_SYSTEM), HumanMessage(question)])
        m.record(plain)
    a = PathResult(answer=plain.content.strip(), input_tokens=m.metrics.input_tokens,
                   output_tokens=m.metrics.output_tokens, latency_s=round(m.metrics.latency_s, 2))

    # --- path B: retrieve, filter, answer ---
    hits = retrieve(store, question, k=4)
    kept = usable(hits)

    with track("rag", quiet=True) as m:
        grounded = llm.invoke([
            SystemMessage(RAG_SYSTEM),
            HumanMessage(f"Runbook extracts:\n\n{format_context(kept)}\n\nQuestion: {question}"),
        ])
        m.record(grounded)
    b = PathResult(answer=grounded.content.strip(), input_tokens=m.metrics.input_tokens,
                   output_tokens=m.metrics.output_tokens, latency_s=round(m.metrics.latency_s, 2))

    # Report every hit, marking which survived — so a caller can see the filter
    # working instead of wondering why only two chunks were used.
    kept_ids = {id(doc) for doc, _ in kept}
    retrieved = [
        RetrievedChunk(
            section=doc.metadata.get("section", "?"),
            source=doc.metadata.get("source", "?"),
            distance=round(dist, 4),
            kept=id(doc) in kept_ids,
            preview=" ".join(doc.page_content.split())[:140],
        )
        for doc, dist in hits
    ]

    total_a = a.input_tokens + a.output_tokens
    total_b = b.input_tokens + b.output_tokens
    console.print(f"[green]✔[/green] no-RAG {total_a} tok · RAG {total_b} tok "
                  f"({total_b / total_a:.1f}×)" if total_a else "")

    return CompareResponse(
        alert=alert,
        question=question,
        without_runbook=a,
        with_runbook=b,
        retrieved=retrieved,
        token_multiplier=round(total_b / total_a, 2) if total_a else 0.0,
    )


if __name__ == "__main__":
    import uvicorn

    console.print("[bold]Comparison API on http://127.0.0.1:8000[/bold]")
    console.print("[dim]docs: http://127.0.0.1:8000/docs[/dim]\n")
    uvicorn.run(app, host="127.0.0.1", port=8000)
