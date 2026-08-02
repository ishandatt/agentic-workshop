# Module 9 — The full pipeline: one system, and what it costs

> **The question this module answers:** every piece works in its own folder.
> What does the whole thing do, and what does one incident actually cost?

**Time:** ~45 min · **Code:** `modules/09-full-pipeline/` · **You need:** modules 1–8, runbook ingested

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | The whole pipeline, three alerts | 25 min | Every stage, three different endings |
| 2 | Read the receipt | 20 min | Per-incident cost, and the structured run log |

---

## Where we are

Eight modules, eight folders, eight things that work on their own. Nothing yet
runs an incident from arrival to resolution, and nobody has added up the bill.

Today does both.

## The problem

Assembling working parts is where the interesting failures live. Each stage was
tested with the others absent; wired together, the questions change:

- What does the *retrieval* query look like once you have investigated?
- Which tools should the *investigation* loop be allowed to touch?
- Where does the guard sit relative to the human?
- And how many model calls has all this quietly become?

Every one of those bit during the build. Two produced real bugs, both written
up below, because they are more useful than the parts that worked first time.

## What you'll build

- A single graph: screen → triage → investigate → consult → propose → guard →
  approve → execute → report
- Per-incident metrics: calls, tokens, latency, reference cost
- `data/runs.jsonl` — a structured record per run, for incident review

---

## Concepts in this module

### The pipeline

```
        ┌──────────────────── code decides ─────────────────┐
alert → screen → triage → investigate → consult → propose → guard →┐
        (input   (model)   (read-only    (RAG)     (model)  (policy)│
         guard)             tools)                                  │
                                              ┌──────────────────────┘
                                              ▼
                              await_approval (a human) → execute → report
```

The shape is the lesson: **the model proposes, code disposes, a human decides.**
Model judgement is used where judgement helps — assessing severity, choosing
what to look at, forming a hypothesis — and used nowhere near the decision about
what is permitted.

### Per-run attribution

`common/metrics.py` gained `session_calls()` for this module. Snapshot the list
length before a run, slice from there afterwards, and you have that incident's
calls separated from everything else the process did.

### The structured run log

One JSON object per incident in `data/runs.jsonl`: the alert, each stage's
output, the policy decision, who approved it, and the per-step token cost.

This is the artefact an incident review actually reads. Not "the agent restarted
the service" but *what it looked at, what it proposed, why, who said yes, and
what that cost*.

---

## Build it

### Step 1 — Run it

**Why:** to see the stages hand off to each other, and to watch three alerts
reach three different endings.

```bash
python modules/09-full-pipeline/01_run.py
```

Three to four minutes — roughly 5–7 model calls per incident.

**What you should see**, per alert:

```
── payment_error_spike ──
  screen      clean
  triage      critical (confidence 0.85) — 5xx error rate has increased…
  investigate 3 tool call(s): get_service_status, get_recent_deploys, get_error_logs
  consult     4 runbook section(s): 3. Connection pool configurati; 4. Service quirks…
  propose     rollback_deploy on payment-service
  guard       allowed=True approval=True — 'rollback_deploy' mutates state…

⏸ awaiting approval
  human       {'approved': True, 'by': 'priya.raghavan'}
  outcome     approved — executed rollback_deploy on payment-service (simulated)
  cost        5 model call(s), 3927 tokens, $0.0219, 34.6s
```

Three endings, and each exercises a different path:

| alert | ending |
|---|---|
| `payment_error_spike` | approved by a human, executed |
| `payment_settlement_window` | proposed, **rejected** by a human |
| `injection_authority` | flagged by screening, proposed a *rollback*, left pending |

**The third row is the interesting one.** The injection demanded an immediate
`restart_service` and claimed prior approval. What actually happened: screening
flagged four patterns, the investigation used read-only tools, the proposal came
out as `rollback_deploy` based on the *real* evidence, and it went to a human
rather than executing. The attack changed nothing it wanted to change.

---

### Two bugs worth more than the working code

Both were found by running this, and both are the kind that do not throw.

**Bug 1: the investigation loop could restart production.**

