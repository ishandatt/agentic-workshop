# Module 3 — MCP tools: letting the agent look things up

> **The question this module answers:** the model has been guessing from one
> paragraph. How do we let it go and *look*?

**Time:** ~60 min · **Code:** `modules/03-mcp-tools/` · **You need:** module 2 finished

> **Before you start, refresh your dependencies:**
>
> ```bash
> source .venv/bin/activate
> pip install -r requirements.txt
> ```
>
> This module pins `mcp>=1.29,<2`. If you set up your environment before that
> pin existed you will have `mcp 2.0`, which removed the API `mcp_server.py`
> uses, and step 4 will fail. The scripts detect this and tell you — but it is
> one command to avoid entirely.

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | A tool call is structured output | 10 min | The model asks; nothing runs |
| 2 | Close the loop by hand | 15 min | An agent, in twenty lines of `while` |
| 3 | The same loop as a graph | 12 min | LangGraph, and what it buys |
| 4 | Move the tools out of the process | 13 min | MCP: one line changes |
| 5 | Blind vs grounded | 10 min | The same alert, both ways |

---

## Where we are

Module 2 got structured triage out of the model, and the results were sobering:
severities that barely separated a slow checkout page from 12% of payments
failing, and hypotheses like *"possibly backend resource constraints,
configuration issues, or internal service failures."*

Not because the model is stupid. Because it was given one paragraph and asked
what was wrong with a system it cannot see. Any engineer handed the same
paragraph would say the same thing.

Today we give it eyes.

## The problem

An on-call engineer does not diagnose from the alert text. They check whether
the alert is still firing, look at what shipped recently, and read the logs.
That is three lookups, and the model can do none of them — it can only produce
text.

So: how does a text generator *do* something? And once it can, who decides
which lookup to make, in what order, and when to stop?

## What you'll build

- A tool-calling model, and a clear view of what a "tool call" actually is
- An agent loop written by hand — the loop, not a framework's version of it
- The same loop as a LangGraph state graph
- An **MCP server**: your tools in a separate process, discovered at runtime
- A side-by-side comparison of the same alert triaged blind and triaged grounded

---

## Concepts in this module

### Tool calling

The model does not execute anything. Given a set of tool descriptions, it can
emit a small piece of structured data meaning *"call `get_service_status` with
`service='payment-service'`"* — and then it stops.

If module 2 is fresh, you already know this mechanism. It is constrained
generation again. The only new idea is that instead of one schema to fill in,
the model is offered several and picks one.

Everything else — running the function, feeding the result back, deciding
whether to go again — is code **you** write.

### Agent

An LLM in a loop that can call tools, observe results, and act again. Module 0
defined it that way; step 2 is where it stops being a slogan:

```python
while True:
    reply = llm.invoke(messages)
    if not reply.tool_calls:
        break
    for call in reply.tool_calls:
        messages.append(ToolMessage(run(call), tool_call_id=call["id"]))
```

That is an agent. Not a special model — a control flow around an ordinary one,
where the *branch taken* is decided at runtime by the model.

### ToolMessage

How a result gets back into the conversation. It carries a `tool_call_id`
matching the request, because a model can ask for several tools at once and
needs to know which answer belongs to which question.

### LangGraph

A library for building agent loops as **graphs**: state, nodes, and edges.
The mapping from step 2's `while` loop is exact:

| Hand-written loop | Graph |
|---|---|
| the `messages` list | the **state** |
| the body of the loop | **nodes** |
| `if not reply.tool_calls` | a **conditional edge** |
| `break` | an edge to `END` |
| going round again | an edge from `tools` back to `model` — **the cycle** |

The honest answer to "why bother, the `while` loop worked" is: it worked
*today*. Soon we will want to pause the loop mid-run and wait for a human,
resume it later, and persist its state. Those are things you bolt onto a
`while` loop badly, and they are what LangGraph exists for.

> **Instructor:** resist introducing LangGraph as "the way to build agents".
> Build the loop first, get it working, then show the graph as the same thing
> in a shape that can grow. People who have written the loop understand the
> graph; people who have only seen the graph think agents are magic.

### MCP (Model Context Protocol)

A small JSON-RPC protocol with two essential moves: *"what tools do you have?"*
and *"call this one with these arguments"*. Tools live in a **server**; your
agent is a **client**.

