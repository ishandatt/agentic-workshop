"""Step 1: measure retrieval on its own, before any model gets involved.

This is the cheapest and most useful measurement in the whole system, and the
one teams skip. If the right chunk never reaches the prompt, no amount of
prompt engineering recovers it — you are tuning the wrong layer.

Retrieval evaluation needs no LLM, so it runs in seconds and costs nothing.
Run it on every change to chunking, embedding model, k, or threshold.

Run:  python modules/06-evaluation/01_retrieval_eval.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from rich.console import Console
from rich.table import Table

from harness import DISTANCE_THRESHOLD, get_store, load_cases

console = Console()

cases = load_cases()
store = get_store()

console.print(f"[bold]{len(cases)} cases[/bold] from data/evals.jsonl")
console.print(f"[dim]k=4, distance threshold {DISTANCE_THRESHOLD}[/dim]\n")

table = Table(title="Retrieval")
table.add_column("case")
table.add_column("hit", justify="center")
table.add_column("kept", justify="right")
table.add_column("best", justify="right")
table.add_column("expected section / outcome")

hits = 0
for case in cases:
    found = store.similarity_search_with_score(case.question, k=4)
    kept = [(d, s) for d, s in found if s <= DISTANCE_THRESHOLD]
    kept_sections = [d.metadata.get("section", "?") for d, _ in kept]
    best = found[0][1] if found else 1.0

    if case.expected_section:
        ok = case.expected_section in kept_sections
        expectation = case.expected_section[:38]
    else:
        # Unanswerable: the right behaviour is to keep nothing.
        ok = len(kept) == 0
        expectation = "(should keep nothing)"

    hits += ok
    table.add_row(
        case.id,
        "[green]✔[/green]" if ok else "[red]✘[/red]",
        str(len(kept)),
        f"{best:.3f}",
        expectation,
    )

console.print(table)

rate = hits / len(cases)
colour = "green" if rate >= 0.8 else "yellow" if rate >= 0.6 else "red"
console.print(f"\n[bold]Retrieval hit rate: [{colour}]{hits}/{len(cases)} "
              f"({rate:.0%})[/{colour}][/bold]\n")

# --- Threshold sweep ----------------------------------------------------------
# The threshold was picked by eye in module 5. Now we can actually check it,
# and see the trade it makes: too low drops good chunks, too high lets junk in.
console.print("[bold]How does the threshold change the score?[/bold]\n")

sweep = Table()
sweep.add_column("threshold", justify="right")
sweep.add_column("answerable found", justify="right")
sweep.add_column("unanswerable correctly empty", justify="right")
sweep.add_column("overall", justify="right")

for threshold in (0.30, 0.35, 0.40, 0.45, 0.50, 0.60, 0.90):
    good = bad = 0
    answerable = unanswerable = 0
    for case in cases:
        found = store.similarity_search_with_score(case.question, k=4)
        kept_sections = [d.metadata.get("section", "?") for d, s in found if s <= threshold]
        if case.expected_section:
            answerable += 1
            good += case.expected_section in kept_sections
        else:
            unanswerable += 1
            bad += len(kept_sections) == 0
    total = (good + bad) / len(cases)
    mark = " [dim]← current[/dim]" if threshold == DISTANCE_THRESHOLD else ""
    sweep.add_row(f"{threshold:.2f}", f"{good}/{answerable}",
                  f"{bad}/{unanswerable}", f"{total:.0%}{mark}")
console.print(sweep)

console.print(
    "\n[dim]That table is the argument for evaluation in one picture. The\n"
    "threshold is a dial with two failure modes pulling in opposite\n"
    "directions: tighten it and you starve real questions of context, loosen\n"
    "it and unanswerable questions arrive with confident-looking junk\n"
    "attached.\n\n"
    "Without this table you are guessing. With it, the guess becomes a choice\n"
    "you can defend — and re-check the next time you change the chunker.[/dim]\n"
)

console.print(
    "[bold]Note what this measured, and what it did not.[/bold]\n"
    "[dim]No LLM was called. This is pure retrieval: did the right text reach\n"
    "the prompt? An answer can still be wrong from perfect context — that is\n"
    "the next script — but if THIS number is bad, nothing downstream can save\n"
    "you.[/dim]\n"
)
