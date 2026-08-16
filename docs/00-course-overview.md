# Module 0 — Course overview

> **The question this module answers:** what are we building today, and why
> should you care about each piece?

**Time:** ~20 min · **Code:** none — this is the map before the territory

> **Instructor:** present the slide deck (`slides/`) first. This doc is the
> written companion attendees refer back to all day.

---

## Run sheet for the day

| # | Module | Time | The question it answers |
|---|---|---|---|
| 0 | Overview | 20 min | What are we building, and why this way? |
| 1 | [Foundations](01-foundations.md) | 45 min | What *is* an LLM call, and what does a framework add? |
| 2 | [Alert triage](02-triage.md) | 50 min | How do we get **structured data** out of a model that only emits text? |
| 3 | [MCP tools](03-mcp-tools.md) | 60 min | How does a model *do* things instead of just describing them? |
| 4 | [RAG ingestion](04-rag-ingestion.md) | 45 min | How do we teach it facts it was never trained on? |
| 5 | [RAG vs no-RAG](05-rag-vs-norag.md) | 30 min | Does retrieval actually help, and what does it cost? |
| 6 | [Evaluation](06-evaluation.md) | 45 min | How do we know it works — repeatably? |
| 7 | [Guardrails](07-guardrails.md) | 45 min | What happens when the input is hostile? |
| 8 | [Human approval](08-approval.md) | 45 min | How do we pause an automated system for a human? |
| 9 | [Full pipeline](09-full-pipeline.md) | 45 min | What does the whole thing cost and how do we watch it? |

**Bonus modules**, for afterwards or for people who finish early:

| # | Module | Time | The question it answers |
|---|---|---|---|
| 10 | [Memory](10-memory.md) | 35 min | "Give it memory" means four different things. Which? |
| 11 | [Context](11-context.md) | 35 min | The conversation stops fitting. What do you drop? |
| 12 | [Connections](12-connections.md) | 30 min | What do all these reconnections actually cost? |
| 13 | [LiteLLM](13-litellm.md) | 30 min | One interface for every model — when do you want it? |
| 14 | [Google ADK](14-google-adk.md) | 40 min | A third agent framework. What changes, what doesn't? |
| 15 | [LLM gateway](15-llm-gateway.md) | 40 min | Twenty services calling providers directly. Now what? |
| 16 | [Fine-tuning](16-finetuning.md) | 45 min | When is training the model the right move? |
| 17 | [n8n](17-n8n.md) | 40 min | A team builds this on a canvas instead. What survives? |

Bonuses 1–3 close threads the core workshop deliberately leaves hanging; 4–8
look outwards, at what changes when this stops being one pipeline on one laptop.
They are largely independent, with one chain: **13 → 14 → 15** build on each
other. Two carry extra prerequisites: 16 assumes Apple Silicon, and 17 needs a
second container.

Every module is a working checkpoint. Fall behind and you can jump into the
next `modules/NN-*` folder and keep going.

---

## The scenario

We're building an **incident-responder agent**.

It's 2 AM. The payment service starts throwing errors. An alert fires. Normally
an on-call engineer wakes up, reads the alert, checks dashboards, looks at
recent deploys, greps logs, finds the runbook, follows it, and fixes the thing.

We're going to automate the boring parts of that — carefully, with a human
still holding the switch on anything consequential.

A fake alert arrives via `curl`, and the pipeline:

1. **Input guardrails** validate the alert and screen it for prompt injection
2. An **agent** on a local model triages it: how bad, what's the hypothesis
3. The agent calls **MCP tools** — service status, recent deploys, error logs
4. It retrieves procedures from an internal **runbook** via **RAG**
5. **Output guardrails** confirm only whitelisted actions are proposed
6. A **human approves or rejects** the plan — the pipeline stops and waits
7. The remediation **executes** (simulated) and everything is logged
8. **Metrics** — tokens, latency, cost — are recorded at every step

## Why this scenario

- **It's realistic.** Alert → triage → gather context → plan → approve → act is
  how production incident automation genuinely works.
- **It exercises every building block** in one coherent story, so concepts
  arrive when you need them rather than as a list of features.
- **The runbook is fictional on purpose.** It contains invented internal rules
  — settlement windows, service quirks, escalation paths — that no model could
  have seen in training. That makes the difference between a grounded answer
  and a confident guess impossible to miss.

## The architecture

```
curl alert ──▶ Input guardrails ──▶ LangChain/LangGraph agent ──▶ Output guardrails
                                      │            │
                                      ▼            ▼
                                  MCP tools   RAG (pgvector)
                                                      │
                        Execute (simulated) ◀── Human approval
```

Keep this diagram in view. Each module lights up one box, and we'll come back
to it at the end to see the whole path.

---

## What an agent actually is

Worth settling before anything else, because the word is badly overloaded.

A **chatbot** takes input and returns output. One turn, one answer.

