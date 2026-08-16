# Module 8 — Human approval: an agent that stops

> **The question this module answers:** the guard said "needs approval" and
> nobody was asked. How do we actually put a person in the loop?

**Time:** ~45 min · **Code:** `modules/08-approval/` · **You need:** module 7 finished, Postgres running

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | Pause and resume | 15 min | `interrupt()`, three outcomes |
| 2 | Survive the process dying | 12 min | Two commands, no running process between them |
| 3 | The approval queue | 18 min | `/pending`, `/approve/{id}`, `/reject/{id}` |

---

## Where we are

Module 7 built a guard that classified `rollback_deploy` as *allowed, requires
approval* — and then the program printed that verdict and exited. The action sat
in a variable with `requires_approval: True` attached and no human in sight.

There was no mechanism to ask anyone, because asking is harder than it looks.
The person you need is asleep. They will answer in four hours. Your process
cannot sit in `input()` for four hours, and the alert that triggered all this
must not be lost when the pod restarts.

## The problem

A blocking prompt is not human-in-the-loop, it is a hostage situation. Real
approval means:

- the run **stops** before doing anything consequential
- its state **survives** the process exiting
- someone else, **later**, from a different program, decides
- the run **continues from where it stopped**, not from the beginning

That last point matters more than it sounds. Restarting from the beginning means
re-running the investigation, re-paying for the tokens, and possibly reaching a
different conclusion than the one that was approved.

## What you'll build

- A graph that pauses with `interrupt()` before any mutating action
- Postgres-backed checkpoints, so a paused run outlives its process
- An approval queue with `/pending`, `/approve/{id}`, `/reject/{id}`
- Protection against the same action being approved twice

---

## Concepts in this module

### `interrupt()`

LangGraph's pause. Called inside a node, it does two things: hands a payload out
to whoever invoked the graph, and saves the entire state to the checkpointer
before raising out of the run.

```python
answer = interrupt({"kind": "approval_request", "action": action})
# execution reaches the next line only after a resume
```

**The part that surprises people:** on resume, the node runs *again from the
top*, and `interrupt()` returns the supplied value instead of interrupting. So
everything above the interrupt executes twice. Keep side effects out of it, or
you will send two Slack messages per approval.

### Checkpointer

Where graph state is stored between steps. `MemorySaver` keeps it in a dict —
fine for tests, useless here, because the whole point is surviving process
death. We use `PostgresSaver`.

**A checkpointer is required for interrupts.** Without one there is nowhere to
save the paused state, so there is nothing to resume.

### Thread id

The key a run is stored under, and the handle you resume with. In a real system
this is your incident id. It must be unique per run and stable across processes.

### `Command(resume=...)`

How the answer goes back in. Re-invoke the graph with a `Command` instead of a
fresh state, and it loads the checkpoint, re-enters the interrupted node, and
carries on.

---

## Build it

### Step 1 — Pause and resume

**Why:** to see the mechanism with nothing else in the way.

```bash
python modules/08-approval/01_interrupt.py
```

Three runs of the same graph:

```
── approved ──
⏸ waiting for a human
  rollback_deploy on payment-service
  Deploy 9f2a41c reduced the settlement pool from 50 to 5…
Human says: {'approved': True, 'by': 'priya.raghavan'}
  decision: approved (by priya.raghavan)
  outcome : executed rollback_deploy on payment-service (simulated)

── rejected ──
  decision: rejected (by tom.oyelaran)
  outcome : rejected by tom.oyelaran

── forbidden by policy ──
Ran to completion without pausing.
  outcome: blocked by policy: payment-service must not be restarted during
           the settlement window (14:00-16:00 IST); alert time is 14:45 IST
```

**The third run is the one to talk about.** Nobody was asked. The alert fires
inside the settlement window and proposes a restart, so module 7's guard refused
it and the graph never reached the approval node.

That ordering — **guard first, then ask** — is deliberate. Interrupting someone
to approve an action policy already forbids trains them to click approve, and
the reviewer's attention is the scarce resource the whole gate depends on.

> **Instructor:** worth drawing the graph on a whiteboard here.
> `propose → guard → (await_approval | execute | finish)`. The conditional edge
> out of `guard` is where the three outcomes separate, and it is ordinary Python
> returning a node name.

