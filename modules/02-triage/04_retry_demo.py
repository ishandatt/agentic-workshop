"""Step 4: the retry loop, every message shown — running the real thing.

This script calls `triage()`. Not a copy of it, not a simplified version — the
same function `05_api.py` serves requests with. `show_transcript=True` simply
turns on printing inside it.

WATCH FOR THE INVARIANT. The model, the schema, the system prompt and the
temperature are all fixed before the loop starts. Between attempt 1 and attempt
2 the ONLY thing that changes is that two messages were appended: the model's
own failed answer, and the complaint about it. That is what makes this a retry
rather than a different strategy — and it is what the earlier version of this
demo got wrong by turning the schema on partway through.

HOW THE FAILURE IS FORCED — read this, it matters:

We do not rely on the model misbehaving. `DemoTriageResult` below adds one
house rule that no model can satisfy on a first attempt, because no model can
know it. Attempt 1 therefore fails on any model, every time. The feedback
teaches the rule, and the model complies.

Run:  python modules/02-triage/04_retry_demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import field_validator
from rich.console import Console

from common.metrics import session_report

from samples import load_alert
from schemas import TriageResult
from triage import TriageError, triage

console = Console()


class DemoTriageResult(TriageResult):
    """The real contract, plus one house rule the model cannot know about.

    Inheriting from TriageResult means every existing field and constraint
    still applies — this adds a rule rather than replacing anything.

    The rule is a stand-in for the kind of formatting requirement real incident
    tooling imposes: a paging system that only renders summaries carrying a
    known prefix, a log pipeline that greps for a marker, a ticketing system
    with a title convention. Perfectly ordinary, and entirely local to your
    organisation.

    Why the model cannot guess it — two facts established in Step 2:

      1. Validators do not appear in the JSON Schema. Search the output of
         `DemoTriageResult.model_json_schema()` for "SEV" and you will not
         find it. Only `Field(...)` constraints are serialised; a
         `@field_validator` is ordinary Python that runs after parsing.
      2. Ollama never shows the schema to the model anyway. It compiles it
         into a sampling constraint, which is why attaching it costs zero
         extra input tokens.

    So this rule is genuinely unknowable until we put it in a message. That is
    exactly what the retry does, and it is why this demo works identically on
    qwen2.5:7b, llama3.1:8b, or anything else you point it at.
    """

    @field_validator("summary")
    @classmethod
    def _house_style(cls, v):
        # `str.startswith` is an exact prefix test. A model would essentially
        # never emit this prefix spontaneously, which is what makes the first
        # attempt fail deterministically rather than usually.
        if not v.startswith("SEV: "):
            raise ValueError("summary must begin with the exact prefix 'SEV: '")
        return v


alert = load_alert("payment_error_spike")

console.print(
    "[bold]Running the real triage() with printing turned on[/bold]\n"
    "[dim]Nothing below is a special demo code path. The only difference from\n"
    "what the API runs is show_transcript=True and one extra validation rule.\n"
    "\n"
    "Attempt 1 cannot succeed: DemoTriageResult requires the summary to start\n"
    'with "SEV: ", and nothing has told the model that yet.[/dim]\n'
)

try:
    # The same cap production uses. DemoTriageResult can fail on two counts at
    # once — the missing prefix AND confidence being a percentage — so more
    # headroom was tempting, but measured over five runs it recovers on attempt
    # 2 every time. A demo whose cap differs from production teaches the wrong
    # number.
    result = triage(
        alert,
        max_attempts=3,
        show_transcript=True,
        schema=DemoTriageResult,
    )

    console.print(
        f"\n[green]✔ Final result:[/green] severity={result.severity} "
        f"confidence={result.confidence}"
    )
    console.print(f"[dim]  summary: {result.summary}[/dim]")

except TriageError as e:
    # The honest other outcome. Retrying is a bet, not a guarantee — and when
    # the bet loses, failing loudly beats inventing a result.
    console.print(f"\n[red]✘ Never recovered:[/red] {e}")

console.print(
    "\n[bold]What to take from the transcript[/bold]\n"
    "[dim]1. The request panel is printed once, because there is only one\n"
    "   request. Every attempt sends the same model, schema, system prompt and\n"
    "   temperature.\n"
    "2. Each retry appends exactly two messages: the model's own failed answer,\n"
    "   then the complaint. The complaint alone would be far weaker — the model\n"
    "   needs to see what it said to know what to change.\n"
    "3. Look at the input tokens per attempt below. Each round resends the\n"
    "   whole growing conversation, so a retry costs more than double, and it\n"
    "   compounds. That is why max_attempts exists.[/dim]\n"
)

session_report()
