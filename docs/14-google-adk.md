# Bonus 5 — Google ADK: the same agent, a third way

> **The question this module answers:** we built an agent by hand and again in
> LangGraph. What does a third framework change, and what does it not?

**Time:** ~40 min · **Code:** `modules/14-google-adk/` · **You need:** modules 3 and 13

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | The same agent, a third way | 18 min | Module 3's investigation, rebuilt in ADK |
| 2 | Delegation as a field | 22 min | `sub_agents`, and who really decides the route |

---

## Where we are

Module 3 built the agent loop three times over: a `while` loop, then a LangGraph
graph, then the same graph with tools behind MCP. Each rebuild made the
underlying pattern clearer.

This is a fourth build in a framework from a different vendor, and the point is
comparative: what is genuinely common to agent frameworks, and what is one
company's opinion.

## The problem

There are a lot of agent frameworks, they all demo well, and choosing between
them is usually done on the strength of a README. That is a bad way to pick a
dependency you will be operating in eighteen months.

The only reliable way to tell what a framework actually gives you is to build
something you have already built and diff the experience. We have the ideal
candidate: module 3's investigation, whose correct answer we know by heart.

So the questions here are comparative, not exploratory:

- What is *common* to every agent framework, and therefore not a reason to pick
  one?
- What does ADK genuinely provide that we assembled by hand?
- What does adopting it cost — in dependencies, in async, in worldview?

## What you'll build

- Module 3's investigation, rebuilt as a declarative ADK agent on the local model
- A multi-agent version: a coordinator that owns no tools and two specialists
  that do
- A measured look at where the work actually went, and how often the routing was
  wrong

---

## The local-model problem, and its solution

**Agent Development Kit** is Google's open-source agent framework. It defaults
to Gemini — an API key and a cloud call, both against this workshop's rules.

ADK ships a `LiteLlm` model adapter, which is why bonus 4 came first. Point it
at `ollama_chat/qwen2.5:7b` and the whole thing runs locally with no key:

```python
agent = LlmAgent(
    name="sre_assistant",
    model=LiteLlm(model="ollama_chat/qwen2.5:7b", api_base="http://localhost:11434"),
    instruction="You are an SRE assistant investigating a production alert…",
    tools=[get_service_status, get_recent_deploys, get_error_logs],
)
```

> **A dependency warning.** `google-adk[extensions]` pins
> `langgraph>=0.2.60,<0.4.8`, which is **incompatible** with the LangChain we
> use (it needs `langgraph>=1.2.5`). Installing the extras downgrades LangGraph
> and breaks modules 3, 8 and 9. Install `google-adk` and `litellm` separately,
> without the extra — verified: LangGraph, LangChain, FastAPI and Pydantic all
> stay put.

---

## Concepts in this module

### `LlmAgent`

ADK's unit of work: a declarative object carrying a model, an instruction, a set
of tools, and optionally other agents. You describe *what the agent is* rather
than wiring *what happens next*, which is the visible difference from a
LangGraph graph.

### `description` versus `instruction`

Two prompts with different audiences, and confusing them is the usual first
mistake. `instruction` is what the agent reads about its own job. `description`
is what **other agents** read when deciding whether to hand work over. A
specialist with a great instruction and a vague description never gets any work.

### Runner and session

The runner executes an agent and owns the conversation state. `InMemoryRunner`
keeps it in memory; ADK also ships database-backed session services — the same
concern LangGraph's checkpointer solved in module 8.

### `sub_agents` and `transfer_to_agent`

Listing agents in a parent's `sub_agents` field makes ADK generate a
`transfer_to_agent` tool on the parent. So delegation is not a routing table: it
compiles down to an ordinary tool call, and the *model* chooses the target. That
single fact explains most of what step 2 shows you.

---

## Build it

### Step 1 — The same agent, a third way

**Why:** the fastest way to see what a framework adds is to watch it do
something you have already watched a different framework do.