---

### Step 2 — Survive the process dying

**Why:** step 1 paused and resumed inside one program, which a function call
could have done. This tests the real claim.

Run it twice, as two separate commands:

```bash
python modules/08-approval/02_persistence.py
```

```
⏸ paused, awaiting a human
  rollback_deploy on payment-service

This process is about to exit.
The run lives on in Postgres under thread id 'incident-persistence-demo'.
```

The process **exits**. Nothing is holding the incident. Now:

```bash
python modules/08-approval/02_persistence.py --approve
```

```
loaded from Postgres
  Found a paused run for thread incident-persistence-demo
  stopped at node: ('await_approval',)
  proposed: rollback_deploy on payment-service

  decision: approved (by priya.raghavan)
  outcome : executed rollback_deploy on payment-service (simulated)
```

**Between those two commands there was no running process.** The incident
existed only as rows in a database. The second invocation used `get_state()` to
discover a paused run — which is how a *different* program finds work waiting —
then resumed it.

**What just happened:** the agent's state stopped being a Python object and
became data. That is the property that makes everything in this module possible,
and it is why module 3 introduced LangGraph rather than leaving the loop as a
`while`.

---

### Step 3 — The approval queue

**Why:** because "resume the thread you already know about" is not an inbox.

```bash
python modules/08-approval/03_api.py
```

Send an alert:

```bash
curl -s -X POST http://127.0.0.1:8000/alert -H 'Content-Type: application/json' \
  -d @data/sample_alerts/payment_error_spike.json | python3 -m json.tool
```

```json
{"id": "db8a0802", "status": "awaiting_approval",
 "action": "rollback_deploy", "service": "payment-service",
 "reason": "Deploy 9f2a41c reduced the settlement pool from 50 to 5…"}
```

**The request returned immediately.** It did not block waiting for a human. Now
the on-call inbox:

```bash
curl -s http://127.0.0.1:8000/pending | python3 -m json.tool
```

And the decision, possibly hours later, from anywhere:

```bash
curl -s -X POST http://127.0.0.1:8000/approve/db8a0802 \
  -H 'Content-Type: application/json' -d '{"by":"priya.raghavan"}'
```

```json
{"id": "db8a0802", "decision": "approved", "decided_by": "priya.raghavan",
 "outcome": "executed rollback_deploy on payment-service (simulated)"}
```

**Two design points worth pausing on.**

**Why a `pending_approvals` table when the checkpointer already stores the run?**
Because a checkpoint is keyed by thread id: perfect for "resume this run",
useless for "what is waiting for me?". A queue must be *listable*, and that is a
different access pattern. So we keep a small table alongside.

**Double-approval is a 409, not a second execution:**

```bash
curl -X POST http://127.0.0.1:8000/approve/db8a0802 -d '{"by":"priya.raghavan"}'
# HTTP 409  incident db8a0802 is already approved
```

Someone will double-click. Someone will retry a request that actually succeeded.
Without that check, the rollback runs twice.

**And the policy path still short-circuits:**

```bash
curl -s -X POST http://127.0.0.1:8000/alert -H 'Content-Type: application/json' \
  -d @data/sample_alerts/payment_settlement_window.json
```

```json
{"status": "completed",
 "outcome": "blocked by policy: payment-service must not be restarted during
             the settlement window (14:00-16:00 IST); alert time is 14:45 IST"}
```

No queue entry, because there is nothing to ask.

---

## What we just built

An agent that can be interrupted, persisted, and resumed by someone else, hours
later, without losing its work — and a queue that makes "what needs me?" a
single HTTP call.

---

## Live experiments (10 min)

**Kill it mid-flight.** Start `02_persistence.py`, then `Ctrl-C` during the
pause. Run `--approve`. It still works — there was nothing to interrupt.

**Approve from the database.** While an incident is pending, look at it directly:

```sql
SELECT id, service, action, status FROM pending_approvals;
SELECT thread_id, type FROM checkpoints LIMIT 5;
```

**Make the interrupt run twice.** Add `print("asking...")` as the first line of
`await_approval`, above the `interrupt()` call, then approve something. It prints
twice. That is the re-execution behaviour, and it is why side effects belong
below the interrupt.

