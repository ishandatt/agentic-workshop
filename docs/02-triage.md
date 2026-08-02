# Module 2 — Alert triage: getting structured data out of a text generator

> **The question this module answers:** a model emits text. Our code needs
> fields. How do we cross that gap reliably enough to build on?

**Time:** ~45 min · **Code:** `modules/02-triage/` · **You need:** module 1 finished and `check_setup.py` green

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | Watch prompting fail | 12 min | Ask nicely for JSON, count how often you get it |
| 2 | Constrain the model | 12 min | Schema-driven output, and a real `if` statement |
| 3 | Harden it | 10 min | Retries that carry the error back |
| 4 | Watch a retry fire | 6 min | The loop unrolled, every message shown |
| 5 | Put it behind HTTP | 10 min | `curl` a real alert into a running service |

---

## Where we are

In module 1 you called `llm.invoke()` and got a paragraph back. Fine for a
demo, useless for a pipeline: our agent has to *decide* things — page or don't
page, escalate or file a ticket — and decisions are made on fields, not prose.

This module builds the first real stage of the incident responder: an alert
arrives over HTTP, and structured triage comes out. Everything later in the
pipeline consumes what we define here.

## The problem

A language model is a text generator. It has no obligation to produce JSON, no
concept of your schema, and no memory that you asked twice. Yet the very first
thing any real system needs is to branch on the answer:

```python
if triage.severity in ("high", "critical") and triage.confidence >= 0.7:
    page_oncall()
```

You cannot write that against a paragraph. Regexes and string matching fall
apart because the wording changes every run. So: how do you get a guarantee out
of something with no obligations?

## What you'll build

- Two Pydantic contracts — one for alerts coming in, one for triage going out
- A demonstration of prompt-based JSON extraction failing
- Schema-constrained generation that mostly can't fail
- A defensive `triage()` with retries that feed the error back to the model
- A FastAPI service you can `curl` a real alert into

---

## Concepts in this module

### Pydantic

A validation library. You declare the shape you expect as a class, and it
enforces it at runtime — parsing, coercing types, and raising a precise error
when reality disagrees.

```python
class Alert(BaseModel):
    service: str
    severity: Literal["info", "warning", "critical"]
    value: float
    timestamp: datetime
```

That's not documentation. `Alert(**payload)` will reject `severity="apocalyptic"`,
refuse a `value` that isn't a number, and turn the ISO-8601 string
`"2026-08-01T14:23:11Z"` into a real `datetime` object — with no conversion
code from you.

Ordinary Python type hints are ignored at runtime. Pydantic's are not. That
distinction is the whole reason the library exists.

### JSON Schema

A standard, machine-readable description of a JSON shape: which fields exist,
their types, which are required, what values are allowed. Pydantic generates it
from your class for free:

```python
TriageResult.model_json_schema()
```

This is the bridge. Your class becomes a schema, the schema is handed to the
*runtime*, and generation is held to it. One declaration, used three ways:
validation, API documentation, and generation constraint. (Note "handed to the
runtime", not "to the model" — that distinction turns out to matter a lot, and
we come back to it.)

> **Instructor:** worth flagging early that these descriptions are for humans
> and for other providers — on Ollama they are never shown to the model. We
> prove it below, and it explains a result that otherwise makes no sense.

### Structured output (constrained decoding)

The key mechanism of this module, and it's worth being precise about why it
works.

A model generates one token at a time by sampling from a probability
distribution over its whole vocabulary. **Constrained decoding masks that
distribution.** Given a JSON Schema, the runtime computes which tokens could
possibly continue a valid document — and sets the probability of everything
else to zero.

So after `{"severity": "` the only sampleable tokens are those starting `low`,
`medium`, `high`, or `critical`. The model *cannot* emit `"apocalyptic"`, not
because it was told not to, but because those tokens were never candidates.

That's the difference from prompting. A prompt is a request the model may
decline. A constraint is enforced by the machinery underneath it.

```python
structured_llm = llm.with_structured_output(TriageResult)
result = structured_llm.invoke(messages)   # already a TriageResult
```

**Now the part that surprises almost everyone**, and that we got wrong while
building this module.

**The model never reads your schema.** Measured on Ollama: identical messages,
sent with and without the `format` schema attached, produce **80 input tokens
both times**. The schema is compiled into a sampling constraint on the
runtime's side. It costs nothing in prompt tokens, and it is never shown to the
model as text.

