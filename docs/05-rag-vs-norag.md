# Module 5 — RAG vs no-RAG: measuring what grounding buys

> **The question this module answers:** we built retrieval. Does it actually
> make the answers better, and what does it cost?

**Time:** ~30 min · **Code:** `modules/05-rag-vs-norag/` · **You need:** module 4 ingested

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | Same question, both ways | 18 min | Five questions, two answers each, one bill |
| 2 | Put it behind HTTP | 12 min | `/triage/compare` returns both paths |

---

## Where we are

Module 4 built a vector store and immediately found its failure mode: retrieval
always returns something, even when nothing is relevant.

Now we use it. The retrieved chunks go into the prompt, the model answers from
them, and — because every fact in our runbook is invented — we can tell at a
glance whether the answer came from the document or from the model's
imagination.

## The problem

"RAG improves answers" is an article of faith in most teams. It is also
measurable, and the measurement has two halves people usually skip:

- **Did the answer actually change?** Not "does it look better" — is it now
  citing a fact that only exists in your document?
- **What did it cost?** Every retrieved chunk is tokens on every call, forever.

And a third, harder one: **what happens when retrieval finds nothing?** A system
that answers confidently from irrelevant chunks is worse than one with no
retrieval at all.

## What you'll build

- A side-by-side comparison across five questions, with token counts
- A distance filter that drops irrelevant chunks before they reach the model
- A prompt that permits the model to say "Not covered in the runbook."
- A `/triage/compare` endpoint returning both answers, the chunks, and the bill

---

## Concepts in this module

### Grounding

Constraining the model to answer from supplied text rather than its own
parameters. It is a prompt instruction, not a mechanism — nothing forces
compliance, which is why we ask for citations and check them.

### The distance threshold

`rag.py` drops any chunk further than **0.45** away before building the prompt.
That number comes from module 4's measurements: answerable questions scored
0.29–0.34, an unanswerable one 0.51.

Be honest about what it is — hand-tuned on a handful of examples, one embedding
model, one small document. It is a starting point, and module 6 exists to find
out whether it is any good.

### Permission to fail

The single highest-value sentence in the RAG prompt:

> If the extracts do not contain the answer, say exactly: "Not covered in the
> runbook."

Without it a model will always produce *something*, assembling an answer from
whatever chunks arrived. Giving explicit permission to decline converts a silent
failure into a visible one.

### Citation

Asking the model to name the section it used. Two benefits: a human can check
it, and a bad retrieval becomes obvious in the output instead of hiding inside a
fluent paragraph.

---

## Build it

### Step 1 — Same question, both ways

**Why:** to replace opinion about RAG with a table.

```bash
python modules/05-rag-vs-norag/01_compare.py
```

Five questions run twice each. Four have answers that exist only in our runbook;
the fifth has no answer there at all.

**What you should see** — for a question the runbook covers:

```
can I restart payment-service at 15:00 IST?

╭─ without runbook ─────────────────────────────────────────╮
│ Restarting during peak hours is generally discouraged…    │  ← plausible, generic
╰───────────────────────────────────────────────────────────╯
╭─ with runbook ────────────────────────────────────────────╮
│ No, you cannot restart payment-service at 15:00 IST.      │
│ **The payment service must never be restarted between     │
│ 14:00 and 16:00 IST.** This is due to the daily           │
│ settlement window…                                        │
╰───────────────────────────────────────────────────────────╯
retrieved 4, kept 4 after the distance filter:
  keep 0.251  1. Before you touch anything: the settlement window
  keep 0.274  1. Before you touch anything: the settlement window
```

The ungrounded answer is not *wrong* exactly — it is generic SRE advice that is
not our policy. The grounded one names the window.

**And then the bill:**

```
question                                  no-RAG tok   RAG tok      ×   chunks
can I restart payment-service at 15:00 I…         95       601   6.3×        4
what should I check first for a payment-…         85       614   7.2×        4
what is the safe minimum settlement pool…         82       539   6.6×        4
who do I page for a suspected duplicate …         68       479   7.0×        3
how do I rotate the TLS certificate?              77        98   1.3×        0
```

**Six to seven times the tokens**, on every call, forever. The retrieved context
dominates completely — the question itself is rounding error. That is the trade
stated plainly: you are paying per answer for facts that would otherwise be
wrong.

> **Instructor:** this table is the slide to photograph. Most teams adopt RAG
> without ever quantifying the multiplier, then get surprised by the bill.

**Now the last row, which is the important one.** Nothing in the runbook covers
TLS certificates:

```
how do I rotate the TLS certificate?
  drop 0.510  3. Connection pool configuration changes
  drop 0.544  5. Actions and their blast radius
  drop 0.560  1. Before you touch anything: the settlement window
  drop 0.575  1. Before you touch anything: the settlement window

╭─ with runbook ──────────────╮
│ Not covered in the runbook. │
╰─────────────────────────────╯
```

Every hit dropped, and the model declined. **That took two things working
together** — the distance filter removing the junk, and the prompt granting
permission to decline. Remove either and you get a confident answer about
certificate rotation assembled from a connection-pool policy.

Notice it also cost almost nothing: 1.3× rather than 7×, because there was no
context to send.

**What just happened:** you measured the benefit, measured the cost, and saw the
"nothing relevant" path work.

---

### Step 2 — Put it behind HTTP

**Why:** so the comparison is something you can show someone rather than
describe.

```bash
python modules/05-rag-vs-norag/02_api.py
```

