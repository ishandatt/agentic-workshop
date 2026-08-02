"""Step 0: why "just ask the model for JSON" is not an answer.

An alert arrives as structured data. To act on it, we need structured data
back — a severity we can branch on, a confidence we can threshold. What the
model produces is *text*.

Three parts, and part 3 is the one that matters:

  Part 1: ask for prose            -> obviously unusable
  Part 2: ask for JSON             -> fails, and you can see why
  Part 3: prompt-engineer the fix  -> works! ...on this one input.

Part 3 is the trap this module exists to get you out of. Green results from a
tuned prompt feel like success and are not the same thing as a guarantee.

Run:  python modules/02-triage/01_the_parsing_problem.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from rich.console import Console
from rich.panel import Panel   # draws a border, so we can see EXACTLY where
                               # the model's output starts and stops

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.display import show_messages
from common.metrics import session_report, track

from samples import load_alert

console = Console()

alert = load_alert("payment_error_spike")
llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)

ALERT_TEXT = (
    f"Service: {alert.service}\n"
    f"Reported severity: {alert.severity}\n"
    f"Metric: {alert.metric} = {alert.value}\n"
    f"Context: {alert.description}"
)


def validate(raw: str) -> tuple[bool, str]:
    """Check a response by hand, the way you'd have to without a schema.

    Deliberately NOT using our Pydantic model here. TriageResult repairs some
    problems on the way in, and this function's job is to report exactly what
    the model produced — warts included.

    The volume of code below is itself the argument: this is the validation
    you write when the model's output is merely hoped for. The return type
    `tuple[bool, str]` means "a pair of (passed, reason)".
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return False, f"not JSON at all ({e.msg})"

    if not isinstance(data, dict):
        return False, f"JSON, but a {type(data).__name__}, not an object"

    # Are the fields we need even present?
    missing = [k for k in ("severity", "summary", "hypothesis", "confidence")
               if k not in data]
    if missing:
        # ", ".join(...) glues a list of strings together with commas.
        return False, f"missing field(s): {', '.join(missing)}"

    if data["severity"] not in ("low", "medium", "high", "critical"):
        return False, f"severity={data['severity']!r} is not one of our four values"

    conf = data["confidence"]
    if not isinstance(conf, (int, float)) or isinstance(conf, bool):
        return False, f"confidence={conf!r} is not a number"
    if not 0.0 <= conf <= 1.0:
        # Chained comparison: Python allows `0.0 <= x <= 1.0` directly.
        return False, f"confidence={conf} is outside 0.0-1.0"

    return True, "ok"


def run_attempts(prompt: str, label: str, n: int = 5) -> int:
    """Send the same prompt n times and report how many results were usable.

    One success proves nothing. What matters is the failure RATE, because in
    production you get thousands of attempts, not one.
    """
    # Show the exact prompt before the attempts. The two prompts in this
    # script differ, and that difference is the entire lesson.
    show_messages([{"role": "human", "content": prompt}], title=f"Prompt for {label}")

    passed = 0
    for i in range(1, n + 1):
        with track(f"{label}-{i}", quiet=True) as m:
            response = llm.invoke([HumanMessage(prompt)])
            m.record(response)

        raw = response.content.strip()
        ok, reason = validate(raw)

        # Show the FIRST response in full. Five walls of JSON teach nothing,
        # but one does — and you cannot reason about a failure you never saw.
        # The panel border matters: it marks exactly where the model's output
        # begins, so a leading ```json fence is unmistakable.
        if i == 1:
            console.print(
                Panel(
                    raw,
                    title=f"[dim]raw response, attempt 1 of {n}[/dim]",
                    border_style="green" if ok else "red",
                    expand=False,
                )
            )

        if ok:
            passed += 1
            console.print(f"  [green]✔ attempt {i}[/green] usable")
        else:
            console.print(f"  [red]✘ attempt {i}[/red] {reason}")
            # repr() reveals escape characters and quotes — which is how you
            # SEE the fences and newlines that a plain print renders invisibly.
            console.print(f"    [dim]{repr(raw[:110])}[/dim]")
    return passed


# ---------------------------------------------------------------------------
# Part 1 — Ask for prose
# ---------------------------------------------------------------------------
console.print("[bold]1) Asking for a normal answer[/bold]\n")

with track("prose-triage") as m:
    response = llm.invoke(
        [
            SystemMessage("You are an experienced site reliability engineer."),
            HumanMessage(f"Triage this alert:\n\n{ALERT_TEXT}"),
        ]
    )
    m.record(response)

console.print(f"[cyan]{response.content}[/cyan]\n")

console.print(
    "[yellow]A good answer — and useless to a program.[/yellow]\n"
    "[dim]To route this alert we must ask: is severity >= high? Is confidence\n"
    "above threshold? You cannot write that `if` against a paragraph, and you\n"
    "cannot regex it either — the wording changes every run.[/dim]\n"
)

# ---------------------------------------------------------------------------
# Part 2 — Ask for JSON, the way everybody asks the first time
# ---------------------------------------------------------------------------
console.print("[bold]2) Asking for JSON[/bold]\n")

NAIVE_PROMPT = (
    "Triage this alert. Reply with a JSON object containing: severity "
    "(low/medium/high/critical), summary, hypothesis, and confidence (0 to 1)."
    f"\n\nAlert:\n{ALERT_TEXT}"
)

naive_passed = run_attempts(NAIVE_PROMPT, "naive-json")
console.print(f"\n[bold]{naive_passed}/5 usable.[/bold]")
console.print(
    "[dim]The model is being helpful: it wrapped the JSON in a markdown code\n"
    "fence, because that is how JSON appears in the text it learned from.\n"
    "It did what we asked. We just didn't ask precisely enough.[/dim]\n"
)

# ---------------------------------------------------------------------------
# Part 3 — Fix it with prompt engineering, and read the result carefully
# ---------------------------------------------------------------------------
console.print("[bold]3) Now we engineer the prompt[/bold]\n")

# Everything you'd naturally reach for: forbid the fences explicitly, and show
# an exact example. The doubled {{ }} are escaped braces — this string goes
# through .format(), where single braces mean placeholders.
TUNED_PROMPT = """Triage this alert and reply with ONLY a JSON object.
No markdown, no code fences, no explanation before or after.

Use exactly this shape:
{{"severity": "low|medium|high|critical", "summary": "...", \
"hypothesis": "...", "confidence": 0.0}}

Alert:
{alert_text}""".format(alert_text=ALERT_TEXT)

tuned_passed = run_attempts(TUNED_PROMPT, "tuned-json")
console.print(f"\n[bold]{tuned_passed}/5 usable.[/bold]\n")

console.print(
    "[yellow]And that is the trap.[/yellow]\n"
    "[dim]Prompt engineering probably just took you to green — on ONE alert,\n"
    "at ONE temperature, with ONE model version. Nothing about that generalises\n"
    "and nothing about it is enforced. You cannot tell from a passing run\n"
    "whether the next alert, a longer description, or a model upgrade breaks it.\n\n"
    "Notice which change did the work: the explicit EXAMPLE. Telling the model\n"
    "the exact shape beats telling it the rules — worth remembering.\n\n"
    "But we still only asked. The prompt is a suggestion the model is free to\n"
    "decline, and a suggestion is not a contract. Next: stop asking.[/dim]\n"
)

session_report()