**Reject and check.** `POST /reject/{id}` then `GET /pending` — the queue empties
and `outcome` records who said no.

---

## Homework

**Add a timeout.** A pending approval that nobody answers for an hour should do
*something* — escalate per the runbook (`#payments-oncall`, then Priya), or
expire and record that it did.

Come back able to answer: **what should the default be when nobody responds?**
Auto-approve is obviously wrong. Auto-reject is safer and means an outage
continues while everyone sleeps. There is no free answer, which is the point.

---

## Checkpoint ✅

You're done when:

- [ ] You can explain what `interrupt()` does to the graph's state
- [ ] You have resumed a run from a process that did not start it
- [ ] You can say why the guard runs before the approval gate, not after
- [ ] You have seen a double-approval rejected with 409
- [ ] You can explain why there is a table as well as a checkpointer
- [ ] You know which code in `await_approval` executes twice

---

## Discussion questions

**1. Is an approval gate a real control, or does it just move the blame?**

<details><summary>Answer</summary>

It is real, and it degrades badly under load, and both facts matter.

It is real because it bounds the worst case. An unattended agent that can
restart production has an unbounded worst case; one that can only *propose* has
a worst case of "someone was asked a question".

It degrades because approval quality is a function of approval **volume**. A
system generating forty requests an hour trains its operators to click approve,
and then you have a rubber stamp with extra steps and a stronger illusion of
safety than you started with.

Which makes the design goal counter-intuitive: **minimise the number of
approvals**, not maximise them. Auto-execute reads. Let policy refuse the
obvious cases outright, without asking. Reserve the interrupt for decisions a
human can genuinely add judgement to. Every request you remove makes the
remaining ones more likely to be read.

</details>

**2. On resume, the node runs again from the top. What breaks?**

<details><summary>Answer</summary>

Anything with a side effect above the `interrupt()` call. Post to Slack there
and every approval sends two messages. Increment a counter and it double-counts.
Charge something and you have charged twice.

The rule is: **code above the interrupt must be idempotent**, because it is
re-execution on resume, not a continuation.

It also affects reading. Any value computed above the interrupt is recomputed
from the *checkpointed* state — so if the world changed while the human was
asleep, you may be re-deciding on stale inputs. Our proposal is deterministic so
it does not bite, but an agent that re-queried service status on resume could
get a different answer than the one the human approved.

Worth stating plainly: the thing being approved should be a **snapshot**, not a
promise to re-derive.

</details>

**3. Postgres holds both the checkpoints and the queue. Is that fine?**

<details><summary>Answer</summary>

At this scale, yes, and it buys something real: the approval decision and the
resumed run can be made atomic, because they are in one database.

Two things to watch as it grows. Checkpoint tables get large — every step of
every run is a row, so retention is a policy decision you should make on purpose
rather than discovering. And our API opens a new connection per operation, which
is honest at workshop scale and wasteful at any other; a pool is the fix, and
it is one of the bonus modules.

The structural point: the checkpointer is a **library's** storage, and its schema
belongs to LangGraph. Do not query it as though it were yours. Our
`pending_approvals` table exists partly for that reason — it is the interface we
own and can change.

</details>

**4. What if the human approves the wrong thing?**

<details><summary>Answer</summary>

Then the wrong thing happens, and that is not a flaw in the design so much as
where the design deliberately stops.

The gate is not a correctness check, it is an **accountability boundary**: a
named person decided, at a recorded time, with the reason in front of them. That
is what `decided_by` and `decided_at` exist for, and it is what an incident
review needs.

Which puts weight on what the request *says*. Ours shows the action, the target
and the reason — an approval UI showing only "Approve action #4471?" is worse
than no approval at all, because it manufactures a signature without informed
consent.

And note what the gate does not remove: policy still refused the settlement-
window restart with no human involved. Some things should not be approvable at
all, and encoding those in code rather than in a reviewer's judgement is what
module 7 was for.

</details>

---

**Next →** [Module 9 — the full pipeline](09-full-pipeline.md): every piece now exists in its own
folder. Time to wire them into one system and see what an end-to-end incident
actually costs.
