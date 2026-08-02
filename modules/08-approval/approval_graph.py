"""An agent that stops. Everything in this module is built on that one idea.

Module 7 ended with a guard that correctly flagged a rollback as needing human
approval — and then nobody was asked, because there was no mechanism to ask
anyone. The action sat in a variable with `requires_approval: True` attached and
the program exited.

This file adds the mechanism. The graph pauses before any mutating action, and
"pauses" here means something stronger than blocking a thread: the run is
serialised to Postgres and the process is free to exit. Somebody approves it
tomorrow, from a different process, and the agent picks up mid-thought.

The mechanism is `interrupt()`, and it works because of the property LangGraph
had all along and we did not need until now: the graph's state is DATA, so it
can be written to a database and read back.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "07-guardrails"))

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

# `interrupt()` stops the graph and hands a payload out to the caller.
# `Command(resume=...)` is how the caller sends an answer back in.
from langgraph.types import Command, interrupt

from common.config import DATABASE_URL

from guards import check_action

# The checkpointer needs a plain psycopg connection string — no SQLAlchemy
# driver prefix, unlike the vector store in module 4.
CHECKPOINT_CONNECTION = DATABASE_URL


class IncidentState(TypedDict):
    """The state carried through the graph.

    A `TypedDict` is a dict with declared keys — dict at runtime, checked by
    tools. LangGraph serialises this whole structure to Postgres at every step,
    which is why everything in it must be JSON-friendly.

    `Annotated[list, add_messages]` attaches a REDUCER: when a node returns
    `messages`, they are appended rather than replacing the list. Every other
    key here replaces on write.
    """

    messages: Annotated[list, add_messages]
    alert: dict
    proposed_action: dict | None
    decision: str | None          # "approved" | "rejected" | None
    decided_by: str | None
    outcome: str | None


def propose(state: IncidentState) -> dict:
    """Decide what to do about the alert.

    A real version calls the model and the tools from module 3. This one is
    deliberately hard-coded, because the subject of this module is the PAUSE,
    and a non-deterministic proposal would make the pause hard to demonstrate.
    """
    alert = state["alert"]

    # A crude two-branch rule standing in for module 3's investigation. If there
    # is a deploy to blame, the runbook prefers a rollback; with no deploy in
    # the window, a restart is the obvious next move.
    if "no deploys" in alert["description"].lower():
        action = {
            "action": "restart_service",
            "service": alert["service"],
            "reason": (
                "Workers are wedged and no deploy is implicated, so a restart is "
                "the standard remediation."
            ),
        }
    else:
        action = {
            "action": "rollback_deploy",
            "service": alert["service"],
            "reason": (
                "Deploy 9f2a41c reduced the settlement pool from 50 to 5, below the "
                "documented floor of 40. The runbook prefers rollback over restart."
            ),
        }
    return {
        "proposed_action": action,
        "messages": [{"role": "assistant",
                      "content": f"Proposing {action['action']} on {action['service']}."}],
    }


def guard(state: IncidentState) -> dict:
    """Run module 7's output guard over the proposal.

    Note the order: guard BEFORE asking a human. There is no point interrupting
    someone to approve an action that policy forbids outright — and asking makes
    it likelier that somebody eventually approves one.
    """
    action = state["proposed_action"]
    when = datetime.fromisoformat(state["alert"]["timestamp"].replace("Z", "+00:00"))
    decision = check_action(action["action"], action["service"], when)

    if not decision.allowed:
        return {
            "decision": "rejected",
            "decided_by": "policy",
            "outcome": f"blocked by policy: {decision.reason}",
        }
    # Allowed, but mutating: fall through to the approval gate.
    return {}


def needs_approval(state: IncidentState) -> str:
    """Route: policy already refused, needs a human, or safe to run."""
    if state.get("decision") == "rejected":
        return "finish"

    action = state["proposed_action"]
    when = datetime.fromisoformat(state["alert"]["timestamp"].replace("Z", "+00:00"))
    if check_action(action["action"], action["service"], when).requires_approval:
        return "await_approval"
    return "execute"


def await_approval(state: IncidentState) -> dict:
    """Stop here and wait for a person.

    `interrupt(payload)` does two things. It hands `payload` out to whoever is
    running the graph — so an API can show it to a human — and it saves the
    entire state to the checkpointer and raises out of the run.

    The subtle part: when the graph is resumed, this node runs AGAIN from the
    top, and `interrupt()` returns the value that was sent in rather than
    interrupting a second time. So treat everything above the interrupt as code
    that executes twice, and keep side effects out of it.
    """
    action = state["proposed_action"]

    answer = interrupt(
        {
            "kind": "approval_request",
            "action": action,
            "alert": state["alert"],
            "asked_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    # Execution only reaches this line after a resume.
    approved = bool(answer.get("approved"))
    return {
        "decision": "approved" if approved else "rejected",
        "decided_by": answer.get("by", "unknown"),
        "outcome": None if approved else f"rejected by {answer.get('by', 'unknown')}",
    }


def after_approval(state: IncidentState) -> str:
    return "execute" if state.get("decision") == "approved" else "finish"


def execute(state: IncidentState) -> dict:
    """Perform the action. Simulated — nothing real is touched."""
    action = state["proposed_action"]
    return {
        "outcome": f"executed {action['action']} on {action['service']} (simulated)",
        "messages": [{"role": "assistant",
                      "content": f"Executed {action['action']}."}],
    }


def finish(state: IncidentState) -> dict:
    """Terminal bookkeeping, so every path ends with an outcome string."""
    if state.get("outcome"):
        return {}
    return {"outcome": "no action taken"}


def build_graph(checkpointer):
    """Wire the nodes together.

    A checkpointer is REQUIRED for interrupts. Without one there is nowhere to
    save the paused state, so there is nothing to resume from — LangGraph will
    tell you so, somewhat tersely.
    """
    builder = StateGraph(IncidentState)
    builder.add_node("propose", propose)
    builder.add_node("guard", guard)
    builder.add_node("await_approval", await_approval)
    builder.add_node("execute", execute)
    builder.add_node("finish", finish)

    builder.add_edge(START, "propose")
    builder.add_edge("propose", "guard")
    builder.add_conditional_edges("guard", needs_approval,
                                  ["await_approval", "execute", "finish"])
    builder.add_conditional_edges("await_approval", after_approval,
                                  ["execute", "finish"])
    builder.add_edge("execute", "finish")
    builder.add_edge("finish", END)

    return builder.compile(checkpointer=checkpointer)


def open_checkpointer():
    """Open a Postgres checkpointer as a context manager.

    `PostgresSaver.from_conn_string` yields a saver and closes the connection
    afterwards, so callers should use it in a `with` block. `.setup()` creates
    the checkpoint tables if they do not exist — safe to call every time.
    """
    return PostgresSaver.from_conn_string(CHECKPOINT_CONNECTION)


def load_alert(name: str) -> dict:
    path = Path(__file__).resolve().parents[2] / "data" / "sample_alerts" / f"{name}.json"
    return json.loads(path.read_text())
