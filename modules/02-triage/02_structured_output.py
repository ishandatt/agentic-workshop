"""Step 2: stop asking for JSON. Constrain the model so it can only emit JSON.

The previous script asked politely and got fenced markdown back. Here we hand
the model a JSON Schema and let the runtime enforce it during generation.

The result is a clean split you should expect to see on screen:

  structurally valid   5/5   <- the shape is now guaranteed
  fully valid          0/5   <- the VALUES still are not

That gap is the point of this script. Constrained decoding fixes shape. It has
nothing to say about whether a number is in range.

Run:  python modules/02-triage/02_structured_output.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from rich.console import Console

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.display import show_messages, show_response
from common.metrics import session_report, track

from samples import load_alert
from schemas import TriageResult

console = Console()

alert = load_alert("payment_error_spike")
llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)

# --- What actually gets sent --------------------------------------------------
# A Pydantic model can describe itself as JSON Schema. This object goes to the
# RUNTIME, not to the model: Ollama compiles it into a sampling constraint, so
# it costs zero prompt tokens and the model never reads it. That includes the
# `description=` text — useful documentation, powerless to steer Ollama. Words
# meant for the model belong in the prompt.
console.print("[bold]The schema we send to the runtime:[/bold]")
console.print_json(json.dumps(TriageResult.model_json_schema()))

# --- The one line that changes everything ------------------------------------
# `.with_structured_output(SomeModel)` wraps the model so that:
#   1. the schema above is attached to the request
#   2. Ollama constrains token generation to match it — invalid tokens are not
#      merely discouraged, they are never sampled
#   3. the response is parsed and validated into a TriageResult for you
#
# `include_raw=True` returns {"raw", "parsed", "parsing_error"} rather than a
# bare result. We need "raw" for token usage (it is not on the parsed object)
# and for showing you exactly what the model wrote.
structured_llm = llm.with_structured_output(TriageResult, include_raw=True)

SYSTEM = "You are an experienced site reliability engineer triaging a production alert."
messages = [
    SystemMessage(SYSTEM),
    HumanMessage(
        f"Service: {alert.service}\n"
        f"Reported severity: {alert.severity}\n"
        f"Metric: {alert.metric} = {alert.value}\n"
        f"Context: {alert.description}"
    ),
]

console.print()
show_messages(messages)


def is_structurally_valid(text: str) -> bool:
    """Did we get a JSON object with the right keys and the right enum value?

    This deliberately checks everything EXCEPT the numeric range, so we can
    separate "the shape is right" from "the values are right". Those two
    properties have different enforcers, which is the lesson of this script.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    # `all(...)` is True only if every item in the sequence is truthy.
    if not all(k in data for k in ("severity", "summary", "hypothesis", "confidence")):
        return False
    if data["severity"] not in ("low", "medium", "high", "critical"):
        return False
    return isinstance(data["confidence"], (int, float))


ATTEMPTS = 5
structurally_valid = 0
fully_valid = 0

console.print()
for i in range(1, ATTEMPTS + 1):
    with track(f"structured-{i}", quiet=True) as m:
        out = structured_llm.invoke(messages)
        m.record(out["raw"])

    raw = out["raw"].content

    # Show the first response verbatim — once, not five times. This is the
    # model's actual output, before anything of ours touches it.
    if i == 1:
        show_response(raw)
        console.print()

    if is_structurally_valid(raw):
        structurally_valid += 1

    if out["parsed"] is not None:
        fully_valid += 1
        console.print(f"  [green]✔ attempt {i}[/green] fully valid")
    else:
        # Dig out the offending value to display. `.get()` returns None rather
        # than raising when the key is missing.
        try:
            bad = json.loads(raw).get("confidence")
        except json.JSONDecodeError:
            bad = "?"
        console.print(
            f"  [yellow]~ attempt {i}[/yellow] well-formed JSON, "
            f"but confidence={bad} is outside 0.0-1.0"
        )

# --- The two numbers ---------------------------------------------------------
console.print(
    f"\n[bold]structurally valid  {structurally_valid}/{ATTEMPTS}[/bold]   "
    f"[dim](object, keys, types, enum)[/dim]"
)
console.print(
    f"[bold]fully valid         {fully_valid}/{ATTEMPTS}[/bold]   "
    f"[dim](the above, plus every value in range)[/dim]\n"
)

console.print(
    "[green]The structural problem is solved, permanently.[/green]\n"
    "[dim]No fences. No preamble. Never a missing key, never an invented\n"
    "severity — the grammar cannot emit one. Compare that with the previous\n"
    "script, where this same model could not manage bare JSON at all.[/dim]\n"
)

console.print(
    "[yellow]The semantic problem is untouched.[/yellow]\n"
    "[dim]We asked for a fraction between 0.0 and 1.0, and the schema above\n"
    'says "maximum": 1.0. The model answers with a percentage anyway.\n\n'
    "Why: the schema is enforced at TWO different points, and that keyword\n"
    "only reaches one of them. When llama.cpp compiles the schema into a\n"
    "sampling grammar, structure and `enum` survive the translation and\n"
    "numeric bounds are dropped — so nothing stops the model emitting 80.\n"
    "The bound still applies at VALIDATION, which is exactly why every\n"
    "attempt above was rejected. Delete `le=1.0` from schemas.py and the 80\n"
    "would sail through into the pipeline instead.\n\n"
    "So the bound is not useless — it cannot PREVENT the mistake, but it is\n"
    "the only thing that CATCHES it.\n\n"
    "Note what we do NOT do about this: quietly divide by 100. That would be\n"
    "guessing at intent — a model answering on a 0-10 scale writes 8 meaning\n"
    "0.8, and that rule would turn it into 0.08 with nothing in the logs to\n"
    "show for it. The invalid answer is rejected instead. The next script\n"
    "shows what to do about it.[/dim]\n"
)

session_report()