```bash
python modules/14-google-adk/01_adk_agent.py
```

**What you should see** — the same investigation module 3 produced:

```
→ calling get_service_status({'service': 'payment-service'})
← returned {'service': 'payment-service', 'status': 'degraded', …
→ calling get_recent_deploys({'service': 'payment-service'})
← returned {'sha': '9f2a41c', … 'perf: reduce settlement worker connection pool 50 -> 5'
→ calling get_error_logs({'limit': 5, 'service': 'payment-service'})
```

…and the same conclusion: the deploy that cut the pool from 50 to 5.

**Note there is no decorator on the tools.** ADK takes bare Python functions and
builds the schema from the signature and docstring — the same three inputs
LangChain's `@tool` and FastMCP's `@server.tool()` use. Three frameworks, one
convention, because the model needs the same three things regardless.

**What just happened:** you got module 3's answer from a framework that has
never heard of LangChain, using tool functions that are almost character-for-
character the same. The tools were portable; only the wiring changed.

---

### Step 2 — Delegation as a field

**Why:** everything in step 1 was ergonomics. `sub_agents` is the one place ADK
offers something we did not build by hand — so it deserves more than a bullet in
a feature table.

```bash
python modules/14-google-adk/02_sub_agents.py
```

The shape is a coordinator that owns no tools and two specialists that do:

```
incident_coordinator          decides who should handle this
  ├── triage_specialist       no tools; classifies severity from the text
  └── investigation_specialist the three read-only ops tools from module 3
```

Two prompts go in — "how severe is this?" and "why is it happening?" — which
should land in different places.

**What you should see:** the transfer itself, as an ordinary tool call.

```
⇢ incident_coordinator transfers to triage_specialist
⇢ triage_specialist transfers to investigation_specialist
  → investigation_specialist calls get_service_status({'service': 'payment-service'})
  ← returned {'service': 'payment-service', 'status': 'degraded', …
```

```
 prompt               transferred to                              tools   answered by
 severity question    nobody — coordinator answered                   0   incident_coordinator
 root cause question  triage_specialist → investigation_specialist    3   investigation_specialist
```

**Both rows are wrong, and that is the lesson.** The intended routing was
severity → `triage_specialist` with no tools, and root cause →
`investigation_specialist` directly. On `qwen2.5:7b` you will most likely get
neither.

**Row 1 — the coordinator answers a question it was told not to answer.** Its
instruction says it does not answer questions and has no tools of its own. It
answers anyway, because it *can*: a severity judgement needs no tools, and the
path of least resistance is to just reply.

This is module 7's lesson in a new costume. **An instruction is a request, not a
control.** If the coordinator must never answer, do not ask it nicely — check
the author of the final event and reject the run, or give it no way to reply.

**Row 2 — it routes to the wrong specialist, which then re-routes.** The
root-cause question lands on `triage_specialist`, which reads its own
instruction, concludes this is not its job, and transfers on to
`investigation_specialist`. In ADK a sub-agent also gets `transfer_to_agent`,
so it can hand work sideways.

The final answer is correct and it cost an extra model call to get there.
Self-correcting chains are simultaneously a real strength of multi-agent designs
and a real way to burn tokens.

**Now run it again. And again.** You will very likely get the same table every
time — which is the most important observation in this module. **Unreliable does
not mean random.** This is a *systematic* bias, and systematic is the more
dangerous kind: a noisy router announces itself the first time you test it; a
consistently wrong one passes your smoke test and fails on the input you never
tried.

**None of this is a bug in ADK.** `sub_agents` compiles to a `transfer_to_agent`
tool, so routing is a model decision carrying exactly the reliability module 3
measured for every other tool call.