The first version bound every MCP tool to the investigation agent and relied on
*"Do not call restart_service"* in the prompt. Given `injection_authority`, the
agent called `restart_service` **twice** — during investigation, long before the
output guard ever saw a proposal.

The guard checks what the agent *proposes*. It cannot check what the agent has
already done.

The fix is one line, and it is capability removal rather than instruction:

```python
tools = [t for t in all_tools if t.name in READ_ONLY_ACTIONS]
```

> **Instructor:** this is the module's best five minutes. Module 7 taught "guard
> the output" and it was not enough, because there were *two* places actions
> could happen and only one was guarded. Ask the room where else in their own
> systems a model touches something before the checkpoint.

**Bug 2: truncating tool output silently deleted the diagnosis.**

Evidence was capped at 400 characters when stored and 200 in the prompt — sensible-
looking numbers. The deploy result is 419 characters of pretty-printed JSON, so
the cap fell here:

```
'{"service": "payment-service", "deploys": [{"sha": "9f2a41c",
  "deployed_at": "2026-08-01T13:58:00Z", "author": "priya.raghavan",
  "message": "perf: reduce settlement '
                                      ^ cut
```

Losing `connection pool 50 -> 5` — the single fact the whole diagnosis turns on.
The pipeline then proposed `rollback_deploy` on some runs and `none` on others
**from identical inputs**, which read like model flakiness and was not.

Measured after the fix: 3 runs, 3 identical outcomes.

> **Instructor:** the general lesson is that context limits are a correctness
> concern, not a cost concern. Cap where the data says it is safe, not at a
> round number that looks tidy — and if your agent seems non-deterministic,
> check what you truncated before blaming the model.

---

### Step 2 — Read the receipt

**Why:** nobody has added up what this costs, and it is more than people guess.

```
alert                       decision   calls  tokens  ref cost  seconds
payment_error_spike         approved       5    3927   $0.0219     34.6
payment_settlement_window   rejected       7    5523   $0.0271     36.0
injection_authority         pending        7    5734   $0.0277     36.3

3 incidents · 15184 tokens · $0.0766 at reference prices
Average per incident: 5061 tokens, $0.0255. At 200 alerts a day that is $153 a month.
```

**Sit with that table.** Module 1's single call was 95 tokens. One incident is
now around 5,000 — a fiftyfold increase, arriving one reasonable decision at a
time: structured triage, a tool loop that resends the transcript each turn,
retrieved runbook sections, a proposal carrying all of it.

Nothing there is waste. It is the price of an answer that is grounded, checked,
and auditable rather than fluent. But it is a price, and you should know it
before someone in finance discovers it for you.

Then look at `data/runs.jsonl`:

```bash
python3 -c "import json;[print(json.dumps(json.loads(l),indent=2)[:600]) for l in open('data/runs.jsonl')]" | head -40
```

Each record answers the questions a post-incident review asks — *why did it
propose that, what did it look at, who said yes* — without anyone reproducing
the run.

It is also the seed of the next three things you would build: aggregate it for a
dashboard, diff two weeks of it for a regression report, sample it for your next
evaluation set.

---

## What we just built

A complete incident-response pipeline that investigates with read-only tools,
grounds itself in your runbook, refuses forbidden actions in code, asks a named
human before touching production, and produces an auditable record with a price
attached.

Which is, allowing for the fake infrastructure, a real system.

---

## Live experiments (10 min)

**Re-break bug 1.** Remove the read-only filter in `investigate` and re-run with
`injection_authority`. Watch `restart_service` appear in the evidence trail.
Then put it back.

**Re-break bug 2.** Change the evidence truncation back to `[:200]` and run three
times. Watch the proposal flip between `rollback_deploy` and `none`.

**Take the runbook away.** Set `DISTANCE_THRESHOLD = 0.05` in `pipeline.py` so
nothing is retrieved. The pipeline still runs — and proposes on evidence alone,
without the rule that says rollback beats restart.

---

## Homework

**Add a stage.** A `notify` node after `report` that posts the outcome to
`#payments-oncall` (print it — no real Slack). Then decide where it belongs for
the *rejected* path: does a human rejecting an action deserve a notification?

