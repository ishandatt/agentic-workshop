"""Step 1: one alert, all the way through, with a receipt at the end.

Runs the full pipeline over several alerts and prints, for each: what each stage
produced, what the policy decided, and what the whole incident cost.

The last part matters more than it sounds. Every module added a call — triage,
several tool turns, retrieval, a proposal — and nobody has yet added them up.

Run:  python modules/09-full-pipeline/01_run.py
      (run module 4's 04_ingest.py first)
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from langgraph.types import Command
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from common.metrics import session_calls

from pipeline import build_pipeline, initial_state, load_alert, open_checkpointer

console = Console()

RUN_LOG = Path(__file__).resolve().parents[2] / "data" / "runs.jsonl"

# (alert file, what the human says if asked)
# Three alerts chosen to exercise three different endings: approved and
# executed, approved-request-refused-by-a-human, and a hostile alert that the
# pipeline contains without anyone needing to intervene.
SCENARIOS = [
    ("payment_error_spike", {"approved": True, "by": "priya.raghavan"}),
    ("payment_settlement_window", {"approved": False, "by": "tom.oyelaran"}),
    ("injection_authority", None),
]


def show_stages(state: dict):
    """Print what each stage produced, in order."""
    flags = state.get("injection_flags") or []
    console.print(f"  [bold]screen[/bold]      "
                  f"{'[red]' + ', '.join(flags) + '[/red]' if flags else 'clean'}")

    t = state.get("triage") or {}
    console.print(f"  [bold]triage[/bold]      {t.get('severity', '?')} "
                  f"(confidence {t.get('confidence', '?')}) — {str(t.get('summary', ''))[:62]}")

    console.print(f"  [bold]investigate[/bold] {len(state.get('evidence', []))} tool call(s): "
                  f"{', '.join(e['tool'] for e in state.get('evidence', [])) or 'none'}")

    sections = [c["section"][:30] for c in state.get("runbook", [])]
    console.print(f"  [bold]consult[/bold]     {len(sections)} runbook section(s): "
                  f"{'; '.join(sections) if sections else 'none kept'}")

    p = state.get("proposal") or {}
    console.print(f"  [bold]propose[/bold]     [cyan]{p.get('action')}[/cyan] on "
                  f"{p.get('service')}")
    console.print(f"              [dim]{str(p.get('reason', ''))[:100]}[/dim]")

    pol = state.get("policy") or {}
    colour = "green" if pol.get("allowed") else "red"
    console.print(f"  [bold]guard[/bold]       [{colour}]allowed={pol.get('allowed')}[/{colour}] "
                  f"approval={pol.get('requires_approval')} — {str(pol.get('reason'))[:66]}")


results = []

with open_checkpointer() as checkpointer:
    checkpointer.setup()
    graph = build_pipeline(checkpointer)

    for alert_name, answer in SCENARIOS:
        console.rule(f"[bold]{alert_name}[/bold]")
        alert = load_alert(alert_name)

        # Snapshot the metrics list length so we can attribute only THIS run's
        # calls, not everything the process has done.
        before = len(session_calls())
        started = datetime.now(timezone.utc)

        thread = {"configurable": {"thread_id": f"pipeline-{alert_name}"}}
        state = graph.invoke(initial_state(alert), thread)

        interrupts = state.get("__interrupt__")
        if interrupts:
            payload = interrupts[0].value
            show_stages(state)
            console.print()
            console.print(Panel(
                f"[bold]{payload['proposal']['action']}[/bold] on "
                f"{payload['proposal']['service']}\n\n{payload['proposal']['reason']}",
                title="[yellow]⏸ awaiting approval[/yellow]",
                border_style="yellow", expand=False,
            ))
            if answer is None:
                console.print("[dim]  (no decision supplied — leaving it pending)[/dim]\n")
                state = graph.get_state(thread).values
            else:
                console.print(f"  [bold]human[/bold]       {answer}\n")
                state = graph.invoke(Command(resume=answer), thread)
        else:
            show_stages(state)

        calls = session_calls()[before:]
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()

        decision = state.get("decision") or "pending"
        colour = {"approved": "green", "rejected": "red"}.get(decision, "yellow")
        console.print(f"\n  [bold]outcome[/bold]     [{colour}]{decision}[/{colour}] — "
                      f"{state.get('outcome') or 'awaiting a human'}")
        console.print(f"  [bold]cost[/bold]        {len(calls)} model call(s), "
                      f"{sum(c.total_tokens for c in calls)} tokens, "
                      f"${sum(c.ref_cost_usd for c in calls):.4f}, {elapsed:.1f}s\n")

        results.append({
            "alert": alert_name,
            "started_at": started.isoformat(),
            "elapsed_s": round(elapsed, 2),
            "injection_flags": state.get("injection_flags", []),
            "triage": state.get("triage"),
            "evidence_count": len(state.get("evidence", [])),
            "runbook_sections": [c["section"] for c in state.get("runbook", [])],
            "proposal": state.get("proposal"),
            "policy": state.get("policy"),
            "decision": decision,
            "decided_by": state.get("decided_by"),
            "outcome": state.get("outcome"),
            "calls": len(calls),
            "tokens": sum(c.total_tokens for c in calls),
            "ref_cost_usd": round(sum(c.ref_cost_usd for c in calls), 6),
            "steps": [{"step": c.step, "tokens": c.total_tokens,
                       "latency_s": round(c.latency_s, 2)} for c in calls],
        })

# --- the receipt --------------------------------------------------------------
console.rule("[bold]Per-incident cost[/bold]")

table = Table()
table.add_column("alert")
table.add_column("decision")
table.add_column("calls", justify="right")
table.add_column("tokens", justify="right")
table.add_column("ref cost", justify="right")
table.add_column("seconds", justify="right")
for r in results:
    table.add_row(r["alert"][:26], r["decision"], str(r["calls"]),
                  str(r["tokens"]), f"${r['ref_cost_usd']:.4f}", f"{r['elapsed_s']:.1f}")
console.print(table)

total_tokens = sum(r["tokens"] for r in results)
total_cost = sum(r["ref_cost_usd"] for r in results)
console.print(f"\n[bold]{len(results)} incidents · {total_tokens} tokens · "
              f"${total_cost:.4f} at reference prices[/bold]")
console.print(f"[dim]Average per incident: {total_tokens // len(results)} tokens, "
              f"${total_cost / len(results):.4f}. At 200 alerts a day that is "
              f"${total_cost / len(results) * 200 * 30:.0f} a month.[/dim]\n")

# --- the structured log -------------------------------------------------------
# One JSON object per run, appended. This is the artefact an incident review
# reads: what was proposed, on what evidence, who decided, and what it cost.
with RUN_LOG.open("a") as fh:
    for r in results:
        fh.write(json.dumps(r) + "\n")

console.print(f"[dim]Appended {len(results)} structured run records to "
              f"{RUN_LOG.relative_to(Path(__file__).resolve().parents[2])}[/dim]\n")

console.print(
    "[bold]What that file is for.[/bold]\n"
    "[dim]Every run records the alert, each stage's output, the policy decision,\n"
    "who approved it, and the per-step token cost. That is enough to answer the\n"
    "questions a post-incident review actually asks — why did it propose that,\n"
    "what did it look at, who said yes — without anyone having to reproduce the\n"
    "run.\n\n"
    "It is also the input to the next thing you would build: aggregate it and\n"
    "you have a dashboard; diff two weeks of it and you have a regression\n"
    "report; sample it and you have your next evaluation set.[/dim]\n"
)
