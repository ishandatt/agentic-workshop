"""Step 2: the production-shaped version — retries, feedback, and honest failure.

Schema enforcement got us most of the way. This script uses the hardened
`triage()` function from triage.py, which adds the parts you need when the
thing runs unattended at 2am.

It also runs every sample alert, so you can see the model form different
opinions about different situations.

Run:  python modules/02-triage/03_defensive_triage.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rich.console import Console
from rich.table import Table

from common.display import show_messages
from common.metrics import session_report

from samples import list_alerts, load_alert
from triage import SYSTEM_PROMPT, TriageError, alert_to_prompt, triage

console = Console()

console.print("[bold]Triaging every sample alert[/bold]")
console.print(
    "[dim]verbose=True prints each attempt, so retries are visible when they "
    "happen.[/dim]\n"
)

# Collect results to display as a table at the end. A list of tuples is fine
# for this; each tuple is one row.
rows = []

for name in list_alerts():
    alert = load_alert(name)
    console.print(f"[bold]{name}[/bold] — {alert.service}, reported {alert.severity}")

    # Show what triage() sends, once, for the first alert. Every later alert
    # uses the identical shape with different values.
    if not rows:
        show_messages(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "human", "content": alert_to_prompt(alert)}],
            title="What triage() sends for every alert",
        )

    try:
        # The whole defence — schema enforcement, validation, retry with
        # feedback — is inside this one call.
        result = triage(alert, max_attempts=3, verbose=True)

        # Comparing the model's assessment against what monitoring claimed is
        # often the most interesting output. Disagreement is a signal: either
        # the threshold is mis-tuned or the model is wrong, and both are worth
        # a human's attention.
        agreement = "same" if result.severity == alert.severity else "DIFFERS"
        console.print(
            f"  [green]✔[/green] {result.severity} "
            f"(confidence {result.confidence:.2f}) — monitoring said "
            f"{alert.severity}, [bold]{agreement}[/bold]"
        )
        console.print(f"  [dim]{result.summary}[/dim]")

        # Show the first result as JSON, once. This is what actually travels to
        # the next stage of the pipeline — `model_dump_json()` serialises a
        # Pydantic object back out, and `indent=2` pretty-prints it.
        if not rows:
            console.print("\n[bold]  What the rest of the pipeline receives:[/bold]")
            console.print_json(result.model_dump_json())
        console.print()

        # The summaries are printed in full above, so the table holds only the
        # comparison — which is the part worth staring at.
        rows.append((name, alert.severity, result.severity,
                     f"{result.confidence:.2f}", agreement))

    except TriageError as e:
        # Catching our own exception type specifically. An unexpected error
        # (a bug in our code) still propagates and crashes loudly, which is
        # what you want — only the *known* failure mode is handled here.
        console.print(f"  [red]✘ gave up:[/red] {e}\n")
        rows.append((name, alert.severity, "FAILED", "-", "-"))

# --- Summary table ----------------------------------------------------------
table = Table(title="Triage results")
for col in ("alert", "monitoring said", "model said", "confidence", "agreement"):
    table.add_column(col)
for row in rows:
    # `*row` unpacks the tuple into separate arguments, so add_row receives
    # five values rather than one tuple.
    table.add_row(*row)
console.print(table)

# --- The payoff: ordinary code, on typed fields ------------------------------
# This is what the whole module exists to make possible. `result` here is the
# last alert's triage — a real object with real types, so routing is a plain
# `if`. No parsing, no string matching, no hoping.
if rows and rows[-1][2] != "FAILED":
    console.print("\n[bold]Routing the last alert[/bold]")
    if result.severity in ("high", "critical") and result.confidence >= 0.7:
        console.print("[red]  → would page the on-call engineer[/red]")
    elif result.confidence < 0.5:
        console.print("[yellow]  → low confidence, would gather more context first[/yellow]")
    else:
        console.print("[green]  → would file a ticket, no page[/green]")
    console.print(
        "[dim]  `result.confidence >= 0.7` is guaranteed to be a float between\n"
        "  0.0 and 1.0 — not because the model complied, but because anything\n"
        "  else was rejected and retried until it did.[/dim]"
    )

console.print(
    "\n[dim]Now look at the metrics table. Most alerts needed two attempts:\n"
    "qwen2.5:7b answers confidence as a percentage on its first try, that gets\n"
    "rejected, and the retry carries the error back. Every retry is a full\n"
    "call, paid for in tokens and latency — reliability has a price and this\n"
    "is the bill. A blind retry would buy the same mistake twice; including\n"
    "the error is what changes the answer.[/dim]\n"
)

session_report()
