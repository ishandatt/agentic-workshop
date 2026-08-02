"""Step 2: grade the answers — twice, by two methods that disagree.

Retrieval scored 94%. That says the right text reached the prompt; it says
nothing about whether the answer was any good.

Two graders run here:

  fact recall   does the answer contain the exact strings only our runbook
                could supply? Deterministic, free, and blind to meaning.
  LLM judge     a second model call asking "is this answer correct?".
                Understands paraphrase, and is itself fallible.

Where they disagree is the interesting part, and this script prints those rows
first.

Run:  python modules/06-evaluation/02_answer_eval.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from rich.console import Console
from rich.table import Table

from common.metrics import session_report

from harness import build_llm, get_store, load_cases, run_case

console = Console()

cases = load_cases()
llm = build_llm()
store = get_store()

console.print(f"[bold]Grading {len(cases)} cases[/bold]")
console.print("[dim]two model calls each — one to answer, one to judge — so this "
              "takes a couple of minutes[/dim]\n")

results = []
for case in cases:
    result = run_case(llm, store, case, use_judge=True)
    results.append(result)

    recall_mark = "[green]✔[/green]" if result.fact_recall == 1.0 else \
                  "[yellow]~[/yellow]" if result.fact_recall > 0 else "[red]✘[/red]"
    judge_mark = "[green]✔[/green]" if result.judge_correct else "[red]✘[/red]"
    console.print(f"  {result.case.id:<20} facts {recall_mark}  judge {judge_mark}  "
                  f"[dim]{result.answer[:58].replace(chr(10), ' ')}…[/dim]")

# --- Scores -------------------------------------------------------------------
retrieval_hits = sum(r.retrieval_hit for r in results)
fact_perfect = sum(r.fact_recall == 1.0 for r in results)
judge_correct = sum(bool(r.judge_correct) for r in results)
total = len(results)

console.print()
summary = Table(title="Scores")
summary.add_column("measure")
summary.add_column("score", justify="right")
summary.add_column("what it tells you")
summary.add_row("retrieval hit rate", f"{retrieval_hits}/{total}",
                "did the right text reach the prompt")
summary.add_row("fact recall (exact)", f"{fact_perfect}/{total}",
                "did the answer contain the required strings")
summary.add_row("judge verdict", f"{judge_correct}/{total}",
                "does a model think the answer is correct")
console.print(summary)

# --- Where the graders disagree ----------------------------------------------
# This is the part worth reading. Agreement tells you little; disagreement
# tells you which grader is lying to you.
disagreements = [r for r in results if (r.fact_recall == 1.0) != bool(r.judge_correct)]

console.print(f"\n[bold]Graders disagreed on {len(disagreements)} case(s)[/bold]")
if disagreements:
    console.print("[dim]Each of these is either a strict-matching artefact or a "
                  "judge mistake. Read them.[/dim]\n")
for r in disagreements:
    console.print(f"[bold]{r.case.id}[/bold] — {r.case.question}")
    console.print(f"  facts found: {r.facts_found}  missing: [red]{r.facts_missing}[/red]")
    console.print(f"  judge: {'correct' if r.judge_correct else 'incorrect'} — "
                  f"[dim]{r.judge_reason}[/dim]")
    console.print(f"  answer: [dim]{r.answer[:170].replace(chr(10), ' ')}…[/dim]\n")

# --- The unanswerable cases ---------------------------------------------------
console.print("[bold]Unanswerable questions — did the system decline?[/bold]\n")
for r in results:
    if not r.case.answerable:
        mark = "[green]✔ declined[/green]" if r.declined else "[red]✘ answered anyway[/red]"
        console.print(f"  {r.case.id:<20} {mark}  [dim]{r.answer[:64]}…[/dim]")

console.print(
    "\n[dim]Three numbers, and they measure different things. A high retrieval\n"
    "score with a low judge score means the context was there and the model\n"
    "fumbled it — a prompting problem. The reverse means you are tuning\n"
    "prompts to compensate for a retrieval bug, which never ends well.\n\n"
    "Fact recall is the one to trust when it FAILS. A missing '40' or a\n"
    "missing 'Tom Oyelaran' is unambiguous. When it passes, it only proves a\n"
    "string appeared somewhere — including inside a sentence that says the\n"
    "opposite.[/dim]\n"
)

console.print(
    "[bold]And the uncomfortable part:[/bold] [dim]the judge is qwen2.5:7b — the\n"
    "same model, with the same weaknesses, grading its own homework. It will\n"
    "be lenient in exactly the places the answerer is weak. Treat the judge as\n"
    "a smoke alarm, not an auditor: useful for catching regressions between\n"
    "runs, not for certifying that a system is correct.[/dim]\n"
)

session_report()
