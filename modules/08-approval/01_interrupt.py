"""Step 1: watch the agent stop mid-run, then start again.

Three runs of the same graph, differing only in what the human says:

    approved     the rollback executes
    rejected     it does not
    forbidden    policy refuses before anyone is asked

The thing to watch is the shape of the interaction. The graph runs, stops, and
returns control to us — with the run still alive in Postgres, waiting.

Run:  python modules/08-approval/01_interrupt.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from langgraph.types import Command
from rich.console import Console
from rich.panel import Panel

from approval_graph import build_graph, load_alert, open_checkpointer

console = Console()


def run_scenario(graph, label: str, alert_name: str, answer: dict | None):
    """Start a run, and if it pauses, resume it with `answer`."""
    console.rule(f"[bold]{label}[/bold]")

    alert = load_alert(alert_name)

    # A thread_id names this run. It is the key the checkpointer stores state
    # under, and the handle you resume with — so it must be unique per incident
    # and stable across processes.
    thread = {"configurable": {"thread_id": f"demo-{label.replace(' ', '-')}"}}

    state = graph.invoke(
        {"alert": alert, "messages": [], "proposed_action": None,
         "decision": None, "decided_by": None, "outcome": None},
        thread,
    )

    # When a graph interrupts, the returned state carries `__interrupt__`.
    # Its presence is how you know the run is paused rather than finished.
    interrupts = state.get("__interrupt__")

    if not interrupts:
        console.print(f"[dim]Ran to completion without pausing.[/dim]")
        console.print(f"  outcome: [yellow]{state.get('outcome')}[/yellow]\n")
        return

    payload = interrupts[0].value
    action = payload["action"]

    console.print(Panel(
        f"[bold]{action['action']}[/bold] on [bold]{action['service']}[/bold]\n\n"
        f"{action['reason']}",
        title="[yellow]⏸ waiting for a human[/yellow]",
        border_style="yellow", expand=False,
    ))

    console.print(
        "[dim]The graph has stopped. Its state is in Postgres, and this process\n"
        "could exit right now without losing it.[/dim]\n"
    )

    if answer is None:
        # This scenario expected policy to refuse before anyone was asked. If we
        # are here, the routing is wrong — say so loudly rather than resuming
        # with nothing, which crashes deep inside LangGraph.
        console.print("[red]Unexpected pause: this run should have been refused "
                      "by policy before reaching a human.[/red]\n")
        return

    console.print(f"[bold]Human says:[/bold] {answer}\n")

    # `Command(resume=...)` re-enters the graph at the interrupted node. The
    # value becomes the return value of `interrupt()` inside that node.
    final = graph.invoke(Command(resume=answer), thread)

    colour = "green" if final.get("decision") == "approved" else "red"
    console.print(f"  decision: [{colour}]{final.get('decision')}[/{colour}] "
                  f"(by {final.get('decided_by')})")
    console.print(f"  outcome : {final.get('outcome')}\n")


# The checkpointer is a context manager: it owns a database connection, so it
# opens once here and every run below shares it.
with open_checkpointer() as checkpointer:
    checkpointer.setup()          # create the checkpoint tables if needed
    graph = build_graph(checkpointer)

    run_scenario(graph, "approved", "payment_error_spike",
                 {"approved": True, "by": "priya.raghavan"})

    run_scenario(graph, "rejected", "payment_error_spike",
                 {"approved": False, "by": "tom.oyelaran"})

    # This alert fires inside the settlement window, so module 7's policy
    # refuses before any human is involved.
    run_scenario(graph, "forbidden by policy", "payment_settlement_window", None)

console.print(
    "[bold]What to take from the third run.[/bold]\n"
    "[dim]Nobody was asked. The guard refused the action outright, so the graph\n"
    "never reached the approval node.\n\n"
    "That ordering is deliberate: guard first, then ask. Interrupting a human\n"
    "to approve something policy already forbids trains them to click approve,\n"
    "and burns the one resource an approval gate depends on — the reviewer's\n"
    "attention.[/dim]\n"
)

console.print(
    "[bold]And the mechanism, in one paragraph.[/bold]\n"
    "[dim]`interrupt()` saved the entire graph state to Postgres and raised out\n"
    "of the run. `Command(resume=...)` loaded it back, re-entered the paused\n"
    "node, and returned the answer as the value of the `interrupt()` call.\n\n"
    "Note the consequence: everything ABOVE the interrupt in that node runs\n"
    "twice — once before the pause, once on resume. Keep side effects out of\n"
    "it, or you will send two Slack messages for every approval.[/dim]\n"
)