Two consequences follow, and both matter:

**1. `description=` does not steer the model.** Those carefully worded
descriptions in `schemas.py` never arrive. This explains a result that
otherwise looks bizarre — rewriting the confidence description to say *"this is
NOT a percentage: 85 is wrong, 0.85 is correct"* changed the output not at all
(0/6 in range, both before and after), while adding **one sentence to the
system prompt** fixed it immediately (5/5). One is inside the schema, invisible.
The other is in the conversation, where the model can actually read it.

> Descriptions still earn their place — they document the contract for humans
> and they *are* sent to the model by providers that implement structured
> output via function calling, such as OpenAI. Just don't rely on them here.

**2. The grammar enforces shape, not magnitude.** Structure, required keys,
types and `enum` are all expressible as a token-level grammar, and all hold on
every observation. A numeric bound is a fact about a *value*, and llama.cpp's
schema converter does not express it. So `"maximum": 1.0` is silently dropped:

| | `confidence` returned |
|---|---|
| schema **with** `minimum`/`maximum` | 85, 85, 85 |
| schema **without** them | 80, 85, 80 |

No difference — the bound has no effect whatsoever. Meanwhile `severity` was a
valid enum member on every single call across dozens of runs.

**The rule to take away is not a list of keywords.** Support varies by runtime
and by version, and other keywords (`pattern`, `maxLength`) did appear to hold
in our spot checks. The rule is: **assume nothing is enforced until you measure
it on your model and your runtime**, then handle the rest yourself.

How we handle it matters, and it is a deliberate choice. We could add a
validator that divides anything above 1 by 100 — and we specifically do not.
That guesses at intent: a model answering on a 0-10 scale writes `8` meaning
`0.8`, and the same rule hands the pipeline `0.08`, wrong by a factor of ten,
with nothing in the logs to show for it. **Silent repair destroys the evidence
that anything was wrong.**

So the invalid answer is rejected, and the retry loop in Step 3 asks the model
to fix it. Slower and more expensive — and it leaves a trail.

> **Instructor:** the sentence worth landing — *"know which half of your
> contract the runtime enforces and which half you enforce. Otherwise a schema
> is just a wish with extra syntax."*

### Retry with feedback

Enforcement still isn't a guarantee. The connection can drop, the model can
stall, and semantic nonsense can satisfy a schema perfectly.

The defence is a retry that carries the *error message* back into the
conversation:

```
attempt 1  ->  invalid: confidence must be <= 1
attempt 2  ->  "That response was not valid: ... Reply again, matching the schema."
```

A blind retry usually reproduces the same mistake at the same price. A retry
carrying the complaint usually doesn't.

### FastAPI and uvicorn

**FastAPI** is a web framework built around type hints. Its relevance here is
that it uses the *same Pydantic models* you already wrote, so the HTTP contract
and the validation rules cannot drift apart. Write `def receive_alert(alert: Alert)`
and it parses the body, validates it, and returns a field-level HTTP 422 on bad
input — with no parsing code from you.

**uvicorn** is the server that actually speaks HTTP. FastAPI only *describes*
the application. Same split as Flask and gunicorn.

### 422 vs 503

Worth being deliberate about, because it's a modelling decision, not trivia:

- **422 Unprocessable Entity** — *your request was wrong*. The alert was
  malformed. Retrying identical input will fail identically.
- **503 Service Unavailable** — *your request was fine, we couldn't serve it*.
  The model failed to produce valid output after every retry. Retrying later is
  reasonable.

Collapsing both into a 500 tells the caller nothing about whether to retry.

---

## Build it

### Step 1 — Watch prompting fail

**Why:** everyone's first instinct is to ask the model for JSON. Before we
replace that instinct, let's earn the right to — and then watch prompt
engineering *appear* to fix it, which is the more dangerous outcome.

```bash
python modules/02-triage/01_the_parsing_problem.py
```

**What you should see:** three parts — a good paragraph of prose, then **0/5**
usable JSON, then **5/5** after tuning the prompt.

**Part 1** returns excellent triage as prose. Read it out; it's genuinely good.
Then ask the room how they'd write `if severity >= high` against it.

**Part 2** asks the way everybody asks the first time:

```python
NAIVE_PROMPT = (
    "Triage this alert. Reply with a JSON object containing: severity "
    "(low/medium/high/critical), summary, hypothesis, and confidence (0 to 1)."
)
```