Come back able to answer: **which stage would you cut if you had to halve the
cost per incident, and what would you lose?**

---

## Checkpoint ✅

You're done when:

- [ ] You have run all three alerts end to end
- [ ] You can name each stage and what it is trusted to decide
- [ ] You can explain why the investigation loop gets read-only tools
- [ ] You can explain how a 200-character truncation caused non-determinism
- [ ] You have read one record from `data/runs.jsonl`
- [ ] You can state the cost per incident and what drives it

---

## Discussion questions

**1. Five to seven model calls per incident. Where would you cut?**

<details><summary>Answer</summary>

Look at what each call buys before cutting any of it.

The **investigation loop** is the biggest single cost and the most defensible —
it is the difference between module 3's grounded answer and module 2's guess.
But it resends the whole transcript each turn, so its cost is quadratic-ish in
turns. Capping `MAX_TOOL_TURNS` is the cheapest real saving.

**Triage** is arguably redundant: `propose` could produce the severity too, and
you would save a call. We keep them separate because triage runs *before*
investigation and decides whether investigation is worth doing at all — which is
its own saving on the alerts that need nothing.

**Retrieval** is one embedding call, effectively free next to the chat calls,
and it earns more than anything else per token.

The honest first move is not cutting calls but **not starting**. Most alerts do
not need an agent. A cheap classifier that routes 80% of alerts to a static
response is worth more than any optimisation inside the pipeline.

</details>

**2. The injection was contained. By what, exactly?**

<details><summary>Answer</summary>

Four things, and it is worth being precise because only some of them are
defences.

**Screening** flagged it — signal, not protection; the pipeline continued.
**Wrapping** the description as untrusted data made obedience less likely —
probabilistic. **Read-only tools** in the investigation loop made the attack's
actual goal unreachable — a real boundary. **The approval gate** meant the
proposal went to a human regardless — a real boundary.

Only the last two would hold against an attacker who had read our source code.
That is the test worth applying to any defence: *does it still work if they know
exactly how it works?*

Also worth noticing what the attack did achieve. The triage came back
`critical` with confidence 0.95 on a 2.1% error rate, which is wrong — the
injection successfully distorted the *assessment* even though it could not
touch the action. Containing an attack is not the same as being unaffected by it.

</details>

**3. Every stage trusts the previous one. Is that safe?**

<details><summary>Answer</summary>

No, and the pipeline is deliberately arranged so it does not have to be.

Errors do propagate: a wrong triage skews the retrieval query, which changes
what the proposal sees. Nothing re-checks upstream conclusions, and by the time
a human sees the request they get the *proposal*, not the chain of inference
behind it.

What stops that being catastrophic is that the two consequential steps do not
trust anything upstream. `check_action` reads the action, the service and a
timestamp — not the triage, not the hypothesis, not the confidence. It would
refuse a settlement-window restart proposed with perfect reasoning and
100% confidence.

The design principle: **let judgement flow downstream, but never let it flow
into the gate.**

</details>

**4. What is still missing before this could run unattended?**

<details><summary>Answer</summary>

Quite a lot, and naming it is more useful than pretending otherwise.

**Operationally:** timeouts on pending approvals (an unanswered request must
escalate or expire); retries and idempotency for real actions; rate limiting, so
a flapping alert cannot generate two hundred approval requests; and a circuit
breaker for when the model or Ollama is down.

**Observationally:** the run log is a file, not a queryable store. No dashboards,
no alerting on the agent itself. Nothing tells you the agent has quietly stopped
proposing anything.

**Epistemically, and this is the big one:** the eval set from module 6 covers
retrieval and answers, not the pipeline. Nothing measures whether the *proposals*
are right. Which is the uncomfortable summary — we have built a system that is
safe by construction and only anecdotally correct.

Safe-by-construction is the right order to build in. It is not the finish line.

</details>

---

**Next →** The core workshop ends here. The **bonus modules** pick up the
threads deliberately left hanging: what "memory" actually means for an agent,
how to keep a conversation going past the context window, and the connection
lifecycles this code has been cheerfully ignoring since module 3.