In another terminal, send the alert from module 2:

```bash
curl -s -X POST http://127.0.0.1:8000/triage/compare \
  -H 'Content-Type: application/json' \
  -d @data/sample_alerts/payment_error_spike.json | python3 -m json.tool
```

**What you should see:** one JSON response containing both answers, every
retrieved chunk with its distance and whether it survived the filter, per-path
token counts and latency, and a single `token_multiplier`.

On a sample run:

```
token_multiplier: 3.78

without runbook:  "The sudden increase in HTTP 5xx error rate suggests an issue
                   within the payment-service itself or its dependencies…"

with runbook:     "The likely cause could be `redis-sessions` pool exhaustion,
                   as this is the most common issue according to the runbook.
                   1. Check the Redis connection pool status using:
                      paycli pool status --service payment-service…"
```

The grounded answer produces our **first-check rule** and the **actual command
from our runbook**. The ungrounded one produces a well-written restatement of
the alert.

Both paths are asked the **identical question** — that is deliberate, and worth
checking in `alert_question()`. If the two prompts differed, the comparison
would measure prompt wording rather than the effect of retrieval.

**What just happened:** you have an endpoint that answers "is RAG worth it here?"
with data, per alert.

---

## What we just built

Evidence. Specifically: grounded answers cite invented facts they could not have
known, ungrounded answers are fluent and generic, grounding costs roughly 4–7×
tokens, and the "nothing relevant" path is handled rather than papered over.

---

## Live experiments (10 min)

**Remove the permission to fail.** Delete the *"If the extracts do not contain
the answer…"* sentence from `RAG_SYSTEM` in `01_compare.py` and re-run. Watch
the TLS question get an answer.

**Raise the threshold to 0.9.** In `rag.py`, set `DISTANCE_THRESHOLD = 0.9`, so
nothing is ever filtered. The TLS question now arrives with four irrelevant
chunks and explicit instructions to answer from them. This is the single most
common RAG bug in the wild.

**Change k.** Set `k=1` in the `retrieve()` call. Cheaper, and it starts missing
answers that lived in the second chunk. Then `k=8`: more reliable, and the
multiplier climbs past 10×.

---

## Homework

**Find a question where RAG makes the answer worse.** They exist: something the
model knows well generally, where a marginally-relevant runbook chunk drags it
off course. Add it to `QUESTIONS` and record both answers.

Come back able to answer: **how would you have caught that automatically?**
Eyeballing five questions does not scale to five hundred, which is exactly the
gap the next module fills.

---

## Checkpoint ✅

You're done when:

- [ ] You have run both paths and can name a fact only the grounded one produced
- [ ] You can state the token multiplier you measured
- [ ] You have seen the TLS question answered with "Not covered in the runbook."
- [ ] You can explain the two mechanisms that made that refusal possible
- [ ] You have `curl`ed `/triage/compare` and read both answers in the response

---

## Discussion questions

**1. RAG cost 6–7× the tokens. When is that not worth paying?**

<details><summary>Answer</summary>

When the model already knows the answer reliably. Retrieval buys you facts that
are **local, recent, or private** — your policy, your incident, your customer.
It buys nothing for "what does a 502 mean", and it actively hurts by filling
context with distraction.

The pattern worth internalising: route rather than retrieve-always. Cheap
classification first — does this question need our documents? — then retrieve
only when the answer is yes. Our TLS row shows the shape of the saving: 1.3×
instead of 7× when nothing is retrieved.

Also worth noting the cost is not only money. Every chunk is latency, and every
chunk is context the model has to read past to find what matters.

</details>

**2. The threshold is 0.45. How would you defend that number?**

<details><summary>Answer</summary>

Right now, you cannot. It was tuned by eye on five questions against one small
document with one embedding model, and it will drift as any of those change.

Defending it requires a labelled set — questions paired with the sections that
should be retrieved — and then measuring recall at various thresholds. Too low
and you drop good chunks (silent misses); too high and junk reaches the model
(silent hallucination). Both failures are invisible without measurement.

That is the whole argument for the next module. Until you have the labelled set,
every number in a RAG system is a guess someone made on a Tuesday.

</details>

**3. The model cited sections. Does that make the answer trustworthy?**

<details><summary>Answer</summary>

It makes it *checkable*, which is different and still valuable.

A citation proves nothing on its own — models can cite a real section and then
misstate what it says, or cite the section they were given while answering from
their own knowledge. What citation buys is that a human can verify in seconds
instead of re-deriving the answer.

Worth stating for later: citation is a **debugging** tool, not a safety control.
When the action is consequential, you do not want a checkable answer, you want a
human who checked. That distinction is the approval module.

</details>

**4. Retrieval and tools both feed the model context. When do you use which?**

<details><summary>Answer</summary>

Roughly: **tools for state, retrieval for knowledge.**

Tools answer "what is true right now" — error rates, deploys, logs. The answer
changes minute to minute, and it must be fetched fresh.

Retrieval answers "what do we know" — policies, procedures, past incidents.
Slow-changing, written down, too large to include wholesale.

The blurry middle is worth naming: a document that changes often (an on-call
rota, a status page) is better fetched as a tool than embedded, because a vector
store is a cache and caches go stale silently. If the answer must be current,
call something.

Our incident responder needs both, and module 9 wires them together.

</details>

---

**Next →** Module 6 — evaluation: everything so far has been judged by reading
the output and nodding. That does not scale past a demo, and it cannot tell you
whether a change made things better. Time to measure.