The script prints the first raw response in full, inside a border:

```
╭──────────── raw response, attempt 1 of 5 ────────────╮
│ ```json                                              │
│ {                                                    │
│   "severity": "high",                                │
│   "summary": "High error rate on payment-service...  │
│   "confidence": 0.8                                  │
│ }                                                    │
│ ```                                                  │
╰──────────────────────────────────────────────────────╯
  ✘ attempt 1 not JSON at all (Expecting value)
```

**Stop on this.** The JSON *inside* is flawless — right fields, right values,
`confidence` correctly a decimal. It's unusable anyway, because of six
backticks. All five attempts fail identically.

Markdown code fences. The model isn't being difficult — it's being *helpful*,
because that's how JSON appears in nearly all the text it learned from. It did
what we asked. We didn't ask precisely enough.

> **Instructor:** the border is doing real work here — it shows exactly where
> the model's output starts, so the fence is obviously part of the response
> rather than part of your terminal. The `repr()` line underneath makes the
> `\n` visible too.

**Part 3** applies the obvious fixes — forbid fences explicitly, give an exact
example — and gets 5/5.

**What just happened:** you fixed it with prompt engineering. That is exactly
the trap.

You are green on *one* alert, at *one* temperature, on *one* model version.
Nothing about that generalises and nothing about it is enforced. A passing run
tells you nothing about the next alert, a longer description, or a model
upgrade.

Note which change did the work: the **explicit example**, not the stern
instruction. Showing the shape beats stating the rule — worth remembering
whenever you write a prompt.

> **Instructor:** resist declaring victory at 5/5. The question to ask the room
> is *"what would have to happen for you to find out this broke?"* The answer
> is usually "a customer tells us", and that's the argument for the next step.

---

### Step 2 — Constrain the model

**Why:** the fix isn't a better prompt. It's removing the model's ability to
answer wrongly.

```bash
python modules/02-triage/02_structured_output.py
```

**What you should see:** the JSON Schema we send, then five attempts, then a
full result — and a routing decision made from it.

Read `schemas.py` first. `TriageResult` declares four fields, and the
constraints are doing real work:

```python
severity: Literal["low", "medium", "high", "critical"]
confidence: float = Field(ge=0.0, le=1.0, description="...")
```

Then the line that changes everything:

```python
structured_llm = llm.with_structured_output(TriageResult, include_raw=True)
```

No `json.loads`. No try/except around parsing. `out["parsed"]` arrives as a
`TriageResult` instance.

`include_raw=True` returns `{"raw", "parsed", "parsing_error"}` instead of a
bare result. We need it for a mundane reason worth stating out loud: **token
usage lives on the raw message.** Parse straight to the Pydantic object and the
metrics table reports zeros for every call — silently, which is the worst way
for a measurement to be wrong. (This bit us while writing the module.)

**Look at the raw JSON the script prints**, and at the two numbers under it:

```
╭──────────────── Model returned ────────────────╮
│ {                                              │
│   "severity": "critical",                      │
│   "summary": "High error rate on POST /v1/...  │
│   "hypothesis": "The payment-service is ...    │
│   "confidence": 80                             │
│ }                                              │
╰────────────────────────────────────────────────╯

  ~ attempt 1 well-formed JSON, but confidence=80 is outside 0.0-1.0
  …
  structurally valid  5/5   (object, keys, types, enum)
  fully valid         0/5   (the above, plus every value in range)
```

No fences, no preamble, never a missing key, never an invented severity — the
structural problem is solved permanently. And **not one attempt is usable**,
because `confidence` is `80`.

`severity`, by contrast, was genuinely constrained: it is always one of our
four words, on every call, because enums are grammar-enforceable.

**What just happened:** you moved the *structural* half of the guarantee from
the prompt into the schema, where machinery enforces it. The *semantic* half is
still yours. Step 3 is how we take it.

> **Instructor:** the temptation here is to reach for a validator that divides
> by 100 and move on. Say out loud why we don't: a model on a 0-10 scale writes
> `8` meaning `0.8`, and that rule silently produces `0.08`. Repairing output
> you don't understand is how you get a confident number that's wrong by 10x
> with nothing in the logs.

---

#### "But the tuned prompt also got 5/5. Why bother?"

