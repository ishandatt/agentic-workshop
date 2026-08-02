# Module 1 — Foundations: your local LLM stack

> **The question this module answers:** what *is* an LLM call, really — and
> what does a framework add on top of it?

**Time:** ~45 min · **Code:** `modules/01-foundations/` · **You need:** a working laptop and the repo cloned

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | Get the stack running | 10 min | `./setup.sh`, then seven green checks |
| 2 | An LLM API is just HTTP | 10 min | Call the model with no framework at all |
| 3 | The same call via LangChain | 15 min | See exactly what the framework adds |
| 4 | Experiments | 10 min | Temperature and cost, hands on |

---

## Where we are

We're building an **incident-responder agent**: an alert arrives, the agent
works out what's wrong, looks things up, proposes a fix, and waits for a human
to approve it.

Everything in that sentence is built on one primitive — asking a language model
a question and getting text back. Today we do only that, but we do it with our
eyes open: what goes over the wire, what it costs, and what a framework is
actually for. Get this wrong and every later layer is built on sand.

## The problem

Most people meet LLMs through a chat window or an SDK. Both hide the mechanics,
and the mechanics are where the engineering lives:

- What exactly did we send, and what came back?
- How many **tokens** did that cost, and how would we know?
- Frameworks like LangChain are everywhere — but what do they *do*? Are they
  worth the dependency?

You can't reason about latency, cost, or reliability until you can answer
those. So we'll answer them by hand first, then let a framework do it.

## What you'll build

- A verified local stack: model server, vector database, Python environment
- A working LLM call using nothing but an HTTP client
- The same call through LangChain, with the differences visible side by side
- A metrics helper that reports tokens, latency, and cost on every call

---

## Concepts in this module

New vocabulary, explained before you meet it in code.

### Ollama

A program that runs language models **on your own machine** and exposes them
over plain HTTP at `localhost:11434`. It plays the role that OpenAI's or
Anthropic's servers would play, minus the network, the API key, and the bill.

We run it natively rather than in a container because containers on macOS run
inside a Linux VM with no access to Apple's GPU. Native Ollama uses Metal
acceleration and is 5–10x faster.

### Model

The actual neural network weights. Ours is **`qwen2.5:7b`** — 7 billion
parameters, about 4.7 GB on disk. Frontier cloud models are hundreds of times
larger, which is precisely why this one is useful for a workshop: it fails in
visible, instructive ways that big models paper over.

We also install **`nomic-embed-text`**, a different kind of model that turns
text into a list of numbers rather than into a reply. More on that when we need
it.

### Token

Models don't read characters or words; they read **tokens** — roughly 4
characters of English, so about ¾ of a word. Tokens matter because they are
simultaneously the unit of:

- **cost** — commercial APIs bill per token, input and output priced separately
- **latency** — generation time scales with output tokens
- **capacity** — every model has a fixed **context window** (the maximum
  tokens it can consider at once)

Every provider reports token counts on every response. That number is the raw
material for all cost and performance tracking.

### Temperature

A dial on randomness, usually 0.0–2.0. At `0.0` the model picks the most likely
next token every time and is nearly deterministic. Higher values sample more
freely — better for creative writing, worse for anything you need to behave the
same way twice. Agents want low temperature.

### LangChain

A framework for building LLM applications. Here's the honest version of what it
does, since "framework" explains nothing:

An LLM API is simple — text in, text out. Real applications need more:
different providers with incompatible request shapes, prompts assembled from
templates, outputs parsed into structured data, several calls chained together,
tools the model can invoke. Everyone building this writes the same adapters.
LangChain is that shared layer.

Concretely, three things you'll see today:

1. **A uniform interface.** Every component — model, prompt, parser — has an
   `.invoke()` method. So they're interchangeable, and anything built from them
   is itself invokable.
2. **Message objects** (`SystemMessage`, `HumanMessage`, `AIMessage`) instead
   of hand-built dictionaries. The wire format is identical; the difference is
   that your editor can check these, and they carry richer payloads cleanly.
3. **Normalised token accounting.** Every provider reports usage differently.
   LangChain flattens them all into `usage_metadata`, so cost code written once
   works everywhere.

> **Instructor:** be even-handed here. LangChain is a dependency with real
> churn, and plenty of good systems skip it. The argument for it is that we
> will need tool calling, structured output, and stateful loops — and hand-
> rolling all of that per provider is where the time actually goes.

### LCEL and the `|` operator

Python lets a class define what an operator means for its objects. LangChain
uses this to make `|` mean "pipe the left into the right":

```python
chain = prompt | llm
```

That reads like a shell pipeline and behaves like one. `chain` is now itself
invokable, which is the point — small pieces compose into bigger pieces that
are still substitutable for the small ones.

### Postgres + pgvector

A normal PostgreSQL database with an extension that adds a `vector` column type
and similarity search. It's part of the stack from day one so that setup
problems surface now, on a calm day, rather than mid-exercise. We verify it
works today and start storing things in it once the agent needs to look facts
up in documents.

