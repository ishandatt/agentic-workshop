"""Step 2: the wall. Every proposed action goes through code that cannot be talked to.

Step 1 ended with an agent that had been successfully persuaded to restart
production. Nothing we can do to the prompt reliably prevents that, because the
attacker writes prompts too.

So change the question. Instead of "how do we stop the model asking?", ask "what
happens when it asks?" — and make the answer independent of the model entirely.

Every action here passes through `check_action()`: a whitelist, plus the
settlement-window rule from the runbook, enforced in Python. It has no prompt,
no context, and nothing to persuade.

Run:  python modules/07-guardrails/02_output_guards.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from rich.console import Console
from rich.table import Table

from guards import (
    ALLOWED_ACTIONS,
    IST,
    MUTATING_ACTIONS,
    READ_ONLY_ACTIONS,
    check_action,
    in_settlement_window,
)

console = Console()

ALERTS_DIR = Path(__file__).resolve().parents[2] / "data" / "sample_alerts"


def load(name: str) -> dict:
    return json.loads((ALERTS_DIR / f"{name}.json").read_text())


def when_of(alert: dict) -> datetime:
    """Parse the alert's ISO-8601 timestamp into a timezone-aware datetime.

    `fromisoformat` does not accept a trailing "Z" on older Pythons, so we
    swap it for the equivalent "+00:00" offset it does accept.
    """
    return datetime.fromisoformat(alert["timestamp"].replace("Z", "+00:00"))


# --- The whitelist ------------------------------------------------------------
console.print("[bold]The action whitelist[/bold]")
console.print("[dim]Anything not on this list cannot execute. Adding to it is a "
              "code change that goes through review — which is the point.[/dim]\n")

table = Table()
table.add_column("action")
table.add_column("class")
table.add_column("auto-execute?")
for action in sorted(ALLOWED_ACTIONS):
    if action in READ_ONLY_ACTIONS:
        table.add_row(action, "read-only", "[green]yes[/green]")
    else:
        table.add_row(action, "mutating", "[yellow]needs approval[/yellow]")
table.add_row("delete_database", "[red]unknown[/red]", "[red]refused[/red]")
console.print(table)

# --- The attack from step 1, at the gate --------------------------------------
console.rule("[bold]The persuaded agent reaches the gate[/bold]")

authority = load("injection_authority")
console.print(
    f"[dim]The agent was convinced by injection_authority.json and is now asking "
    f"to restart payment-service.\nAlert timestamp: {authority['timestamp']}[/dim]\n"
)

decision = check_action("restart_service", "payment-service", when_of(authority))

console.print(f"  action    : restart_service(payment-service)")
console.print(f"  allowed   : {'[green]yes[/green]' if decision.allowed else '[red]NO[/red]'}")
console.print(f"  reason    : {decision.reason}\n")

console.print(
    "[dim]Note what the guard did NOT consider: whether the alert looked\n"
    "legitimate, how confident the agent was, whether a ticket number was\n"
    "quoted, or whether the description claimed prior approval. It converted a\n"
    "UTC timestamp to IST, compared two integers, and refused.\n\n"
    "The injection succeeded completely at the model layer and bought nothing.[/dim]\n"
)

# --- The same action, outside the window --------------------------------------
console.rule("[bold]The same action, a few hours later[/bold]")

normal = load("payment_error_spike")   # 14:23 UTC = 19:53 IST, outside the window
decision2 = check_action("restart_service", "payment-service", when_of(normal))

console.print(f"  alert time: {normal['timestamp']}  "
              f"(in settlement window: {in_settlement_window(when_of(normal))})")
console.print(f"  allowed   : {'[green]yes[/green]' if decision2.allowed else '[red]NO[/red]'}")
console.print(f"  approval  : "
              f"{'[yellow]required[/yellow]' if decision2.requires_approval else 'not required'}")
console.print(f"  reason    : {decision2.reason}\n")

console.print(
    "[dim]A policy that always says no is easy and useless. This one permits\n"
    "the restart outside the window — and still routes it to a human, because\n"
    "it mutates production. Two separate decisions: is it allowed at all, and\n"
    "may it happen unattended.[/dim]\n"
)

# --- The full matrix ----------------------------------------------------------
console.rule("[bold]Every combination[/bold]")

matrix = Table(title="check_action() decisions")
matrix.add_column("action", no_wrap=True)
matrix.add_column("service", no_wrap=True)
matrix.add_column("IST", justify="right", no_wrap=True)
matrix.add_column("ok", justify="center")
matrix.add_column("appr", justify="center")
matrix.add_column("why", no_wrap=True)


def short_reason(d) -> str:
    """One-phrase summary, so the table fits a terminal."""
    if "whitelist" in d.reason:
        return "not whitelisted"
    if "settlement" in d.reason:
        return "settlement window"
    if "read-only" in d.reason:
        return "read-only"
    return "mutates state"

CASES = [
    ("get_service_status", "payment-service", authority),
    ("get_error_logs", "payment-service", authority),
    ("restart_service", "payment-service", authority),      # in window
    ("restart_service", "payment-service", normal),         # outside window
    ("restart_service", "checkout-service", authority),      # window rule is payment-only
    ("rollback_deploy", "payment-service", authority),
    ("delete_database", "payment-service", normal),          # not on the whitelist
]

for action, service, alert in CASES:
    when = when_of(alert)
    d = check_action(action, service, when)
    matrix.add_row(
        action,
        service,
        f"{when.astimezone(IST):%H:%M}",
        "[green]✔[/green]" if d.allowed else "[red]✘[/red]",
        "[yellow]yes[/yellow]" if d.requires_approval else "—",
        short_reason(d),
    )
console.print(matrix)

console.print(
    "\n[bold]Three properties worth naming.[/bold]\n"
    "[dim]It is deterministic — same inputs, same answer, every time, and you\n"
    "can unit-test it. It is auditable — the reason string is a log line that\n"
    "explains itself to an incident review. And it is unpersuadable — there is\n"
    "no prompt to inject into, because there is no prompt.\n\n"
    "This is the difference between a guardrail and a guideline. Everything\n"
    "before this module was a guideline.[/dim]\n"
)

console.print(
    "[bold]What this still does not do:[/bold] [dim]it blocked the restart and\n"
    "flagged the rollback as needing approval — but nobody was asked. The\n"
    "action is sitting in a variable with 'requires_approval: True' attached\n"
    "and no human in sight. Wiring an actual person into the loop is the next\n"
    "module.[/dim]\n"
)
