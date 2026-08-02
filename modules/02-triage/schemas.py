"""The two data contracts for this module: what comes IN, and what must come OUT.

Both are Pydantic models. Pydantic is a validation library: you declare the
shape you expect, and it enforces it at runtime — parsing, type-coercing, and
raising a precise error when reality disagrees.

That matters twice over here:

1. **Incoming alerts** arrive as JSON from a monitoring system we don't
   control. Validating at the edge means a malformed alert is rejected with a
   clear 422, instead of causing a mystery crash five functions deeper.
2. **Outgoing triage** is produced by a language model, which is a text
   generator with no obligation to obey us. Pydantic is the thing that turns
   "the model said something" into "the model said something *usable*".

The same class also becomes the JSON Schema we hand to the model, so one
declaration is both the request contract and the validation rule.
"""

from datetime import datetime

# `Literal` restricts a value to an explicit set of options — the closest
# Python gets to a string enum in a type hint. Pydantic enforces it at runtime.
from typing import Literal

# BaseModel is the class you inherit from to get validation.
# Field() attaches metadata (description, constraints) to a single attribute.
from pydantic import BaseModel, Field


class Alert(BaseModel):
    """An incoming alert from a monitoring system.

    Inheriting from BaseModel is what makes this validated. Writing
    `Alert(**json_dict)` parses and checks every field; anything wrong raises
    ValidationError rather than silently producing a broken object.
    """

    # Each line is `name: type = Field(...)`. The type is enforced, not just
    # documentation — unlike ordinary Python type hints.
    service: str = Field(description="Name of the affected service")

    # The severity the MONITORING SYSTEM reported. Note this is deliberately
    # separate from the severity our model will assess: one is what the alert
    # claimed, the other is what we concluded. Conflating them would hide
    # exactly the disagreement we care about.
    severity: Literal["info", "warning", "critical"] = Field(
        description="Severity as reported by the monitoring system"
    )

    metric: str = Field(description="Metric that triggered the alert")
    value: float = Field(description="Observed value of that metric")
    description: str = Field(description="Human-written context from the alert")

    # Pydantic parses ISO 8601 strings into real datetime objects
    # automatically. "2026-08-01T14:23:11Z" arrives as a str over HTTP and
    # becomes a datetime here, with no conversion code from us.
    timestamp: datetime = Field(description="When the alert fired")


class TriageResult(BaseModel):
    """What we require the model to produce.

    A warning about the `description=` fields below, because it is
    counter-intuitive: under Ollama they never reach the model.

    The schema is compiled into a sampling constraint by the runtime, not
    injected into the prompt — measured, the same request costs an identical
    number of input tokens with and without it. So these descriptions document
    the contract for humans, and they DO travel to the model on providers that
    implement structured output via function calling (OpenAI and friends). Here
    they steer nothing. To steer the model, put the words in the prompt.
    """

    # Our own assessment, on a deliberately different scale from the incoming
    # alert's severity, so "monitoring said critical, triage said low" is
    # expressible rather than lost.
    severity: Literal["low", "medium", "high", "critical"] = Field(
        description="Your assessment of actual severity, which may differ from "
        "the severity the monitoring system reported"
    )

    summary: str = Field(
        description="One sentence describing what is happening, in plain "
        "language an on-call engineer can read at 2am"
    )

    hypothesis: str = Field(
        description="Your single most likely explanation for the cause, and "
        "why you believe it"
    )

    # `ge` and `le` are "greater/less than or equal" constraints, and they end
    # up serving TWO different enforcement points with different coverage:
    #
    #   generation  ge/le become "minimum"/"maximum" in the JSON Schema, which
    #               Ollama compiles into a sampling grammar. Structure, keys,
    #               types and `enum` survive that translation; numeric bounds
    #               are dropped. So nothing here stops a model emitting 80.
    #   validation  Pydantic applies them after parsing. This is what actually
    #               rejects an out-of-range value.
    #
    # Which makes this line the opposite of decoration: it cannot PREVENT the
    # mistake, but it is the only thing that CATCHES it. Delete `le=1.0` and a
    # confidence of 80 flows into the pipeline and gets routed on.
    #
    # Prevention lives in the prompt, where the model can actually read it —
    # SYSTEM_PROMPT in triage.py spells out that confidence is a decimal, which
    # moved first-attempt results from 0/6 in range to 6/6.
    #
    # What we deliberately do NOT do is "fix" the value here with a validator
    # that divides anything above 1 by 100. That guesses at intent: a model
    # answering on a 0-10 scale writes 8 meaning 0.8, and the same rule hands
    # the pipeline 0.08 — wrong by a factor of ten, with nothing in the logs to
    # show for it. Silent repair destroys the evidence that anything happened.
    # Reject, and let the retry loop in triage.py ask again.
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in the hypothesis as a decimal fraction "
        "between 0.0 and 1.0, for example 0.85",
    )
