# Bonus 1 — Memory: four things wearing one word

> **The question this module answers:** people say "give the agent memory" and
> mean four different mechanisms. Which one do you actually need?

**Time:** ~35 min · **Code:** `modules/10-memory/` · **You need:** module 4 ingested

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | Three kinds of conversation memory | 18 min | None, buffer, summary — measured |
| 2 | Memory of what happened | 17 min | Retrieval over past incidents |

---

## Where we are

The core workshop is done. This picks up a thread it left hanging: the pipeline
in module 9 has no memory at all. Each alert arrives, is handled, and is
forgotten. The same incident next Tuesday starts from zero.

## The problem

"Memory" collapses four separate mechanisms into one word:

| what people mean | mechanism | where it lives |
|---|---|---|
| remembers this conversation | buffer / summary | the message list |
| remembers past events | vector retrieval | a database |
| remembers workflow position | checkpointer | module 8's Postgres tables |
| remembers facts about you | key/value store | wherever you put it |

They have different costs, different failure modes, and choosing wrongly is
expensive. This module builds two of them and points at where the other two
already exist in the workshop.

## What you'll build

- The same four-turn conversation run three ways — no memory, buffer, summary —
  with per-turn token costs, so the cost *shape* of each is visible rather than
  asserted
- An episodic store: eight past incidents embedded into their own collection,
  retrieved when a similar alert arrives
- A comparison of the same new alert answered with and without that recall

---

## Concepts in this module

### Buffer memory

Keep every message and resend them all. Perfect recall, and cost grows with the
square of conversation length — each turn resends everything before it. This is
what modules 3 and 9 do inside their loops.

### Summary memory

After each turn, compress the conversation into a paragraph and carry only
that. Flat prompt size, at the cost of an extra model call per turn — and it is
lossy, in ways you do not control.

### Episodic (semantic) memory

Store records of past events; retrieve the similar ones when something new
arrives. Mechanically identical to module 4's RAG — same embeddings, same
store, different corpus.

### Checkpoint memory

The graph's own state, persisted. Module 8 already built this: an incident
paused for approval remembers everything about itself across process restarts.

---

## Build it

### Step 1 — Three kinds of conversation memory

```bash
python modules/10-memory/01_kinds_of_memory.py
```

A four-turn conversation where the last question is only answerable if you
remember the first.

```
── No memory ──
Q: Remind me — which service were we talking about, and what was the error rate?
A: I don't have a record of our previous conversation details.

── Buffer memory ──
A: We were discussing the payment-service, which is experiencing 5xx errors
   with an error rate of about 12% of requests.
   per-turn cost: [94, 261, 417, 472]

── Summary memory ──
A: We were discussing a payment service that is experiencing 5xx errors in
   about 12% of requests.
```

```
 strategy   tokens   recalled?   cost shape
 none          298   no          flat
 buffer       1244   yes         grows every turn
 summary      1279   yes         flat-ish, +1 call/turn
```

**Read the per-turn costs, not the totals.** Buffer went 94 → 261 → 417 → 472.
Over four turns it looks cheap; the shape is what kills you. At fifty turns it
is the dominant cost in your system, and at some point the call simply fails.

**Summary memory costs more here**, because paying an extra model call per turn
is a terrible trade over four turns. It is the only thing that works over a
hundred.

**And notice the drift.** Buffer said "payment-service"; summary said "a payment
service". The summariser was explicitly told to preserve specifics and still
softened one. What you compress, you may not get back.

---

### Step 2 — Memory of what happened

```bash
python modules/10-memory/02_episodic_memory.py
```

Eight past incidents from `data/past_incidents.jsonl` — symptom, cause,
resolution, how long it took, who decided — embedded into their own collection.

Then a new alert arrives:

> payment-service error rate still elevated 40 seconds after we restarted it

**Without memory:**

> Given that the error rate is still high 40 seconds after a restart, we should
> investigate potential issues such as configuration errors or underlying
> service dependencies. Additionally, consider implementing more robust
> monitoring…

**With memory:**

