# Bonus 7 — Fine-tuning: teaching form, not facts

> **The question this module answers:** when is training the model the right
> move, and what does it actually buy?

**Time:** ~45 min · **Code:** `modules/16-finetuning/` · **You need:** Apple Silicon, ~1GB download

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | Build the dataset | 15 min | The actual work |
| 2 | LoRA fine-tune | 10 min | ~2 minutes of GPU on a laptop |
| 3 | Measure | 20 min | Held-out, before and after |

---

## Where we are

Sixteen modules of making a model behave through prompting, schemas, retrieval,
validation, retries and guardrails. Fine-tuning is the remaining lever, and it is
the one teams reach for first — usually by mistake.

## The order of operations

Try these in order. Most problems are solved before you reach the last one:

1. **Prompt** — cheapest, fastest, and measured: module 2's one sentence moved
   confidence formatting from 0/6 to 6/6
2. **Structured output** — makes malformed responses impossible
3. **Retrieval** — for anything factual, local, or changing
4. **Tools** — for anything the model cannot know from text
5. **Validation and retry** — for the residue
6. **Fine-tuning** — for form and style, once the above is exhausted

Fine-tuning last, because it is the only one that produces an artefact you must
version, evaluate, store and roll back.

## What it is good at, and what it is not

| Good at | Bad at |
|---|---|
| output format and structure | teaching facts (they change; weights do not) |
| house style and tone | reasoning, at small scale |
| domain vocabulary | anything needing to be current |
| tool-call reliability | replacing retrieval |
| making a small model imitate a big one on one narrow task | general capability |

**Fine-tune for FORM. Retrieve for FACTS.** If you take one line from this
module, that is it.

---

## Build it

### Step 1 — Build the dataset

```bash
python modules/16-finetuning/01_build_dataset.py
```

**What we fine-tune for** is the thing modules 2 and 7 fought hardest: our house
format. Confidence as a decimal, and summaries prefixed `SEV: ` (module 8's
paging rule). Both are pure format, which is exactly fine-tuning's strength.

400 synthetic examples in chat JSONL, split 80/10/10. Plus any *approved* runs
harvested from `data/runs.jsonl` — because module 8's approval gate is quietly a
labelling machine: a human-endorsed proposal is a training example, and a
rejected one is a negative you must not train on.

> **Instructor:** this step is the module. The training command is one line; the
> dataset is the project. Ask the room how long it would take to write 400
> *real* examples for their own system — that number is the true cost of
> fine-tuning, and it is why most teams should not.

**The honest limitation, stated in the script:** synthetic data teaches form
reliably and judgement badly, because every hypothesis came from a template.

---

### Step 2 — LoRA fine-tune

```bash
python modules/16-finetuning/02_finetune.py
```

`Qwen2.5-0.5B-Instruct`, MLX, 300 iterations. Measured on an M-series laptop:

```
Iter 200: Val loss 0.180
Iter 300: Val loss 0.179
✔ Trained in 1.9 minutes.
Adapter size: 23.5 MB (against ~1000MB of base model)
```

**Why 0.5B and not our usual 7B:** it trains in minutes rather than hours, and a
small model is genuinely bad at our format to begin with — which makes the
before/after visible rather than marginal.

**Why LoRA and not full fine-tuning:** full training updates every weight and
needs optimiser state for all of them. LoRA freezes the model and trains small
low-rank adapters — here 23.5 MB against a 1 GB base. You can keep a dozen
adapters for a dozen tasks against one base model, version them like code, and
roll one back without touching anything else.

---

### Step 3 — Measure

```bash
python modules/16-finetuning/03_compare.py
```

Twelve held-out prompts, three checks, base versus fine-tuned:

```
 check                     base   fine-tuned
 parses as JSON           10/12        12/12
 confidence in 0.0-1.0     0/12        12/12
 summary starts 'SEV: '    0/12        12/12
```

**Look at the third row.** That rule appears nowhere in the prompt. The base
model cannot guess it and does not, 0/12. The fine-tuned model produces it
12/12, so the knowledge is in the 23 MB adapter. That is what fine-tuning is
for.

**Compare the cost of the two ways to get that rule.** Module 8 enforced it with
a validator and a retry, paying a whole extra model call whenever the model got
it wrong — measured at 190 → 408 input tokens per retry. Here it costs zero
extra tokens at inference, having cost two minutes of training once.

Side by side on the same prompt:

```
base:        { "action": "escalate to production team", "resolution": "Monitor further…
fine-tuned:  {"severity": "medium", "summary": "SEV: checkout-service p99 latency rose…",
              "hypothesis": "Most likely retention policy has not run on schedule.",
              "confidence": 0.66}
```