An **agent** is a model in a **loop**, with the ability to act. Each pass it
decides: do I have enough to answer, or do I need something first? If it needs
something, it calls a **tool**, reads the result, and goes round again.

```
        ┌──────────────────────────────┐
        ▼                              │
   [ model decides ] ──▶ [ call tool ] ┘
        │
        ▼
   [ final answer ]
```

Three things follow, and they shape the whole workshop:

- **It's non-deterministic.** The same input can take a different path. Testing
  it needs different tools than testing a function.
- **It takes real actions.** A tool call can restart a service. That's the
  entire reason guardrails and human approval exist.
- **It costs money per step.** A loop that runs five times costs five calls.
  This is why we track tokens from module 1 rather than bolting it on later.

> **Instructor:** this is the highest-value slide of the day. If people leave
> understanding only "loop + tools + consequences", the rest lands.

---

## Ground rules for the day

- **Type along.** Copy-paste is allowed, but typing builds memory.
- **Fell behind?** Every `modules/NN-*` folder is a complete working snapshot.
  Jump into the next one and continue.
- **Small models are dumb sometimes.** That's a feature. You'll see bad JSON,
  wrong tool calls, and confident nonsense — failure modes big cloud models
  hide well enough that developers forget to defend against them. Every defence
  we build is one a production system needs anyway.
- **Ask "what would this cost?"** at every step. The metrics helper prints a
  reference cost per call. Build the intuition.
- **Interrupt.** A confusing five minutes is much cheaper caught early.

---

## Glossary

Refer back to this all day. Each term also gets explained in the module where
you first meet it.

| Term | Meaning |
|---|---|
| **Token** | The unit models read and write, and the unit you pay for — roughly 4 characters of English |
| **Context window** | The maximum tokens a model can consider at once; everything must fit |
| **Temperature** | Randomness dial. Low = repeatable, high = creative |
| **Agent** | An LLM in a loop that can call tools, observe results, and act again — versus a chatbot that only replies |
| **Tool / function calling** | The model emits a structured request ("call `get_service_status` with `service=payments`") instead of prose |
| **MCP (Model Context Protocol)** | An open standard that puts tools behind a server so *any* agent can use them, decoupling tools from agents |
| **RAG (Retrieval-Augmented Generation)** | Fetch relevant documents and put them in the prompt, so the model answers from facts instead of memory |
| **Embedding** | A vector representing the meaning of a piece of text; similar meanings land near each other, which is what makes semantic search possible |
| **Chunking** | Splitting documents into retrieval-sized pieces. Size and strategy strongly affect retrieval quality |
| **Vector database** | Storage that finds records by *similarity* rather than exact match. Ours is Postgres + pgvector |
| **Structured output** | Forcing the model to answer in a fixed schema (JSON) instead of prose |
| **Guardrail** | Deterministic code constraining what goes into or comes out of the model — schemas, whitelists, injection filters |
| **Prompt injection** | Text in the *input* that tries to hijack the model's instructions |
| **Human-in-the-loop (HITL)** | Pausing an automated flow for approval before consequential actions |
| **Eval** | A repeatable test suite for model behaviour — the unit test of the agent world |
| **Hallucination** | A fluent, confident, wrong answer. The default failure mode of an ungrounded model |

---

## Discussion questions

Good openers before any code is written.

**1. Where in your own on-call process would you actually trust automation?**

<details><summary>Talking points</summary>

Usually the answer splits cleanly along a line worth naming early: people
trust automated **reading** (gathering context, summarising, correlating) long
before automated **writing** (restarting, scaling, rolling back).

That instinct is correct, and it's the architecture of this workshop. Retrieval
and triage run unattended; anything that mutates state stops for a human.

</details>

**2. What's the worst thing that could happen if this agent were fully
autonomous?**

<details><summary>Talking points</summary>

Restarting a healthy service during a critical window. Acting on a
misdiagnosis and making an incident worse. Following instructions embedded in
an attacker-controlled alert. Looping on a failing action and amplifying load.

Each of these maps to a specific defence we build: policy checks in code,
grounding in a runbook, injection screening, and approval gates. Naming the
failures first makes the defences feel earned rather than ceremonial.

</details>

**3. Why run everything locally on a small model instead of calling a good one?**

<details><summary>Talking points</summary>

Practically: no keys, no spend, no rate limits, no dependency on conference
wifi, and nothing leaves the room.

Pedagogically, it's the stronger reason: a 7B model fails often enough that you
*must* build the defensive engineering to get anything working. On a frontier
model the same sloppy pipeline appears to work — right up until it doesn't, in
production, at scale.

Worth stating plainly: architecture transfers, prompts don't. Everything you
build today points at a bigger model by changing one line in `.env`.

</details>

---

**Next →** [Module 1 — Foundations](01-foundations.md): get the stack running,
then strip an LLM call down to the HTTP request it really is.
