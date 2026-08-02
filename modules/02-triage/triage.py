"""The defensive triage function — the actual deliverable of this module.

`01_the_parsing_problem.py` shows why prompting for JSON is not enough.
`02_structured_output.py` shows the schema-driven fix.
This file is that fix hardened for real use: retries, feedback on failure, and
a clear error when the model genuinely cannot comply.

Both `03_defensive_triage.py` and `05_api.py` import from here, so the logic
exists in exactly one place.
"""

import sys
from pathlib import Path

# Project root onto the import path so `common` is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from rich.console import Console

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.display import show_messages, show_response
from common.metrics import track

# Import from the file sitting next to this one. A script's own directory is
# always on the import path, so a bare `from schemas import ...` finds it.
from schemas import Alert, TriageResult

# Only used when show_transcript=True.
_console = Console()


# The system prompt sets the role and the rules. Keep it short and specific:
# every token here is sent on EVERY call, so waffle costs money forever.
SYSTEM_PROMPT = """You are an experienced site reliability engineer triaging \
a production alert.

Assess the real severity, which may be higher or lower than the monitoring \
system reported — monitoring thresholds are often mis-tuned.

Be concrete and brief. If the evidence is thin, say so through a low \
confidence score rather than inventing detail.

Confidence is a decimal fraction between 0 and 1, for example 0.85. Never \
express it as a percentage such as 85."""

# That last sentence is doing real work, and it belongs in the PROMPT rather
# than anywhere else. The schema already says "maximum": 1.0 and the model
# never sees it — Ollama compiles the schema into a sampling constraint instead
# of showing it to the model, and numeric bounds are dropped in the process.
#
# Measured on qwen2.5:7b, first attempt, six runs across all three sample
# alerts: without this sentence, 0/6 answers were in range (70, 70, 70, 95, 70,
# 70). With it, 6/6 (0.75, 0.75, 0.9, 0.9, 0.85, 0.85).
#
# Note what we did NOT do: add a validator that divides anything above 1 by
# 100. That repairs the symptom by guessing at intent — a model answering on a
# 0-10 scale writes 8 meaning 0.8, and the same rule silently yields 0.08. Fix
# the instruction, validate the result, and retry if it still disagrees.


class TriageError(RuntimeError):
    """Raised when the model could not produce a valid result in time.

    A custom exception type (inheriting from a built-in one) lets callers
    catch *this specific failure* rather than every possible error. `05_api.py`
    uses it to return a clean 503 instead of a stack trace.
    """


def explain_error(error) -> str:
    """Pull the useful part out of a parsing failure.

    LangChain's exception text is long: it quotes the model's ENTIRE completion
    before it gets to the actual complaint, which looks like

        Failed to parse TriageResult from completion {"severity": "high", ...}.
        Got: 1 validation error for TriageResult
        confidence
          Input should be less than or equal to 1

    Only the part after "Got:" is worth anything. Trimming matters twice over:
    a human reading the log can see the reason, and the retry we send back gets
    dramatically cheaper — we already append the model's raw answer separately,
    so quoting it again inside the error is paying twice for the same tokens.
    """
    text = str(error)
    # `partition` splits on the first occurrence and returns three parts:
    # (before, separator, after). If the separator is absent, after is "".
    _, found, tail = text.partition("Got:")
    # `.strip()` removes surrounding whitespace. Fall back to the full text if
    # the marker isn't there, so we never return nothing.
    return tail.strip() if found else text.strip()


def build_llm(temperature: float = 0.1) -> ChatOllama:
    """Create the model client.

    Low temperature on purpose: triage should be repeatable. The same alert
    twice should not produce two different severities.
    """
    return ChatOllama(
        model=CHAT_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=temperature,
    )


def alert_to_prompt(alert: Alert) -> str:
    """Render an Alert into the text the model actually reads.

    Kept as its own function so you can print it, test it, and change the
    formatting without touching the call logic.

    Note the triple-quoted f-string: it spans lines and interpolates `{}`
    expressions. `:%Y-%m-%d %H:%M UTC` is a datetime format spec applied inline.
    """
    return f"""Alert details:
- Service: {alert.service}
- Reported severity: {alert.severity}
- Metric: {alert.metric}
- Observed value: {alert.value}
- Fired at: {alert.timestamp:%Y-%m-%d %H:%M UTC}
- Context: {alert.description}

Triage this alert."""