**And the honest part.** This model learned our *format* and did not become a
better SRE — its hypotheses are template noise, because that is what we trained
on. Nothing here would survive module 6's judge on content.

That is the general result, not an artefact of the small model.

---

## What we just built

A 23 MB artefact that makes a 0.5B model produce our house format perfectly, and
a clear-eyed view of what that did and did not buy.

---

## Live experiments

**Train on 40 examples instead of 400.** Change the count in `01_build_dataset.py`
and re-run all three steps. Find where format compliance starts to break — that
is your data-volume answer, for this task.

**Overfit deliberately.** Raise `--iters` to 2000. Watch validation loss stop
falling while training loss keeps dropping, and check whether the model starts
reproducing training hypotheses verbatim.

**Ask it something factual.** Ask the fine-tuned model when the settlement window
is. It was never trained on that and has no retrieval — watch it invent an
answer in perfect house format, which is the failure mode to fear.

---

## Homework

**Fine-tune for tool-call reliability instead of format.** Build a dataset of
(alert → correct tool call) pairs from module 3's traces, and measure whether a
0.5B model can learn to pick the right tool.

Come back able to answer: **would you ship a 0.5B fine-tune over a 7B general
model for this?** Consider latency, memory, cost, and what happens the first
time it meets an alert unlike anything in your training set.

---

## Checkpoint ✅

- [ ] You can state the order of levers, with fine-tuning last
- [ ] You can explain why we fine-tuned for format and not for facts
- [ ] You have a 23 MB adapter and know why it is not 1 GB
- [ ] You have measured a before/after on held-out data
- [ ] You can say what the fine-tuned model got *worse* at

---

## Discussion questions

**1. Fine-tuning removed a retry that was costing a full extra call. Why not do
this everywhere?**

<details><summary>Answer</summary>

Because you traded a runtime cost for a set of permanent ones.

You now own a model artefact: it must be versioned, stored, evaluated on every
change, deployed, and rolled back when wrong. Your base model updates and you
must retrain. Your format changes and you must retrain. The dataset needs an
owner.

Compare that with the alternative for this specific rule — one sentence in a
prompt, changeable in seconds by anyone, with no artefact at all. Module 2
measured a prompt sentence moving compliance from 0/6 to 6/6 for 27 tokens.

Fine-tuning wins when the behaviour is stable, high-volume and hard to prompt.
It loses when the behaviour changes, or when a prompt would have done — which is
most of the time, and is why it belongs last in the order of levers.

</details>

**2. Could we fine-tune the runbook into the model and drop RAG?**

<details><summary>Answer</summary>

You can, it will appear to work, and it is usually a mistake.

The model would learn the settlement window and recite it — while the actual
failure modes get worse. **Staleness:** the runbook changes and your weights do
not, so you serve last quarter's policy with total confidence. **No citations:**
retrieval can show which section an answer came from; weights cannot, so nobody
can check it. **No access control:** a fact in the weights is available to
everyone, forever, which is a serious problem for anything tenant-specific.
**Retraining cost** on every document change.

There is also a subtle failure: fine-tuning on facts teaches the *style* of
confident factual assertion, so the model becomes more fluent about things it
half-remembers.

The exception worth naming: fine-tuning on your domain's *vocabulary* and
question shapes — not the facts themselves — genuinely improves retrieval-based
systems, because the model gets better at using retrieved context.

</details>

**3. Where should the training data come from?**

<details><summary>Answer</summary>

Ours is synthetic, and the script says so, because a workshop cannot produce
hundreds of real examples. In production the ranking is roughly:

**Best: production traffic with human decisions attached.** Module 8's approval
gate produces exactly this — an approved proposal is an endorsed example, a
rejected one is a labelled negative. If you build an approval workflow, you have
built a labelling pipeline; most teams never notice.

**Good: expert-written examples.** Expensive, high quality, and the realistic
path for a new system with no traffic.

**Careful: a larger model's outputs (distillation).** Effective and it inherits
the teacher's mistakes, plus you should check the licence.

**Worst: synthetic data from templates**, like ours. Fine for format, actively
misleading for judgement — a model trained on our data has learned that
hypotheses are one of six sentences.

The uncomfortable rule: your fine-tune is a compression of your dataset, so a
dataset nobody reviewed produces a model nobody should trust.

</details>

---

**That is the end of the material** — modules 0–9 core, 10–16 bonus. If you want
one thing to take further: modules 4 and 6 together, retrieval quality and how
you would know, repay more effort than anything else here.