---

## Build it

### Step 1 — Get the stack running

**Why:** nothing else in the workshop works until these three pieces are alive.

```bash
./setup.sh
source .venv/bin/activate
```

The script installs native Ollama via Homebrew, pulls both models, creates and
starts the Podman machine, launches Postgres, builds the Python virtualenv, and
copies `.env.example` to `.env`. It's safe to re-run.

**What you should see:** seven steps, each ending in a green tick.

> **Instructor:** the model pull is ~5 GB. If the room's wifi is suffering,
> this is the moment to say so and let people start it while you talk through
> the concepts above.

The database is the only container in this project. `scripts/db.sh` is your
handle on it:

```bash
./scripts/db.sh status    # is the VM up? is Postgres accepting connections?
./scripts/db.sh start     # start it (brings the VM up too if needed)
./scripts/db.sh stop      # stop it, keep the data
./scripts/db.sh reset     # destroy the data as well — a clean slate
./scripts/db.sh psql      # a psql shell inside the container
./scripts/db.sh logs      # follow Postgres logs
```

> **Instructor:** there is no compose file. One container doesn't need an
> orchestrator, and every extra tool is one more thing to fail on the day.
> Open `scripts/db.sh` — the entire database setup is a single `podman run`
> you can read top to bottom.

After a reboot the container does not come back on its own. Run
`./scripts/db.sh start` and carry on.

**Verify:**

```bash
python scripts/check_setup.py
```

**What you should see:** seven green checks — Ollama reachable, both models
present, chat responds, embeddings respond, Postgres reachable, pgvector
enabled. All seven must pass before you continue.

> This script lives in `scripts/`, not in a module folder, because it checks
> the environment *everything* depends on. Re-run it any time something feels
> broken.

---

### Step 2 — An LLM API is just HTTP

**Why:** before adding any abstraction, see the thing being abstracted. This is
the whole "AI call", with nothing in between.

```bash
python modules/01-foundations/02_raw_ollama.py
```

**What you should see:** a two-sentence answer, then the raw response fields,
then a metrics line.

Now open `02_raw_ollama.py` — it's short and heavily commented. Three things to
notice:

**1. The entire request is one POST with a dictionary.**

```python
json={
    "model": CHAT_MODEL,
    "messages": [{"role": "user", "content": PROMPT}],
    "stream": False,
}
```

That `role`/`content` shape is near-universal. Swap the URL and you're talking
to a different provider.

**2. The response carries token counts.**

`prompt_eval_count` is input tokens, `eval_count` is output tokens, and the
durations are in nanoseconds. Every provider reports some version of this —
**it is the raw material for all cost tracking.**

**3. We wrap the call in a timer.**

```python
with track("raw-http-chat") as m:
    ...
    m.record_raw(input_tokens=..., output_tokens=...)
```

`common/metrics.py` times the block, stores the token counts, and converts them
into a **reference cost** — what this exact call *would* have cost on a paid
cloud API at the prices in your `.env`. Local inference is free; the habit of
watching the number is not free, and it's the habit we want.

**What just happened:** you made an LLM call with no framework, and you can
account for it precisely. Everything from here is convenience layered on this.

---

### Step 3 — The same call through LangChain

**Why:** now that you know what the raw call looks like, the framework's value
is measurable rather than assumed.

```bash
python modules/01-foundations/03_langchain_hello.py
```

**What you should see:** three blocks — a plain answer, a templated answer, then
text streaming in token by token — followed by a cumulative metrics table.

The script demonstrates three things. Follow along in the code:

**1. Typed messages.**

```python
llm.invoke([
    SystemMessage("You are a concise SRE assistant."),
    HumanMessage("In one sentence, what does 'error budget' mean?"),
])
```

Same wire format as the dictionaries in step 2. The gain is that these are real
objects: autocompleted, type-checked, and able to carry structured payloads
without you hand-assembling nested dicts.

**2. Prompt templates and LCEL.**

```python
triage_prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a concise SRE assistant for the {team} team."),
    ("human", "In one sentence, explain the term: {term}"),
])
chain = triage_prompt | llm
chain.invoke({"team": "payments", "term": "circuit breaker"})
```

The template has named holes; `.invoke()` fills them. Same reasoning as
prepared statements over string-concatenated SQL: the structure is fixed and
reusable, only the values vary.

The `|` builds a chain, and `chain.invoke()` works exactly like `llm.invoke()`.
That interchangeability is the design.

**3. Streaming.**

```python
for chunk in llm.stream("Count from 1 to 5 ..."):
    console.print(chunk.content, end="")
```

Tokens print as they're generated. Total latency is unchanged — *perceived*
latency is transformed. It's why every chat UI streams.

Note where the token counts come from: only the **final chunk** carries usage
data. Until the model stops, nobody knows how long the answer was.