The value is not the wire format — it is the process boundary. A tool published
over MCP:

- can be written in any language
- can be used by any agent that speaks MCP, not just yours
- can be restarted, sandboxed, permissioned or rate-limited independently
- takes down only a subprocess when it crashes

**stdio transport** means the client starts the server as a subprocess and talks
to it over stdin/stdout. Two pipes. No ports, no network, no configuration —
which is why it works on locked-down laptops and in CI.

> **A version warning that will save you an afternoon.** `mcp` 2.0.0 was
> released on 2026-07-28 and is a breaking rewrite: `mcp.server.fastmcp` is
> gone, and response fields moved to snake_case. Most published examples — and
> `langchain-mcp-adapters`, which imports a symbol 2.0 removed — still target
> 1.x. `requirements.txt` pins `mcp>=1.29,<2` for exactly this reason. If you
> find a tutorial that does not match our code, check which major version it
> was written for.

---

## Build it

### Step 1 — A tool call is structured output

**Why:** the word "tool" implies the model reaches out and acts. It does not,
and the misunderstanding causes real design errors.

```bash
python modules/03-mcp-tools/01_tool_calling.py
```

**What you should see:** the tool descriptions the model receives, then a reply
whose `content` is empty and whose `tool_calls` holds one entry.

```
text content : ''
tool_calls   : [ { "name": "get_service_status",
                   "args": {"service": "payment-service"}, "id": "..." } ]
```

**Stop on what did not happen.** No function ran. `fake_infra` was not touched.
The model produced a *request* and halted.

Then the script looks the tool up and runs it — two lines that are entirely
ours:

```python
by_name = {t.name: t for t in TOOLS}
result = by_name[call["name"]].invoke(call["args"])
```

**What just happened:** you saw the whole of "tool calling". A model that emits
structured requests, and a dispatcher you wrote. The model still has not seen
the result.

> **Instructor:** worth reading a tool's docstring aloud from
> `local_tools.py`. Unlike the Pydantic descriptions in module 2 — which Ollama
> never showed the model — tool descriptions **are** sent, because the model
> cannot choose a tool it knows nothing about. They are load-bearing prompt
> text disguised as documentation.

---

### Step 2 — Close the loop by hand

**Why:** this is the module's centre. Everything after it is refactoring.

```bash
python modules/03-mcp-tools/02_the_loop.py
```

**What you should see:** the model working the problem, one turn per model call.

```
── Turn 1 ──  → calling get_service_status({"service": "payment-service"})
              ← returned {"status": "degraded", "error_rate_percent": 12.4, …}
── Turn 2 ──  → calling get_recent_deploys({"service": "payment-service"})
              ← returned {"sha": "9f2a41c", … "perf: reduce settlement worker
                          connection pool 50 -> 5"}
── Turn 3 ──  → calling get_error_logs({"service": "payment-service", "limit": 5})
              ← returned ["… TimeoutError: could not acquire connection from
                          pool (size=5, active=5, waiting=37)" …]
── Turn 4 ──  No tool calls — the model is done.
```

And it reaches the actual cause: the deploy that cut the pool from 50 to 5.

**Three things to draw out:**

**Nobody told it that order.** The prompt says "gather evidence"; the sequence
status → deploys → logs was chosen at runtime, and each result shaped the next
request. That is the whole of "agency".

**The loop is bounded.** `MAX_TURNS = 6`, for the same reason module 2's retry
was capped: a loop whose exit depends on a model's judgement may not exit.

**Count the calls.** One model call per turn. A four-turn investigation costs
four completions, and each carries the whole accumulated transcript — so later
turns are the expensive ones.

**What just happened:** you wrote an agent. Twenty lines, no framework.

---

### Step 3 — The same loop as a graph

**Why:** to see exactly what a framework adds, and what it doesn't.

```bash
python modules/03-mcp-tools/03_langgraph.py
```

**What you should see:** identical tool calls, identical conclusion, identical
cost. Only the structure changed.

```python
builder.add_edge(START, "model")
builder.add_conditional_edges("model", should_continue)   # tools, or END
builder.add_edge("tools", "model")                        # <- the cycle
graph = builder.compile()
```

That third line is the loop. `should_continue` is the `if`. `MessagesState` is
the messages list, with a **reducer** that appends what a node returns rather
than replacing the list — which is why `call_model` can return just
`{"messages": [reply]}`.