The obvious objection, and it deserves a real answer rather than hand-waving.
Both approaches passed. Measured on the same alert, `qwen2.5:7b`, temp 0.1:

| | Tuned prompt (part 3) | Schema-constrained (step 2) |
|---|---|---|
| Structural failure possible? | **Yes** — 0/5 before tuning | **No** — grammar forbids it |
| Input tokens per call | **168** | **101** |
| `confidence` returned | **0.8** ✅ | **80** ❌ |
| Parsing code you write | ~25 lines by hand | none |
| Change a field | edit prompt *and* parser, keep in sync | edit the Pydantic class only |
| Holds for a new alert? | unknown — tuned on one | yes, structurally |

Three things worth drawing out:

**The prompt is more expensive, forever.** Those instructions and the example
ride along on *every single request* — 67 extra input tokens here, on a
one-paragraph alert. The schema is enforced by the sampler, not paid for in
prompt tokens.

**But the prompt won on the value.** The example `"confidence": 0.0` anchored
the model to decimals; the schema's `"maximum": 1.0` did not. That's the
grammar/semantics split again, from the other direction — and it's why "just
use structured output" is incomplete advice.

**So use both.** Structure from the schema, semantics from the prompt. Adding
one sentence to the system prompt — *"Confidence is a decimal fraction, e.g.
0.85 — never a percentage like 85"* — moves the raw model output from 0/5 in
range to **5/5**, for 27 extra input tokens.

> **Instructor:** this is the moment to kill "structured output solved it".
> The schema makes bad *shapes* impossible. It does nothing about bad *values*,
> and a prompt example is often the cheaper lever there. Layer them
> deliberately, and know what each layer is buying.

We deliberately leave that sentence *out* of `triage.py`, so the failure stays
visible and the retry loop has real work to do in front of you. Putting it back
is one of the experiments below — and it is what you would ship.

---

### Step 3 — Harden it

**Why:** "mostly can't fail" is not "can't fail", and this runs unattended at
2 AM.

```bash
python modules/02-triage/03_defensive_triage.py
```

**What you should see:** all three sample alerts triaged, a comparison table,
and the metrics table at the bottom.

The logic lives in `triage.py`, which every later script imports. Three layers:

1. **Schema-constrained generation** — step 2
2. **Validation** — Pydantic rejects out-of-range values before they travel
3. **Retry with feedback** — the failure is quoted back to the model

`include_raw=True` earns its keep twice. We used it in step 2 for token counts;
here it also gives us `parsing_error` — the specific complaint we can quote
back to the model:

```python
messages.append(result["raw"])
messages.append(HumanMessage(
    f"That response was not valid: {last_error}\n"
    "Reply again, matching the required schema exactly."
))
```

Without `include_raw`, a bad response simply raises, and you lose both the
token counts and the text that failed — the two things you most need in order
to react and to debug.

And when it truly can't comply, it fails loudly:

```python
raise TriageError(f"No valid triage after {max_attempts} attempts. ...")
```

Never return a half-built or invented result. A caller cannot tell the
difference between a real answer and a fabricated one — that's precisely why
this whole module exists.

**Look at the comparison table**, which is where the interesting failure lives.
A representative run:

```
alert                 monitoring said   model said   confidence   agreement
checkout_latency      warning           high         0.70         DIFFERS
disk_usage            warning           medium       0.90         DIFFERS
payment_error_spike   critical          high         0.70         DIFFERS
```

Your numbers will differ. Three things to draw out, all of them robust across
runs:

**Every row disagrees with monitoring.** Not one alert came back with the
severity it arrived with. That's either three mis-tuned thresholds or a model
with an opinion of its own, and you cannot tell which from here.

**It doesn't separate the serious one.** `checkout_latency` — a slow page
during a marketing campaign, with no error-rate change — scores the same
`high` as 12% of payments failing. Those are not the same emergency, and this
stage cannot tell them apart.

**It isn't stable.** Run it twice. `disk_usage` moves between `medium` and
`high` on *identical input* at temperature 0.1, and confidence swings 0.70 to
0.90. Same input, different answer — the non-determinism from module 0, showing
up in something you might have been tempted to page on.

Don't paper over any of it. This is the honest state of the pipeline after one
stage, and it's the argument for everything that follows: the model is guessing
from a single paragraph, with no access to service state, deploy history, or
your runbook. Structure was never going to fix judgement.