**And the payoff:**

```python
with track("hello-invoke") as m:
    response = llm.invoke([...])
    m.record(response)      # reads response.usage_metadata
```

`m.record()` works for *any* LangChain chat model. Swap `ChatOllama` for
`ChatAnthropic` and the metrics code doesn't change a character. That's the
abstraction earning its keep — and it's the concrete answer to "what does the
framework buy me".

**What just happened:** you wrote the same call twice, once bare and once
framed, and you can now state the difference in one sentence instead of taking
it on faith.

---

## What we just built

A local stack you control end to end, a call you can account for down to the
token, and a metrics helper that will follow every LLM call for the rest of the
build. You also have a working definition of what a framework is for — which
means you'll be able to tell, later, when one is earning its place and when
it's just in the way.

---

## Live experiments (10 min)

Pick at least one and report back to the room.

**Temperature.** In `03_langchain_hello.py`, set `temperature=1.5` and run the
same prompt three times. Then set `0.0` and run three more. What changed? Why
would an agent that calls tools want low temperature?

**Prompt length.** Edit `PROMPT` in `02_raw_ollama.py` to something much
longer, re-run, and watch input tokens move. Then ask for a much longer answer
and watch output tokens move instead.

**Cost intuition.** In `.env`, replace the reference prices with a frontier
model's real pricing. Re-run either script and look at the TOTAL row. Now
multiply by 10,000 alerts a day.

---

## Homework

**Swap the model.** Change `CHAT_MODEL` to `llama3.1:8b` in `.env`, then:

```bash
./setup.sh          # pulls whatever .env names
python modules/01-foundations/03_langchain_hello.py
```

Compare tokens/sec and answer style against `qwen2.5:7b`. Note that `.env` is
the *only* place model choice lives — the setup script and every Python file
both read it, so they can't drift apart.

Come back with an opinion on which model you'd pick and why. "It felt better"
is not an answer; point at the metrics table.

---

## Checkpoint ✅

You're done when:

- [ ] `python scripts/check_setup.py` is fully green
- [ ] You've run both scripts and can explain input vs output tokens
- [ ] You can say in one sentence what LangChain adds over raw HTTP
- [ ] You know where the reference cost comes from and how to change it

---

## Discussion questions

**1. Ollama is free. Why track cost at all?**

<details><summary>Answer</summary>

Because free local inference is the exception, not the destination. The moment
this pipeline runs on a hosted model, every call has a price — and by then the
architecture is fixed and the token count is baked in.

Tracking now builds the instinct to ask "how many calls does this design make,
and how big are the prompts?" *while those are still design decisions*. It also
surfaces non-cost problems: a step whose token count quietly doubles is usually
a bug, and latency scales with tokens whether or not you're paying.

</details>

**2. Where did the tokens come from in the streaming example, and when does
usage data become available?**

<details><summary>Answer</summary>

Only on the **final chunk**. Every earlier chunk carries text but no usage —
the totals can't exist until generation stops.

Practical consequences: you must retain the last chunk (which is why the code
collects them into a list), and you cannot enforce a token budget *mid-stream*
from usage data. If a user abandons a stream, you've spent tokens you may never
receive a report for.

</details>

**3. What are the tradeoffs of the LangChain abstraction? When would you not
want a framework between you and the API?**

<details><summary>Answer</summary>

**Costs.** A large dependency with a fast-moving API; breaking changes between
versions. Debugging gets harder — a failure could be your prompt, the adapter,
or the provider. Abstractions leak: provider-specific features arrive late or
awkwardly, and the uniform interface can hide that two "equivalent" models
behave very differently.

**Skip it when:** you use exactly one provider and one call shape; latency and
dependency count matter more than portability; or your needs are simple enough
that the adapter you'd write is smaller than the framework you'd learn. A
single call with a fixed prompt does not need a framework — step 2 is thirty
lines and has no dependencies beyond an HTTP client.

**Take it when:** you need tool calling, structured output, retrieval, or
stateful multi-step loops — and especially when you might change providers.
That's the machinery that's genuinely tedious to write and maintain per
provider.

The honest answer is that it's a bet on future complexity. We take the bet here
because this pipeline grows in exactly the directions the framework covers.

</details>

**4. Small models fail in ways big ones don't. Is that a problem for learning?**

<details><summary>Answer</summary>

It's the opposite — it's why a 7B model is the right teaching choice. Malformed
JSON, ignored instructions, and invented facts all appear early and often here.
Frontier models make the same mistakes, just rarely enough that developers stop
defending against them.

Every defence built against a small model's failures — schema validation,
retries, guardrails, human approval — is exactly the engineering a production
system needs regardless of model size. The small model just makes the need
impossible to ignore.

</details>

---

**Next →** [Module 2 — Alert ingestion & triage](02-triage.md): a real alert
arrives over HTTP, and we make the model return *structured data* instead of
prose — which is where small models start to fight back.