> **A note on the instruction.** An earlier version of this script said
> "Transfer to triage_specialist". The model duly emitted a function call named
> `triage_specialist` — which does not exist, the only tool being
> `transfer_to_agent` — and ADK raised `ValueError: Tool 'triage_specialist' not
> found`. Agent names read like tool names, and a small model conflates them.
> The script now names the function and its argument explicitly, and catches
> that error rather than crashing, because it is a routing outcome worth seeing
> rather than a stack trace.

**The comparison that matters:**

| | how you express it | who decides the route |
|---|---|---|
| hand-written loop (module 3) | an if-statement you write | you, deterministically |
| LangGraph (modules 8/9) | a conditional edge on state | you, deterministically |
| ADK `sub_agents` | a field on the parent agent | the model, per request |

Neither column is better; they answer different questions. Module 9 routes on a
**policy** decision — settlement window, action whitelist — and that must never
be a model decision, which is exactly why it is a conditional edge in code.
Routing an open-ended question to whichever specialist is best placed to answer
it is the opposite case: there is no rule to write, so asking the model is the
only option on the table.

**What just happened:** you saw multi-agent delegation demystified. It is a tool
call with a nice API on top, which means it inherits both the flexibility and
the failure modes of tool calls.

> **Instructor:** the failure mode worth naming is using a model router for
> something an if-statement would have handled. It is the most common way
> multi-agent architectures become unreliable, and it is very easy to do,
> because declaring `sub_agents=[…]` is less typing than writing the rule.

---

## What is the same, and what is not

| | LangGraph (module 3) | ADK |
|---|---|---|
| tools | functions + docstrings | functions + docstrings |
| the loop | you wire the cycle | built into the agent |
| structure | a graph you compose | a declarative agent object |
| state/sessions | you add a checkpointer | built in, pluggable backends |
| multi-agent | you build it | `sub_agents` field |
| async | optional | throughout |

**The differences are ergonomic, not conceptual.** All four builds do the same
thing: offer tools, run what the model asks for, feed results back, repeat until
it stops asking.

Which is the reason to learn a second framework at all — it makes the pattern
visible, and the pattern is what transfers.

> **Instructor:** the line worth saying out loud is that the answers here are no
> better than module 3's, because it is the same model. Frameworks move the
> ergonomics, not the intelligence. Teams routinely evaluate frameworks when
> their problem is the model, the prompt, or the retrieval.

**What ADK adds that we assembled by hand:** sessions and state with pluggable
storage, artifacts as first-class objects, `sub_agents` for delegation,
callbacks around model and tool calls, and `adk web` — a local UI for stepping
through runs.

**What it costs:** a Google-shaped view of the world, async everywhere, a
`google-genai` dependency whether or not you use Gemini, the LangGraph version
conflict above, and — to get `adk web` at all — a project laid out the way ADK's
tooling expects. That last one is the ordinary shape of a framework adoption
cost: not code you write, but structure you accept. The live experiment below
makes it concrete.

---

## What we just built

The same investigation for a fourth time, plus a multi-agent version — and a
clear account of which parts of an agent framework are genuinely the framework's
and which are the model's.

---

## Live experiments (10 min)

**Give it the mutating tool.** Add a `restart_service` function to `tools=` and
send it the `injection_authority` alert from module 7. ADK will call it, exactly
as LangGraph did — no framework saves you from that, which is why module 9
removes the capability instead.

**Sabotage a description.** In `02_sub_agents.py`, change
`triage_specialist`'s `description` to just `"Triage."` and re-run. Watch the
routing get worse. Descriptions are prompts for other agents, not documentation.

**Make routing impossible.** Ask "what is going on?" — a question that fits both
specialists. See which one wins, and whether it is the same one every time.

**Try `adk web`.** From the repo root:

```bash
.venv/bin/adk web modules/14-google-adk/
```

Open the URL it prints, pick **sre_assistant** from the dropdown, and ask it
about the payment-service alert. You get the tool calls, their arguments and
their returns as an inspectable trace — the thing we printed by hand in module 3
and with `track()` everywhere since.

