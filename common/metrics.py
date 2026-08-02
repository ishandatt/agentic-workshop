"""Token, latency, and cost tracking for every LLM call.

Usage:
    from common.metrics import track, session_report

    with track("triage") as m:
        response = llm.invoke(messages)
        m.record(response)          # reads usage_metadata off the response

    session_report()                # cumulative table at any point

Why this exists:
- Local models are free, but the SAME pipeline pointed at a cloud API is not.
  We convert token counts into a reference cost so you build intuition for
  what each step "would cost" in production.
- Latency and tokens/sec make model and context-size tradeoffs visible. Watch
  what happens to latency once prompts start carrying thousands of tokens of
  retrieved documents.
- Aggregating per-step metrics is 80% of what commercial LLM observability
  products (LangSmith, Langfuse, Datadog LLM Obs) do. We build the tiny
  version so the big ones aren't magic.

This file uses more Python-specific machinery than the rest of the project,
so it is commented in detail.
"""

# This import must be first. It enables modern type-hint syntax (like
# `list[CallMetrics]` instead of `List[CallMetrics]`) on older Python versions.
# It affects only annotations, never runtime behaviour.
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table

from common.config import REF_PRICE_INPUT_PER_1M, REF_PRICE_OUTPUT_PER_1M

console = Console()


# `@dataclass` is a DECORATOR: a function that takes the class below it and
# returns a modified version. This one auto-generates the boilerplate —
# __init__, __repr__, __eq__ — from the annotated attributes, so
# `CallMetrics(step="triage")` just works. Think "record"/"struct" with a
# free constructor.
@dataclass
class CallMetrics:
    """Everything we measured about a single LLM call."""

    # `step: str` declares an attribute and its type. `= 0` supplies a
    # default, so only `step` is required when constructing one. Attributes
    # WITHOUT defaults must come first — that's a dataclass rule.
    step: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0

    # `@property` makes a method callable like an attribute: you write
    # `m.total_tokens`, not `m.total_tokens()`. Useful for values that are
    # derived rather than stored, so they can never go stale.
    @property
    def total_tokens(self) -> int:
        # `self` is the instance, equivalent to `this` elsewhere. Python
        # requires it to be written out as the first parameter.
        return self.input_tokens + self.output_tokens

    @property
    def tokens_per_sec(self) -> float:
        # Guard against divide-by-zero: if latency is 0 (or falsy), return 0.0.
        # Python's ternary reads value-if-true, then the condition, then else.
        return self.output_tokens / self.latency_s if self.latency_s else 0.0

    @property
    def ref_cost_usd(self) -> float:
        """What this call would have cost on a paid cloud API."""
        # Prices are quoted per 1 million tokens, so divide at the end.
        return (
            self.input_tokens * REF_PRICE_INPUT_PER_1M
            + self.output_tokens * REF_PRICE_OUTPUT_PER_1M
        ) / 1_000_000  # underscores are digit separators — 1_000_000 == 1000000


@dataclass
class _Session:
    """Holds every call recorded since the process started.

    The leading underscore is a naming convention meaning "internal — don't
    import this from outside". Python does not enforce privacy; it's a signal
    to humans and linters.
    """

    # A mutable default like `= []` would be SHARED between all instances —
    # a classic Python bug. `field(default_factory=list)` calls list() fresh
    # for each instance, which is what we want.
    calls: list[CallMetrics] = field(default_factory=list)


# One module-level instance. Because Python caches imported modules, every
# file that does `from common.metrics import track` shares this same object —
# which is exactly how the totals accumulate across a whole script.
_SESSION = _Session()