> **Instructor:** ask the room whether they'd page on these results, then run
> the script a second time and ask again. Watching a severity change on
> identical input lands harder than any slide about non-determinism.

**Then the payoff — the thing this whole module exists to enable:**

```python
if result.severity in ("high", "critical") and result.confidence >= 0.7:
    console.print("→ would page the on-call engineer")
```

Ordinary code on typed fields. `result.confidence` is guaranteed to be a float
between 0.0 and 1.0 — **not** because the model complied, but because anything
else was rejected and retried until it did.

**What just happened — and why every alert now passes first time.**

Step 2 could not get `confidence` in range at all: 0/5. Here it succeeds on the
first attempt, every time. Nothing about the schema changed. `triage.py` adds
**one sentence** to the system prompt:

> Confidence is a decimal fraction between 0 and 1, for example 0.85. Never
> express it as a percentage such as 85.

Measured on `qwen2.5:7b`, first attempt, six runs across all three alerts:

| System prompt | `confidence` in range |
|---|---|
| without that sentence | **0/6** — 70, 70, 70, 95, 70, 70 |
| with it | **6/6** — 0.75, 0.75, 0.9, 0.9, 0.85, 0.85 |

That is the lesson of Steps 2 and 3 together, and it is worth stating carefully,
because "the schema didn't work" would be the wrong takeaway.

**There are three enforcement points, not one.** Your Pydantic class feeds all
three, and they have different coverage:

| Point | Mechanism | Covers | `maximum: 1.0`? |
|---|---|---|---|
| **Prompt** | words the model reads | anything you can explain | only if you write it there |
| **Generation** | schema compiled to a sampling grammar | structure, keys, types, `enum` | ❌ dropped by the converter |
| **Validation** | Pydantic, after parsing | every constraint you declared | ✅ this is what rejects `80` |

The schema is doing enormous work at the generation point — it is the entire
reason Step 2 got well-formed JSON with a valid `severity` on 5 attempts out of
5, from a model that could not manage bare JSON at all in Step 1. That *is* the
point of the schema, and it is delivered.

What it does not do is stop the model *producing* `80`. That one keyword is
dropped when llama.cpp compiles the schema into a grammar — structure and
`enum` survive the translation, numeric bounds do not. (Nothing makes bounds
impossible to express as a grammar; this runtime's converter simply doesn't.)

And `maximum: 1.0` is very far from useless: it is precisely what **rejects**
`confidence: 80` at the validation point. Delete `le=1.0` from `schemas.py` and
the 80 flows straight into the pipeline and gets routed on. The bound cannot
*prevent* the mistake, but it is the only thing that *catches* it.

So the sentence to land is narrower than "schema versus prompt":

> The grammar controls what the model **can** emit. The prompt influences what
> it **aims** to emit. Validation decides what you **accept**. Put each
> constraint where it is actually enforced, and know which of the three you are
> relying on.

Here, twenty-odd words in the prompt — which the model does read — moved the
model's aim, so validation stopped having to reject anything.

So where does that leave the retry? As a **safety net that no longer has to
fire on the common path**, which is exactly where you want it. It is still
there for the model that changes, the edge case you did not anticipate, and the
connection that drops. Step 4 triggers it deliberately so you can watch it work.

> **Instructor:** worth pausing on the alternative nobody should take. The
> tempting fix for `confidence: 80` is a validator that divides by 100. It
> would have "worked" — and it would guess at intent, silently turning a model
> on a 0-10 scale writing `8` into `0.08`. Fix the instruction, validate the
> result, retry if it still disagrees. Never quietly rewrite data you did not
> understand.

---

### Step 4 — Watch a retry actually fire

**Why:** Step 3's retry is a safety net that now rarely fires. This triggers it
on purpose and prints every message in both directions — including the
feedback, which is the part you normally never see.

```bash
python modules/02-triage/04_retry_demo.py
```

**This runs the real `triage()`.** Not a copy, not a simplified version — the
same function `05_api.py` serves requests with. `show_transcript=True` only
turns on printing.

#### The invariant to point at

> **The request never changes.** Same model, same schema, same system prompt,
> same temperature — all fixed before the loop starts. Between attempt 1 and
> attempt 2 the *only* difference is that two messages were appended.

That is what makes this a retry rather than a different strategy. It is worth
saying out loud, because it is easy to build a "retry" that quietly changes the
request and then to believe the wrong thing about why it recovered.