> Based on **INC-2103**, it seems likely that this issue might be related to a
> cache warm-up period after the restart… monitor closely…

The second answer knows something the first cannot: *we have seen this, it was
warm-up, and someone nearly restarted again and doubled the outage.* That is not
in the runbook and not in the model. It happened to us and we wrote it down.

> **Instructor:** this is the memory type teams most often lack. Everyone builds
> RAG over documentation. Far fewer store what actually happened, which is where
> the expensive lessons are.

**Note the separate collection.** Past incidents live in `incident_memory`, not
in `runbook`. A runbook rule and a recollection should not compete for the same
retrieval slots — one is policy, the other is an anecdote.

---

## What we just built

Two memory mechanisms with measured costs, and a clear map of which of the four
kinds each problem needs.

---

## Live experiments (10 min)

**Make the summariser drop something.** Add a specific number to a middle turn
in `01_kinds_of_memory.py` and ask for it at the end. Watch whether it survives.

**Poison the memory.** Edit an incident in `past_incidents.jsonl` so its
resolution is wrong, re-run, and see the agent confidently repeat it. Memory has
no truth-checking.

**Blur the boundary.** Ingest the past incidents into the `runbook` collection
instead, then run module 5's comparison. Watch policy questions start returning
anecdotes.

---

## Homework

**Wire episodic memory into module 9.** Add a `recall` node before `propose`
that retrieves similar past incidents, and include them in the proposal prompt.

Then answer: **should a past incident outrank the runbook?** The runbook says
roll back rather than restart. If three past incidents say a restart worked
fine, what should the agent do — and who decides that?

---

## Checkpoint ✅

- [ ] You can name the four kinds of memory and where each lives
- [ ] You can explain why buffer memory's cost grows quadratically
- [ ] You have seen summary memory lose a detail
- [ ] You have seen the agent cite a past incident id
- [ ] You can say why past incidents are in a separate collection

---

## Discussion questions

**1. Why not summarise everything, always?**

<details><summary>Answer</summary>

Because summarisation is lossy in ways you do not control and cannot predict.

The model decides what matters, and it is systematically wrong about specifics:
exact numbers, rare identifiers, negative results ("we already ruled out X").
Negatives are the worst — a summary that drops "the card processor was fine"
leads the agent to re-check it, or worse, to blame it.

It also compounds. Summarising a summary drifts further each time, and there is
no signal that it happened.

The usual production shape is a hybrid: keep the last N turns verbatim, summarise
what falls off the back, and keep anything you truly cannot lose out of the
transcript entirely — in state or in a store you query.

</details>

**2. Episodic memory is just RAG. Why give it a different name?**

<details><summary>Answer</summary>

Mechanically it is identical — same embeddings, same store, same retrieval. The
distinction is about **authority and lifecycle**, and both have practical
consequences.

A runbook is normative: it says what *should* happen, it is reviewed, and it is
current by construction. An incident record is descriptive: it says what *did*
happen once, nobody reviews it again, and it ages badly. Treating them as one
corpus means a two-year-old anecdote can outrank current policy in a prompt.

Hence separate collections, dates in the metadata, and a prompt that cites — so
a human can see which kind of thing the answer rests on.

</details>

**3. What memory does the module 9 pipeline actually need?**

<details><summary>Answer</summary>

Less than instinct suggests, and it is worth being deliberate.

It needs **no conversation memory**: each alert is independent and the tool loop
already keeps its own transcript for its own duration.

It needs **checkpoint memory**, which module 8 gave it — an approval pending
overnight must remember everything.

It would benefit most from **episodic memory**: "we have seen this alert three
times this month and rolled back every time" is genuinely valuable, and it is
the homework.

It should probably *not* have long-lived cross-alert conversation memory. An
agent that remembers what it concluded about last Tuesday's incident will
anchor on it, and anchoring on a superficially similar incident is precisely how
experienced engineers get things wrong too.

</details>

---

**Next →** [Bonus 2 — Context](11-context.md): buffer memory grows until it hits
a wall. What is that wall, and what do you throw away when you reach it?