class _Tracker:
    """The object handed to you by `with track(...) as m`."""

    # `__init__` is the constructor, run automatically on _Tracker(step).
    def __init__(self, step: str):
        self.metrics = CallMetrics(step=step)
        self._t0 = 0.0  # start time, filled in by track() below

    def record(self, response) -> None:
        """Pull token usage off a LangChain response object.

        Works with any LangChain chat model, because they all normalise usage
        into `usage_metadata`. That normalisation is a large part of what the
        framework buys you.
        """
        # getattr(obj, "name", default) reads an attribute that may not exist,
        # instead of raising AttributeError. The `or {}` then covers the case
        # where the attribute exists but is None, so `.get()` below is safe
        # either way.
        usage = getattr(response, "usage_metadata", None) or {}
        self.metrics.input_tokens = usage.get("input_tokens", 0)
        self.metrics.output_tokens = usage.get("output_tokens", 0)

    def record_raw(self, input_tokens: int, output_tokens: int) -> None:
        """For non-LangChain calls (raw HTTP, embeddings) where you already
        have the numbers and there's no usage_metadata to read."""
        self.metrics.input_tokens = input_tokens
        self.metrics.output_tokens = output_tokens


# `@contextmanager` turns the generator function below into something usable
# with `with`. The mechanics: code before `yield` runs on entry, the yielded
# value becomes `m` in `with track(...) as m`, and code after `yield` runs on
# exit — guaranteed, even if the body raises.
@contextmanager
def track(step: str, quiet: bool = False):
    """Time a block of code and record its token usage.

    `quiet` is a keyword argument with a default, so both of these work:
        with track("triage") as m:
        with track("triage", quiet=True) as m:
    """
    t = _Tracker(step)
    # perf_counter is a monotonic high-resolution clock. Use it for measuring
    # durations; time.time() can jump backwards if the system clock changes.
    t._t0 = time.perf_counter()
    try:
        # Hand control (and the tracker) back to the `with` block body.
        yield t
    finally:
        # `finally` always runs — normal exit or exception — so a call that
        # blows up still gets its latency recorded rather than vanishing.
        t.metrics.latency_s = time.perf_counter() - t._t0
        _SESSION.calls.append(t.metrics)
        if not quiet:
            m = t.metrics
            # Adjacent string literals are concatenated automatically, which
            # is how this one long line is split across several.
            # `:.2f` inside an f-string means "2 decimal places".
            console.print(
                f"[dim]⏱ {m.step}: {m.latency_s:.2f}s · "
                f"{m.input_tokens} in / {m.output_tokens} out tok · "
                f"{m.tokens_per_sec:.1f} tok/s · "
                f"ref cost ${m.ref_cost_usd:.6f}[/dim]"
            )


def session_calls() -> list[CallMetrics]:
    """A copy of every call tracked so far.

    Added for the full pipeline, which needs to report the cost of ONE incident
    rather than of the whole process. Snapshot the length before a run, slice
    from there afterwards.
    """
    return list(_SESSION.calls)


def reset_session() -> None:
    """Forget everything tracked so far. Useful between runs in one process."""
    _SESSION.calls.clear()


def session_report() -> None:
    """Print a cumulative table of every tracked call this session."""
    # An empty list is "falsy" in Python, so `if not _SESSION.calls` reads as
    # "if there are no calls". Same idea for empty strings, dicts and zero.
    if not _SESSION.calls:
        console.print("[dim]No LLM calls tracked yet.[/dim]")
        return

    table = Table(title="LLM call metrics (this session)")
    # Loop over a tuple of column names, adding each one.
    for col in ("step", "latency", "in tok", "out tok", "tok/s", "ref cost"):
        table.add_column(col, justify="right" if col != "step" else "left")

    for m in _SESSION.calls:
        # Rich wants strings, so numbers are formatted explicitly here.
        table.add_row(
            m.step,
            f"{m.latency_s:.2f}s",
            str(m.input_tokens),
            str(m.output_tokens),
            f"{m.tokens_per_sec:.1f}",
            f"${m.ref_cost_usd:.6f}",
        )

    # `sum(... for m in ...)` is a generator expression: it produces values
    # lazily and feeds them straight into sum(), without building a list.
    total_in = sum(m.input_tokens for m in _SESSION.calls)
    total_out = sum(m.output_tokens for m in _SESSION.calls)
    total_cost = sum(m.ref_cost_usd for m in _SESSION.calls)
    total_lat = sum(m.latency_s for m in _SESSION.calls)
    table.add_section()  # draws the horizontal rule before the totals
    table.add_row(
        "TOTAL", f"{total_lat:.2f}s", str(total_in), str(total_out), "",
        f"${total_cost:.6f}",
    )
    console.print(table)