**What just happened:** the loop became *data* — a graph you can inspect,
extend with new nodes, and interrupt partway through. Nothing about today needs
that. The modules that add human approval do.

---

### Step 4 — Move the tools out of the process

**Why:** the headline. Tools that live inside your agent can only ever be used
by your agent.

```bash
python modules/03-mcp-tools/04_mcp_agent.py
```

**Diff this file against step 3.** The graph, the nodes, the prompt, the
streaming loop — all identical. One line differs:

```python
# 03:  from local_tools import TOOLS
# 04:  TOOLS = load_mcp_tools()
```

The tools now live in `mcp_server.py`, a separate program started as a
subprocess. It imports no LangChain, no LangGraph, and has never heard of an
LLM. Every tool call is serialised to JSON-RPC, written to a pipe, executed in
another interpreter, and sent back — and neither the model nor the graph can
tell.

**Read `mcp_bridge.py`.** It is about thirty lines and it removes all the magic:
ask the server what tools exist, get names, descriptions and JSON Schemas back,
wrap each in a `StructuredTool`. That is the entire adapter. Published packages
do this for you; writing it once means you know what they do.

**Then read `mcp_server.py`** and notice how little there is to it. The tool
name comes from the function name, the argument schema from the type hints, and
the description from the docstring — three things you would have written
anyway.

> **Instructor:** the point that lands is the diff. Show step 3 and step 4 side
> by side on screen and let the room find the one changed line. The tools moved
> to another process and the agent did not notice — that is what "decoupled"
> means, made concrete.

**One honest caveat**, called out in the bridge's docstring: our adapter opens a
fresh connection per tool call, roughly 350 ms of overhead, so that these
scripts stay ordinary synchronous Python. Real clients keep one session open
for the life of the agent.

---

### Step 5 — Blind vs grounded

**Why:** to measure what tools bought, and what they cost.

```bash
python modules/03-mcp-tools/05_triage_with_tools.py
```

The same alert module 2 used, run twice.

**Path A — no tools, one call:**

> The root cause appears to be an issue within the payment-service itself,
> evidenced by the significant increase in the HTTP 5xx error rate… The
> elevated queue depth in the settlement worker pool further indicates that the
> service is struggling to process requests efficiently.

**Path B — with tools, after three lookups:**

> The root cause appears to be a recent deployment that reduced the settlement
> worker connection pool size, as evidenced by the commit message *"perf:
> reduce settlement worker connection pool 50 -> 5"*. This change has led to
> connection pool exhaustion… The error logs confirm this by showing multiple
> instances of timeout errors.

**Path A is not badly written.** It is fluent, structured, confident — and it
restates the alert. It names no evidence because it has none. Path B names a
commit SHA and a log line.

**Now the cost.** Path A: one call, ~110 input tokens. Path B: multiple calls,
each carrying the whole growing transcript — several times the spend. Tools buy
grounding, and you pay per lookup.

**What just happened:** you closed the gap module 2 opened. The severities are
still imperfect, but the *hypothesis* is now anchored to something real.

---

## What we just built

An agent, in the strict sense: a model in a bounded loop, choosing tools at
runtime, observing results, and stopping when it has enough. Then the tools
moved out of the process entirely without the agent noticing.

You can also now say precisely what each layer does — the model picks, your
code dispatches, the graph sequences, and MCP decides where the tool lives.

---

## Live experiments (10 min)

**Break a tool description.** In `local_tools.py`, change
`get_recent_deploys`'s docstring to something useless like `"gets deploys"` and
re-run step 2. Does it still get called at the right moment? Tool descriptions
are prompt engineering.

**Take a tool away.** Remove `get_error_logs` from `TOOLS` and re-run. Does the
model notice it cannot confirm its hypothesis, or does it assert one anyway?

**Ask about a service that doesn't exist.** Change the alert to
`billing-service`. Our tools return `{"error": …, "known_services": [...]}`
rather than raising — watch whether the model reads that and corrects itself.

**Tempt it.** Change the prompt from "Do not call restart_service" to "Fix it."
Does it reach for the disruptive tool? Nothing in this module stops it.

---

## Homework

**Add a fifth tool.** Something like `get_dependency_health(service)` returning
the status of each dependency in `fake_infra.SERVICES[service]["dependencies"]`.
Add it to `mcp_server.py` **only** — not to `local_tools.py`.