```python
structured_llm = llm.with_structured_output(schema, include_raw=True)  # built ONCE
for attempt in range(1, max_attempts + 1):
    result = structured_llm.invoke(messages)   # identical call, every time
    ...
    messages.append(result["raw"])             # only this grows
    messages.append(HumanMessage(feedback))
```

#### How the failure is forced

We do not rely on the model misbehaving — that was an earlier version of this
demo, and it broke the moment you changed `CHAT_MODEL`. Instead
`DemoTriageResult` adds one house rule:

```python
class DemoTriageResult(TriageResult):
    @field_validator("summary")
    @classmethod
    def _house_style(cls, v):
        if not v.startswith("SEV: "):
            raise ValueError("summary must begin with the exact prefix 'SEV: '")
        return v
```

Stand-in for the kind of formatting rule real tooling imposes — a pager that
only renders summaries with a known prefix, a log pipeline that greps for a
marker. **No model can satisfy it on a first attempt, because no model can know
it**, and that follows from two facts established in Step 2:

1. **Validators are not in the JSON Schema.** Search
   `DemoTriageResult.model_json_schema()` for `SEV` — it is not there. Only
   `Field(...)` constraints are serialised; a `@field_validator` is ordinary
   Python that runs after parsing.
2. **Ollama never shows the schema to the model anyway.**

So the rule is genuinely unknowable until it appears in a message. Which is
precisely what the retry does.

**What you should see:**

```
╭──── The request — identical on every attempt ────╮
│ [ system  ] You are an experienced site reliab…  │
│ [  human  ] Alert details: - Service: payment…   │
╰──────────────────────────────────────────────────╯
──────────────────── Attempt 1 ────────────────────
╭───────────────── Model returned ─────────────────╮
│ { "severity": "high", "summary": "The http_5xx…  │
╰──────────────────────────────────────────────────╯
✘ rejected:
  Value error, summary must begin with the exact prefix 'SEV: '

╭─────── Appended to the conversation ─────────────╮
│ [   ai    ] { "severity": "high", … }            │
│ [  human  ] That response was not valid: …       │
│             Reply again, matching the required   │
│             schema exactly.                      │
╰──────────────────────────────────────────────────╯
──────────────────── Attempt 2 ────────────────────
Same model, same schema, same system prompt. The only
difference from attempt 1 is the two messages above.
╭───────────────── Model returned ─────────────────╮
│ { "summary": "SEV: The http_5xx_rate_percent…  } │
╰──────────────────────────────────────────────────╯
✔ Valid on attempt 2.
```

Recovers on attempt 2 in 5 of 5 runs.

Three things to point at:

**The request panel prints once**, because there is only one request. Everything
after it is the same call with a longer conversation.

**The feedback is two messages, not one.** The model's own failed answer goes
back alongside the complaint, so it can see *what it said* and *why that was
wrong*. A complaint with no referent is much weaker.

**Read the input tokens per attempt.** Each round resends the whole growing
conversation, so a retry costs more than double and compounds. That is why
`max_attempts` exists — a retry loop without a cap is an infinite loop with a
billing department.

> **Instructor:** the question worth asking is *"what would you have logged?"*
> Most systems log "triage failed, retrying" and throw away both the bad answer
> and the reason. This transcript is what you actually need at 3 AM.

---

### Step 5 — Put it behind HTTP

**Why:** real alerts are pushed by a monitoring system, not loaded from disk.

```bash
python modules/02-triage/05_api.py
```

Leave it running. In a second terminal:

```bash
curl -s -X POST http://127.0.0.1:8000/alert \
  -H 'Content-Type: application/json' \
  -d @data/sample_alerts/payment_error_spike.json | python3 -m json.tool
```

**What you should see:** the server logs the alert and its verdict; the `curl`
returns the alert echoed back alongside the triage.

The entire request-handling contract is one annotation:

```python
@app.post("/alert", response_model=TriageResponse)
def receive_alert(alert: Alert):
```

`alert: Alert` makes FastAPI read the body, parse it, and validate it. Send
something malformed and you get a precise 422 — **and the model is never
called**, which is worth noticing: validation at the edge means garbage costs
you nothing.

```bash
curl -s -X POST http://127.0.0.1:8000/alert \
  -H 'Content-Type: application/json' \
  -d '{"service": "x", "severity": "apocalyptic"}' | python3 -m json.tool
```

