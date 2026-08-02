"""Step 2: prove the pause survives the process dying.

Step 1 paused and resumed inside one program, which a plain function call could
have done. The claim worth testing is stronger: the run is in Postgres, so the
process can exit and something else can pick it up later.

This script is the test. Run it twice:

    python modules/08-approval/02_persistence.py            # starts, pauses, exits
    python modules/08-approval/02_persistence.py --approve  # resumes and finishes

Between those two commands there is no running process holding anything. The
incident exists only as rows in a database.
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

# A fixed thread id, so the second invocation can find what the first one left.
# In a real system this is your incident id.
THREAD_ID = "incident-persistence-demo"
THREAD = {"configurable": {"thread_id": THREAD_ID}}

# `sys.argv` is the list of command-line arguments; argv[0] is the script name.
approving = "--approve" in sys.argv
rejecting = "--reject" in sys.argv

with open_checkpointer() as checkpointer:
    checkpointer.setup()
    graph = build_graph(checkpointer)

    # `get_state` reads whatever is stored for this thread without running
    # anything. This is how a separate process discovers a paused run.
    snapshot = graph.get_state(THREAD)
    already_running = bool(snapshot.created_at)

    if approving or rejecting:
        # ---- second invocation: resume ------------------------------------
        if not already_running or not snapshot.next:
            console.print("[red]Nothing is waiting for a decision.[/red]")
            console.print("[dim]Run this script with no arguments first.[/dim]")
            sys.exit(1)

        console.print(Panel(
            f"Found a paused run for thread [bold]{THREAD_ID}[/bold]\n"
            f"stopped at node: [bold]{snapshot.next}[/bold]\n"
            f"proposed: {snapshot.values.get('proposed_action', {}).get('action')} "
            f"on {snapshot.values.get('proposed_action', {}).get('service')}",
            title="[cyan]loaded from Postgres[/cyan]", border_style="cyan", expand=False,
        ))

        who = "priya.raghavan" if approving else "tom.oyelaran"
        console.print(f"\n[bold]Resuming with[/bold] approved={approving} by {who}\n")

        final = graph.invoke(
            Command(resume={"approved": approving, "by": who}), THREAD
        )
        console.print(f"  decision: {final.get('decision')} (by {final.get('decided_by')})")
        console.print(f"  outcome : {final.get('outcome')}\n")

        console.print(
            "[dim]Nothing was held in memory between the two commands. The graph\n"
            "reconstructed itself from the checkpoint, re-entered the node it had\n"
            "stopped in, and carried on.[/dim]\n"
        )

    else:
        # ---- first invocation: start and pause ----------------------------
        if already_running and snapshot.next:
            console.print("[yellow]A run is already paused for this thread.[/yellow]")
            console.print("[dim]Resume it with --approve or --reject, or change "
                          "THREAD_ID to start fresh.[/dim]\n")
            sys.exit(0)

        alert = load_alert("payment_error_spike")
        state = graph.invoke(
            {"alert": alert, "messages": [], "proposed_action": None,
             "decision": None, "decided_by": None, "outcome": None},
            THREAD,
        )

        interrupts = state.get("__interrupt__")
        if not interrupts:
            console.print(f"[yellow]Finished without pausing: "
                          f"{state.get('outcome')}[/yellow]")
            sys.exit(0)

        action = interrupts[0].value["action"]
        console.print(Panel(
            f"[bold]{action['action']}[/bold] on [bold]{action['service']}[/bold]\n\n"
            f"{action['reason']}",
            title="[yellow]⏸ paused, awaiting a human[/yellow]",
            border_style="yellow", expand=False,
        ))

        console.print(
            f"\n[bold]This process is about to exit.[/bold]\n"
            f"[dim]The run lives on in Postgres under thread id "
            f"{THREAD_ID!r}.[/dim]\n\n"
            f"  approve:  python modules/08-approval/02_persistence.py --approve\n"
            f"  reject :  python modules/08-approval/02_persistence.py --reject\n"
        )
