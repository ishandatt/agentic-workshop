# Bonus 2 — Context: what to throw away

> **The question this module answers:** the conversation grows every turn. What
> happens when it stops fitting, and what do you drop?

**Time:** ~35 min · **Code:** `modules/11-context/` · **You need:** module 3 finished

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | Watch the window fill | 18 min | Quadratic growth, and where the tokens go |
| 2 | Four ways to continue | 17 min | Trim, and lose the wrong thing |

---

## Where we are

Bonus 1 ended with buffer memory: perfect recall, cost growing every turn. This
is where that ends — a hard limit, hit without warning.

## The problem

Every model has a fixed **context window**. Cross it and the call does not
degrade gracefully: it fails, or the runtime silently drops the front of your
prompt, which is worse because it looks like the model got stupid.

Agent loops reach that limit unusually fast, for a reason obvious in hindsight:
each turn resends the entire conversation, so cumulative tokens grow with the
*square* of the turn count.

---

## Concepts in this module

### Context window

The maximum tokens a model can consider at once. Two numbers matter and the
smaller wins:

- what the **model** supports (`qwen2.5:7b`: 32,768)
- what the **runtime loaded it with** (`num_ctx`, often far lower)

A model advertising 32k loaded with `num_ctx=4096` has a 4,096-token window, and
nothing warns you.

### Quadratic growth

Turn *n* resends everything from turns 1..*n*. Total tokens across a
conversation therefore grow roughly with the square of its length.

### Trimming strategies

- **sliding window** — keep the last N messages
- **first + last** — keep the opening *and* the recent turns
- **summarise the middle** — compress what falls out instead of deleting it

---

## Build it

### Step 1 — Watch the window fill

```bash
python modules/11-context/01_the_window.py
```

```
qwen2.5:7b
  declared context length: 32768 tokens
  runtime parameters: (defaults)
```

Then a real tool loop:

```
 turn   input tok   output tok   cumulative   messages
    1         455          105          560          3
    2         729          201         1490          7
    3        1073          233         2796          9
    4        1526          336         4658         11
    5        1886          123         6667         13
    6        2076          142         8885         15
    7        2438          419        11742         17

Input tokens went 455 → 2438 (5.4×) across 7 turns.
```

**Output stayed flat; input quintupled.** None of that growth is new
information — it is the same conversation, resent, and charged for again.

Then where the tokens actually are:

```
 message type   approx tokens   share
 AI                      1344     67%
 Tool                     602     30%
 System                    43      2%
 Human                     14      1%
```

**Not what most people guess.** The usual suspect is tool output; here the
model's *own* messages are 67%, because it restates its reasoning every turn and
all of it is resent forever. Capping tool output would not touch that.

> **Instructor:** worth connecting to module 9's second bug. Tool output *is*
> the easiest thing to cap — and capping it carelessly at 200 characters
> deleted the commit message the whole diagnosis depended on. Trim at the tool,
> where you know what the fields mean.

---

### Step 2 — Four ways to continue

```bash
python modules/11-context/02_keeping_it_going.py
```

A conversation with a fact planted in the **first** message (the on-call
engineer and the remaining error budget), ten turns of plausible filler, then a
question about that fact.

```
 strategy                     messages   input tok   recovered   answer
 keep everything                    23         321   both        We have 12 minutes…
 sliding window (last 6)             7         105   neither     To address your questions…
 first 2 + last 6                    9         168   both        You have 12 minutes…
 first 2 + summary + last 6         10         459   both        We have 12 minutes…
```

**The sliding window loses both facts.** It is the default in most chat
frameworks, it is one line of code, and it deletes the beginning of the
conversation — which is exactly where people put the things that matter: the
incident id, who is on call, what has already been ruled out.

**Keeping the first two messages fixes it for 63 extra tokens** — and still costs
half of keeping everything.

**Summarising the middle also works and costs the most here** (459 tokens,
including the summarisation call). Its value is not at this length; it is at the
length where "keep everything" is not on the menu.

---

## What we just built

A measured understanding of where context goes, and four strategies with their
costs and failure modes — including the one most frameworks default to and the
fact it silently drops your setup.

---

## Live experiments (10 min)

**Shrink the window.** Set `num_ctx` low (e.g. 512) via an Ollama `options`
parameter and re-run step 1. Watch what happens when the prompt exceeds it —
note whether you get an error or quietly worse answers.

**Move the planted fact.** Put the error budget in a middle turn instead of the
first. Now `first + last` fails too, and only summarisation recovers it.

**Cap the tool output.** Reduce `get_error_logs`' `limit` to 1 and re-run step 1.
See the Tool share shrink — and consider what you lost.

---

## Homework

**Add trimming to module 3's loop.** Implement `first 2 + last N` in
`02_the_loop.py` and run an investigation long enough to trigger it.

Then answer: **what would you pin?** The system prompt obviously. What else must
never be dropped, and how would you notice if it had been?

---

## Checkpoint ✅

- [ ] You know the difference between a model's context length and `num_ctx`
- [ ] You can explain why agent-loop cost grows quadratically
- [ ] You have seen which message type actually dominates your transcript
- [ ] You have watched a sliding window silently drop the setup
- [ ] You can name what should never live in a trimmable transcript

---

## Discussion questions

**1. Long-context models are getting cheap. Does this stop mattering?**

<details><summary>Answer</summary>

It changes when you hit the wall, not whether the wall exists — and two problems
survive a bigger window.

**Cost is linear in context and you pay it every turn.** A million-token window
does not make a million-token prompt affordable at ten turns.

**Models reason worse with more irrelevant material.** Performance on
information buried mid-context degrades measurably. A big window lets you
include everything; including everything is often the wrong call.

The useful reframing: context is not storage, it is **attention**. The question
is never "does it fit" but "does everything in here earn its place".

</details>

**2. Sliding windows are the default everywhere. Why, if they are this bad?**

<details><summary>Answer</summary>

Because they are one line, they never fail loudly, and for chat assistants they
are usually fine — recent turns genuinely are the relevant ones when someone is
asking follow-up questions.

They fail for *task* agents, where the setup is stated once at the start and
matters until the end. Incident response is exactly that shape: the incident id,
the affected service, and what was ruled out are all stated early and needed
throughout.

The general rule: if your first message is special, do not use a strategy that
treats it as ordinary.

</details>

**3. What should never go in a trimmable transcript?**

<details><summary>Answer</summary>

Anything the system's correctness depends on.

In module 9 that is the alert, the triage, the proposal and the policy decision —
and none of them live in a message list. They are fields in the graph **state**,
which is not trimmed, is persisted, and is what the approval request is built
from.

That is the pattern worth taking away. A conversation transcript is a scratchpad
for the model's reasoning; it is a bad place to store facts. Put facts in state,
in a database, or behind retrieval, and let the transcript be lossy — because it
will be, whether you plan for it or not.

</details>

---

**Next →** [Bonus 3 — Connections](12-connections.md): three handshakes this
workshop has been paying for over and over, measured.