Then open the docs FastAPI generated from those same models:

```bash
open http://127.0.0.1:8000/docs
```

> **Instructor:** this lands well live. `Alert` and `TriageResult` were written
> once, and they're now doing four jobs: runtime validation, the HTTP contract,
> the model's generation constraint, and this documentation page.

One detail worth calling out in the code — `def`, not `async def`:

```python
def receive_alert(alert: Alert):
```

Our `triage()` call blocks while the model thinks. FastAPI runs plain `def`
handlers in a thread pool, so one slow call doesn't stall the server. Wrapping
blocking code in `async def` would freeze the event loop — a common and painful
mistake.

**What just happened:** you have a running service that turns unstructured
incidents into structured decisions, with validation at both edges.

---

## What we just built

The first real stage of the pipeline, and the pattern every later stage reuses:
**declare the contract, constrain the model to it, validate the result, retry
with feedback, fail honestly.**

You also have a working answer to "how do I get reliable structured data out of
an LLM?" — which is the question that stops most prototypes from becoming
systems.

---

## Live experiments (10 min)

Pick one and report back.

**Delete the sentence and watch the retries reappear.** Remove the
*"Confidence is a decimal fraction…"* line from `SYSTEM_PROMPT` in `triage.py`
and re-run Step 3. Every alert now needs a second attempt, and the metrics
table roughly doubles: `triage-attempt-2` resends the whole conversation plus
the failed answer plus the error. Twenty-seven prompt tokens were buying you a
whole extra call per alert, forever. Put it back.

**Prove which half is enforced.** In `schemas.py`, add `"apocalyptic"` to the
`Literal` for `TriageResult.severity` and re-run step 2 — the model can now
choose it. Then remove it again and try to make the model emit it via the
prompt. You can't: the grammar won't let it. Compare that with how easily the
`maximum: 1.0` bound is ignored.

**Prove the descriptions are ignored.** Replace the `description=` on
`hypothesis` with something useless like `"x"`, re-run step 2, and watch the
answer quality not budge. Then put the same guidance in `SYSTEM_PROMPT`
instead and re-run. Only one of those two edits reaches the model — this is the
fastest way to feel the difference between a schema and a prompt.

**Disagree with monitoring.** Edit `data/sample_alerts/disk_usage.json` to
`"severity": "critical"` while leaving the description mild. Does the model
push back, or does it defer to the label?

---

## Homework

**Add a field that changes a decision.** Extend `TriageResult` with:

```python
blast_radius: Literal["single-user", "single-service", "multi-service", "platform-wide"]
```

then use it in the routing logic in
`02_structured_output.py`. Notice what you had to change: the schema, and
nothing else. The prompt, the parsing, and the API all followed automatically.

Come back able to answer: **where did the model learn what your new field
means?** Be specific about which text reached it.

---

## Checkpoint ✅

You're done when:

- [ ] You've seen the naive JSON prompt fail 0/5, and seen the code fences
- [ ] You can say why 5/5 after prompt tuning is *not* the same as a guarantee
- [ ] You can name one thing constrained decoding enforces and one it doesn't
- [ ] `03_defensive_triage.py` triages all three sample alerts
- [ ] You've watched `04_retry_demo.py` fail, get told why, and recover
- [ ] You noticed the model rated all three `high`, and can say why that matters
- [ ] You've `curl`ed an alert into the running API and got structured triage
- [ ] You've sent a malformed alert and got a 422 without the model being called
- [ ] You can explain why a retry carries the error message

---

## Discussion questions

**1. Constrained decoding guarantees valid JSON. What does it *not* guarantee?**

<details><summary>Answer</summary>

Two distinct things, and it's worth separating them.

**Semantic constraints on values.** The grammar enforces types and `enum`s, not
`minimum`/`maximum`/`pattern`. We measured this: `"maximum": 1.0` in the schema,
and `qwen2.5:7b` returned `80` on 6 of 6 runs. Meanwhile the `severity` enum
held on every call. Half your schema is enforced by the machinery; the other
half is enforced by your validation layer, or not at all.

**Anything about truth.** The schema constrains **shape**, not **content**.
`{"severity": "low", "confidence": 0.99, "hypothesis": "Everything is fine"}`
is perfectly valid and could be catastrophically wrong. Constrained decoding
eliminates parsing failures — it does nothing about hallucination, poor
judgement, or overconfidence. Step 3 makes this concrete: the model's severities
barely separate a slow checkout page from 12% of payments failing, and they move
between runs on identical input. All of it perfectly well-formed.

