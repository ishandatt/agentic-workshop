"""Step 3: the approval queue as an HTTP service.

An incident arrives on POST /alert. If it needs a human, the request returns
immediately with a pending id — it does not block waiting for someone to wake
up. The decision arrives later, from a different client, possibly the next day.

    POST /alert            -> {"status": "awaiting_approval", "id": "..."}
    GET  /pending          -> everything waiting for a person
    POST /approve/{id}     -> resume, execute, return the outcome
    POST /reject/{id}      -> resume, do nothing, record who said no

The LangGraph checkpointer already stores the run. We add one small table of
our own on top of it, because a checkpoint is not a queue: it can tell you the
state of a thread you name, but it cannot answer "what is waiting for me?".

Run it:
    python modules/08-approval/03_api.py

Then:
    curl -s -X POST http://127.0.0.1:8000/alert -H 'Content-Type: application/json' \\
      -d @data/sample_alerts/payment_error_spike.json | python3 -m json.tool
    curl -s http://127.0.0.1:8000/pending | python3 -m json.tool
    curl -s -X POST http://127.0.0.1:8000/approve/<id> \\
      -H 'Content-Type: application/json' -d '{"by":"priya.raghavan"}' | python3 -m json.tool
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

import psycopg
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from langgraph.types import Command
from pydantic import BaseModel
from rich.console import Console

from common.config import DATABASE_URL

from approval_graph import build_graph, open_checkpointer

console = Console()

# `lifespan` is FastAPI's startup/shutdown hook. Code before `yield` runs once
# when the server starts; code after it runs on shutdown. It replaces the older
# @app.on_event("startup"), which is deprecated.
@asynccontextmanager
async def lifespan(app: FastAPI):
    with db() as conn:
        conn.execute(CREATE_TABLE)
    with open_checkpointer() as cp:
        cp.setup()
    console.print("[green]✔ tables ready[/green]")
    yield


app = FastAPI(title="Incident approval queue", version="0.1.0", lifespan=lifespan)


# --- our own small table ------------------------------------------------------
# Why not just use the checkpointer? Because it is keyed by thread id: perfect
# for "resume this run", useless for "show me everything waiting". A queue needs
# to be listable, and that is a different access pattern.
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS pending_approvals (
    id            TEXT PRIMARY KEY,
    thread_id     TEXT NOT NULL,
    service       TEXT NOT NULL,
    action        TEXT NOT NULL,
    reason        TEXT NOT NULL,
    alert         JSONB NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    decided_by    TEXT,
    outcome       TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at    TIMESTAMPTZ
);
"""


def db():
    """One short-lived connection per operation.

    Fine at workshop scale and honest about what it is. A real service would
    hold a connection pool — see the bonus module on connection lifecycles.
    """
    return psycopg.connect(DATABASE_URL)


class Alert(BaseModel):
    service: str
    severity: Literal["info", "warning", "critical"]
    metric: str
    value: float
    description: str
    timestamp: datetime


class Decision(BaseModel):
    by: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/alert")
def receive_alert(alert: Alert):
    """Start an incident. Returns immediately, whether or not it needs a human."""
    incident_id = str(uuid.uuid4())[:8]
    thread = {"configurable": {"thread_id": incident_id}}
    payload = alert.model_dump(mode="json")

    console.print(f"[bold]→ alert[/bold] {alert.service} ({incident_id})")

    with open_checkpointer() as checkpointer:
        graph = build_graph(checkpointer)
        state = graph.invoke(
            {"alert": payload, "messages": [], "proposed_action": None,
             "decision": None, "decided_by": None, "outcome": None},
            thread,
        )

    interrupts = state.get("__interrupt__")
    if not interrupts:
        # Ran to completion — either policy refused it, or it needed no approval.
        console.print(f"[dim]  completed: {state.get('outcome')}[/dim]")
        return {"id": incident_id, "status": "completed",
                "outcome": state.get("outcome")}

    action = interrupts[0].value["action"]
    with db() as conn:
        conn.execute(
            "INSERT INTO pending_approvals "
            "(id, thread_id, service, action, reason, alert) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (incident_id, incident_id, action["service"], action["action"],
             action["reason"], psycopg.types.json.Jsonb(payload)),
        )

    console.print(f"[yellow]  ⏸ awaiting approval: {action['action']}[/yellow]")
    return {"id": incident_id, "status": "awaiting_approval",
            "action": action["action"], "service": action["service"],
            "reason": action["reason"]}


@app.get("/pending")
def list_pending():
    """Everything waiting for a person. This is the on-call inbox."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, service, action, reason, created_at FROM pending_approvals "
            "WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
    return {
        "pending": [
            {"id": r[0], "service": r[1], "action": r[2],
             "reason": r[3], "created_at": r[4].isoformat()}
            for r in rows
        ]
    }


def _decide(incident_id: str, approved: bool, by: str):
    """Shared body of approve and reject — they differ by one boolean."""
    with db() as conn:
        row = conn.execute(
            "SELECT thread_id, status FROM pending_approvals WHERE id = %s",
            (incident_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(404, f"no such incident {incident_id!r}")
    thread_id, status = row
    if status != "pending":
        # Guard against double-approval: a second click must not run the action
        # twice. This is the sort of thing that only shows up in production.
        raise HTTPException(409, f"incident {incident_id} is already {status}")

    with open_checkpointer() as checkpointer:
        graph = build_graph(checkpointer)
        final = graph.invoke(
            Command(resume={"approved": approved, "by": by}),
            {"configurable": {"thread_id": thread_id}},
        )

    outcome = final.get("outcome")
    with db() as conn:
        conn.execute(
            "UPDATE pending_approvals SET status = %s, decided_by = %s, "
            "outcome = %s, decided_at = %s WHERE id = %s",
            ("approved" if approved else "rejected", by, outcome,
             datetime.now(timezone.utc), incident_id),
        )

    console.print(f"[bold]{'✔ approved' if approved else '✘ rejected'}[/bold] "
                  f"{incident_id} by {by}")
    return {"id": incident_id, "decision": final.get("decision"),
            "decided_by": final.get("decided_by"), "outcome": outcome}


@app.post("/approve/{incident_id}")
def approve(incident_id: str, decision: Decision):
    return _decide(incident_id, True, decision.by)


@app.post("/reject/{incident_id}")
def reject(incident_id: str, decision: Decision):
    return _decide(incident_id, False, decision.by)


if __name__ == "__main__":
    import uvicorn

    console.print("[bold]Approval queue on http://127.0.0.1:8000[/bold]")
    console.print("[dim]docs: http://127.0.0.1:8000/docs[/dim]\n")
    uvicorn.run(app, host="127.0.0.1", port=8000)