Then run step 4 without touching a line of `04_mcp_agent.py`, and confirm the
agent discovers and uses it.

Come back able to answer: **at what moment did the agent learn the new tool
existed?** Name the function call.

---

## Checkpoint ✅

You're done when:

- [ ] You can explain why a tool call is just structured output
- [ ] You've written the agent loop by hand and can describe its exit condition
- [ ] You can map the `while` loop onto state, nodes, and the conditional edge
- [ ] Step 4 runs and you can point at the single line that differs from step 3
- [ ] You can say what MCP buys that a local function does not
- [ ] You've seen the blind and grounded answers side by side, and the cost gap

---

## Discussion questions

**1. The agent found the real cause. Is that impressive, or did we rig it?**

<details><summary>Answer</summary>

Partly rigged, and it is worth being honest about which part.

`fake_infra.py` is built so the evidence chain holds together: a deploy 25
minutes before the alert, a commit message naming connection pooling, and logs
showing pool exhaustion. Real estates are noisier — five deploys, none obviously
related, and logs full of unrelated errors.

What is *not* rigged is the behaviour: nothing told the model to check deploys
after status, or to read logs to confirm. It sequenced the investigation itself,
and each result changed the next request.

The honest summary: this demonstrates the mechanism works, not that a 7B model
is a good SRE. Test it on your own messy data before believing anything
stronger.

</details>

**2. What stops the agent calling `restart_service`?**

<details><summary>Answer</summary>

One sentence in the prompt. That is all.

Which should worry you. The tool is bound to the model exactly like the read-only
ones; nothing in MCP marks it as dangerous, nothing in LangGraph asks before
running it, and if the model emits that call, `ToolNode` executes it.

Try the experiment above and change the prompt to "Fix it."

The gap is structural, not a prompting failure: **prompts are requests, and this
one can restart production.** Closing it needs code — a whitelist of permitted
actions, or a hard stop before any mutating call. That is what later modules
build, and this is the moment the need becomes obvious.

</details>

**3. MCP adds a process boundary, serialisation and ~350 ms per call. When is
that not worth it?**

<details><summary>Answer</summary>

Often, honestly. If the tools are yours, in your language, deployed with your
agent, and used by nothing else, a plain function is simpler, faster and easier
to debug. Steps 1–3 work perfectly well.

MCP earns its cost when at least one is true:

- the tools are used by **more than one** agent or client
- they are written in a **different language**, or owned by a different team
- they need **isolation** — separate credentials, sandboxing, rate limits
- they come from **someone else** and you are not going to vendor their code

The pattern to avoid: adopting MCP for a single in-house tool used by a single
agent, and paying the protocol tax for a decoupling nobody needs.

</details>

**4. The agent read three tool results into its context. What breaks at scale?**

<details><summary>Answer</summary>

Context, cost, and attention — in that order.

Every result stays in the transcript for the rest of the run, and every
subsequent turn resends all of it. Watch the input-token column climb across
turns. Ask for 500 log lines instead of 5 and you will blow the context window
in one call, and pay for the privilege.

Subtler: models reason *worse* with more irrelevant material in context, not
better. That is why `get_error_logs` defaults to `limit=5`.

The fixes are all engineering, not prompting: cap results at the tool boundary,
summarise old turns, keep bulk data outside the context and pass a reference to
it, or retrieve only the relevant fragment — which is what the next module is
about.

</details>

**5. Nothing here can answer "is it safe to restart this service right now?"
Why not?**

<details><summary>Answer</summary>

Because that fact does not exist anywhere the agent can reach. It is not in the
alert, not in the metrics, not in the deploy log. It lives in a runbook, a
policy document, or somebody's head.

This is the boundary of what tools solve. Tools give access to **system state** —
what is true of the machines right now. They do nothing about **institutional
knowledge**: your escalation policy, your maintenance windows, the quirk in this
service everyone learned the hard way in March.

No amount of tool calling reaches that, and no base model was trained on your
internal documents. That gap is the next module.

</details>

---

**Next →** [Module 4 — RAG ingestion](04-rag-ingestion.md): the agent can see the system but knows
nothing about *your* organisation. We write a runbook full of facts no model
could have been trained on, and teach the agent to look them up.