Worth naming the trap: valid, well-typed output *feels* trustworthy in a way
prose doesn't. The structure is a syntactic guarantee wearing the costume of a
semantic one. Everything later in this pipeline — grounding in real documents,
evaluation, guardrails, human approval — exists because this step cannot
address correctness.

</details>

**2. Why retry with the error message instead of just retrying?**

<details><summary>Answer</summary>

Because at low temperature, an identical prompt produces a near-identical
answer. A blind retry mostly re-rolls the same dice and buys you a second bill
for the same mistake.

Including the error changes the input, which changes the distribution. It also
matches how the failure would be fixed by a person: you don't repeat yourself
louder, you say what was wrong.

Costs to weigh: each retry is a full call in tokens and latency, and the
conversation grows each round. Cap the attempts, and treat a high retry rate as
a signal that the schema or the descriptions need work — not as something to
paper over with more retries.

</details>

**3. We validate alerts at the edge and return 422 without calling the model.
Why does that matter more here than in an ordinary CRUD API?**

<details><summary>Answer</summary>

Because the downstream operation is expensive, slow, and non-deterministic.
Rejecting bad input at the boundary of a CRUD service saves a database
round-trip. Rejecting it here saves seconds of GPU time, real tokens, and a
retry loop — per request.

There's a security dimension too. Everything that reaches the model becomes
*instructions* in some sense. A validated, well-typed alert is a much smaller
attack surface than an arbitrary blob of text, and narrowing what can reach the
model is the cheapest defence available.

</details>

**4. `Alert.severity` and `TriageResult.severity` use different scales. Why not
reuse one enum?**

<details><summary>Answer</summary>

Because they are different claims by different parties. One is what the
monitoring system asserted; the other is what our system concluded. Sharing a
type would invite code that conflates them, and it would make "monitoring said
critical, we assessed low" inexpressible.

That disagreement is arguably the most valuable output of the whole stage — it
finds mis-tuned thresholds and catches the model being wrong. Distinct scales
make the comparison deliberate every time it's made.

</details>

**5. `confidence: 80` is obviously meant to be `0.8`. Why not just divide by 100?**

<details><summary>Answer</summary>

Because "obviously" is doing enormous work in that sentence, and it is a guess
about intent rather than a fact about the data.

A model answering on a 0-10 scale writes `8` meaning `0.8`. The same rule turns
it into `0.08` — wrong by a factor of ten, structurally valid, and completely
invisible. The pipeline then routes on it, the metrics look fine, and nothing
in any log says a value was ever changed.

The deeper problem is that silent repair destroys evidence. A rejection is a
signal: it tells you the prompt is unclear, or the model changed, or your
schema doesn't say what you meant. Coercion converts that signal into a plausible
number and throws the signal away. You lose the ability to notice you were wrong.

The costs are real and worth naming: rejecting doubles the calls for this model,
and the retry resends the whole conversation. The cheap correct fix is the
prompt — one sentence, 27 tokens, and the failure stops happening at the source
rather than being papered over after the fact.

Rule of thumb: **repair what you can prove, reject what you have to guess at.**
Stripping a markdown fence is provable. Rescaling a number is a guess.

</details>

**6. Where would this break if you swapped `qwen2.5:7b` for a 70B model, or for
a hosted frontier model?**

<details><summary>Answer</summary>

Mostly it wouldn't — which is the point. The schema, validation, retry loop and
API are model-agnostic; `.with_structured_output()` is implemented across
providers, and `.env` is the only thing that changes.

What *would* change is the failure rate, and therefore how much the defences
earn their keep. A stronger model needs fewer retries, so step 3 looks like
overhead — right up until the provider degrades, a deploy changes the model
version, or an edge case appears at 3 AM. The defences are insurance, and
insurance always looks unnecessary until it isn't.

The other honest caveat: not every provider implements constrained decoding the
same way. Some do true grammar-constrained sampling; others do a best-effort
JSON mode with retries hidden inside the SDK. Same interface, different
guarantees.

</details>

---

**Next →** Module 3 — MCP tools: triage tells us what the model *thinks*, but
it's still guessing from one paragraph. Next we let it go and look — calling
real tools to check service status, recent deploys, and logs before forming an
opinion.