def triage(
    alert: Alert,
    max_attempts: int = 3,
    verbose: bool = False,
    show_transcript: bool = False,
    schema: type = TriageResult,
) -> TriageResult:
    """Turn an Alert into a validated TriageResult, defending against failure.

    Three layers of defence, which is the point of the whole module:

    1. **Schema-constrained generation** — the model is given the JSON Schema
       for TriageResult and asked to fill it in, rather than being asked
       nicely for JSON in prose.
    2. **Validation** — Pydantic checks the result. A confidence of 95 or a
       severity of "very bad" is rejected here, not passed downstream.
    3. **Retry with feedback** — on failure we tell the model exactly what was
       wrong and ask again. Blind retries mostly reproduce the same mistake;
       retries carrying the error message usually don't.

    Note that layer 2 is doing real work here, not ceremony: qwen2.5:7b
    answers `confidence` as a percentage on nearly every first attempt, so most
    calls genuinely go round the loop twice.

    THE INVARIANT WORTH NOTICING: the request never changes. The model, the
    schema, the system prompt and the temperature are fixed before the loop
    starts. The ONLY thing that differs between attempt 1 and attempt 3 is that
    `messages` has grown. That is what makes this a retry rather than a
    different strategy, and `show_transcript=True` prints it so you can see it.

    Parameters:
      verbose         one terse line per attempt
      show_transcript every message in both directions (used by the demo)
      schema          the output contract; swappable so a caller can add rules
                      without duplicating this loop
    """
    llm = build_llm()

    # `include_raw=True` changes the return type to a dict with three keys:
    #   "raw"           the underlying AIMessage (this is where token usage lives)
    #   "parsed"        a TriageResult, or None if validation failed
    #   "parsing_error" the exception, or None on success
    #
    # Without it, a bad response raises and we lose both the token counts and
    # the text that failed — the two things we most need to react and to debug.
    #
    # Built ONCE, before the loop. Every attempt re-uses this exact object, so
    # no attempt can differ from any other in anything but its messages.
    structured_llm = llm.with_structured_output(schema, include_raw=True)

    # Start with the base conversation. Retries append to this list, so the
    # model can see its own failed attempt and the complaint about it.
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(alert_to_prompt(alert)),
    ]

    last_error = None

    if show_transcript:
        show_messages(messages, title="The request — identical on every attempt")

    # `range(1, max_attempts + 1)` counts 1, 2, 3 — starting at 1 because these
    # numbers appear in human-facing messages.
    for attempt in range(1, max_attempts + 1):
        if show_transcript:
            _console.rule(f"[bold]Attempt {attempt}[/bold]")
            if attempt > 1:
                _console.print(
                    "[dim]Same model, same schema, same system prompt. The only "
                    "difference from attempt 1 is the two messages above.[/dim]\n"
                )
        try:
            # Every attempt is tracked separately, so a retry shows up as extra
            # cost in the metrics table. Failed attempts are not free.
            with track(f"triage-attempt-{attempt}", quiet=not verbose) as m:
                result = structured_llm.invoke(messages)
                # Token usage lives on the raw message, not the parsed object.
                m.record(result["raw"])

        except Exception as e:
            # Transport-level problems: Ollama down, timeout, model unloaded.
            # Worth retrying — these are usually transient.
            last_error = f"call failed: {e}"
            if verbose:
                print(f"  attempt {attempt}: {last_error}")
            continue

        if show_transcript:
            show_response(result["raw"].content)

        parsed = result["parsed"]
        if parsed is not None:
            if verbose and attempt > 1:
                print(f"  attempt {attempt}: valid — recovered after being told the error")
            if show_transcript:
                _console.print(f"\n[green]✔ Valid on attempt {attempt}.[/green]")
            # Success. Pydantic has already enforced the field types, the
            # severity enum, and the 0.0-1.0 confidence range.
            return parsed

        # Reaching here means the model replied, but the reply did not satisfy
        # the schema. Feed the failure back in and try again.
        last_error = explain_error(result["parsing_error"])
        if verbose:
            # Indent the multi-line complaint so it reads as one block.
            shown = last_error.replace("\n", "\n     ")
            print(f"  attempt {attempt} rejected:\n     {shown}")

        # Append the model's own bad answer, then the complaint about it.
        # The model can now see BOTH what it said and why that was rejected —
        # which is the entire mechanism.
        feedback = (
            f"That response was not valid: {last_error}\n"
            "Reply again, matching the required schema exactly."
        )
        messages.append(result["raw"])
        messages.append(HumanMessage(feedback))

        if verbose:
            print("  → sending that error back to the model, asking again\n")

        if show_transcript:
            _console.print(f"\n[red]✘ rejected:[/red]\n[dim]{last_error}[/dim]\n")
            show_messages(messages[-2:], title="Appended to the conversation")

    # Every attempt exhausted. Fail loudly and specifically — never return a
    # half-built or made-up result, because callers cannot tell the difference.
    raise TriageError(
        f"No valid triage after {max_attempts} attempts. Last error: {last_error}"
    )
