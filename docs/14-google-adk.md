# Bonus 5 — Google ADK: the same agent, a third way

> **The question this module answers:** we built an agent by hand and again in
> LangGraph. What does a third framework change, and what does it not?

**Time:** ~30 min · **Code:** `modules/14-google-adk/` · **You need:** modules 3 and 13

---

## Where we are

Module 3 built the agent loop three times over: a `while` loop, then a LangGraph
graph, then the same graph with tools behind MCP. Each rebuild made the
underlying pattern clearer.

This is a fourth build in a framework from a different vendor, and the point is
comparative: what is genuinely common to agent frameworks, and what is one
company's opinion.

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

## Build it

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
`google-genai` dependency whether or not you use Gemini, and the LangGraph
version conflict above.

---

## Live experiments

**Give it the mutating tool.** Add a `restart_service` function to `tools=` and
send it the `injection_authority` alert from module 7. ADK will call it, exactly
as LangGraph did — no framework saves you from that, which is why module 9
removes the capability instead.

**Try `adk web`.** Run `adk web` in `modules/14-google-adk/` for a UI over your
agent. Useful for stepping through a run, and a reminder of what ADK gives you
that a hand-built loop does not.

**Add a sub-agent.** Give the agent a `sub_agents=[…]` triage specialist and see
how delegation is expressed compared with adding a node to a graph.

---

## Checkpoint ✅

- [ ] You have run an ADK agent against a local model with no API key
- [ ] You can name two things ADK provides that we built by hand
- [ ] You can explain why `google-adk[extensions]` must not be installed here
- [ ] You can state what all four agent builds have in common

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

---

**Next →** [Bonus 6 — LLM gateway](15-llm-gateway.md): both frameworks now point
at a model through an adapter. What happens when twenty services do?