Then notice *why* it can see that agent. It is not reading `01_adk_agent.py`;
ADK's tooling ignores flat scripts entirely. It scans for subdirectories holding
an `agent.py` that exposes a module-level `root_agent`, which is why
`modules/14-google-adk/sre_assistant/` exists and why it is the one directory in
this workshop shaped like that. Read the file — it is the same agent as step 1,
and its docstring is about the constraint rather than the code.

The failure mode is worth seeing once, because it is silent: point `adk web` at
a folder with no conforming subdirectory and it starts normally and serves an
**empty dropdown**. No error, no warning, nothing in the log. If the list is
empty, the layout is wrong — not the agent.

---

## Homework

**Measure the router.** Run `02_sub_agents.py` ten times, recording where each
prompt was sent. Compute the hit rate for each prompt, the way module 6 would.

Then answer: **what hit rate would you need before shipping this?** And when the
answer is "higher than I got" — do you fix it with a better description, a bigger
model, or by deleting the coordinator and writing the if-statement?

---

## Checkpoint ✅

- [ ] You have run an ADK agent against a local model with no API key
- [ ] You can name two things ADK provides that we built by hand
- [ ] You can explain why `google-adk[extensions]` must not be installed here
- [ ] You can state what all four agent builds have in common
- [ ] You can explain what `sub_agents` compiles down to, and why that matters
- [ ] You have seen the router send a prompt to the wrong specialist

---

## Discussion questions

**1. Four builds of the same agent. Which would you ship?**

<details><summary>Answer</summary>

Whichever your team can operate — and that is not a dodge.

The **hand-written loop** is the most transparent and the least capable; it is
right for a single narrow task, and you will rebuild it as soon as you need
persistence.

**LangGraph** earns its place when you need the graph to be data: interruption,
checkpointing, resumption. Modules 8 and 9 could not have been built on the
`while` loop without reinventing most of it.

**ADK** is compelling if you are already on Google Cloud, want the built-in
session and artifact services, or need its multi-agent primitives.

The mistake is choosing a framework before you know which of those properties
you need. Build the loop first; it takes twenty lines and tells you what you
actually require.

</details>

**2. ADK, LangGraph and MCP all claim to help with tools. Do they overlap?**

<details><summary>Answer</summary>

They sit at different layers, and it is worth being precise because the
marketing blurs it.

**MCP** is a protocol — how a tool is published and discovered, across process
and language boundaries. It says nothing about agent loops.

**LangGraph and ADK** are orchestration — how the loop runs, where state lives,
how control flows.

You can use MCP tools from either, which is precisely the decoupling module 3
demonstrated: the server has never heard of LangChain, LangGraph or ADK.

The useful mental model: MCP is the tool *interface*, the framework is the
*runtime*. Choosing a framework does not lock your tools in, if the tools speak
MCP — which is a good reason to publish them that way even when you have only
one agent today.

</details>

**3. When is a multi-agent design the right answer, and when is it one agent
with extra steps?**

<details><summary>Answer</summary>

Splitting agents is worth it when the sub-problems differ in something the
framework can act on: **different tools** (so one agent cannot be handed a
mutating tool at all), **different models** (a cheap one for classification, an
expensive one for reasoning), **different context** (so one agent's enormous
transcript never enters the other's window), or **different owners** (separate
teams shipping separate prompts on separate schedules).

It is one agent with extra steps when the split is purely conceptual. Two
specialists sharing the same model, the same tools and the same context are one
agent, one extra model call per request, and one more thing to go wrong.

The tell is the routing decision. If you can write down the rule, write it down —
in code, deterministically. If you genuinely cannot, a model router is the only
option, and you should measure it like the unreliable component it is.

</details>

---

**Next →** [Bonus 6 — LLM gateway](15-llm-gateway.md): both frameworks now point
at a model through an adapter. What happens when twenty services do?
