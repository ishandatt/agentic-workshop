# Bonus 4 — LiteLLM: one interface for every model

> **The question this module answers:** LangChain normalises providers. So does
> LiteLLM. What is the difference, and when do you want the thinner one?

**Time:** ~20 min · **Code:** `modules/13-litellm/` · **You need:** module 1 finished

---

## Where we are

Every call so far went through `ChatOllama` — a LangChain class for one
provider. That is fine until you need a second provider, or until a library you
want only speaks OpenAI.

## The distinction worth holding

| | what it is | what it does |
|---|---|---|
| **LangChain** | application framework | prompts, chains, tools, agents, memory |
| **LiteLLM** | model adapter | one call signature, ~100 providers |

They are not competitors. LiteLLM is roughly the *bottom layer* of what
LangChain does, sold separately — and the next module uses it to point Google's
ADK at our local model.

---

## Build it

```bash
python modules/13-litellm/01_one_interface.py
```

**The whole trick is a provider prefix:**

```python
litellm.completion(model="ollama_chat/qwen2.5:7b", api_base="http://localhost:11434", ...)
#                        ^^^^^^^^^^^^ swap this for openai/… or anthropic/… and nothing else changes
```

The response comes back OpenAI-shaped whatever answered:

```
choices[0].message.content   A connection pool is a cache of reusable…
usage.prompt_tokens          31
usage.completion_tokens      27
```

Module 1 showed Ollama natively returns `prompt_eval_count` / `eval_count`.
Anthropic returns `input_tokens` / `output_tokens`. LiteLLM translated.

**And it knows what things cost.** The same tokens, priced across providers:

```
 model                                   input $/1M   output $/1M   this call
 gpt-4o-mini                                  $0.15         $0.60   $0.000026
 gpt-4o                                       $2.50        $10.00   $0.000438
 anthropic.claude-haiku-4-5-20251001-v1:0     $1.00         $5.00   $0.000211
```

That is the same idea as our `REF_PRICE_*` values in `.env`, with someone else
maintaining the numbers.

---

## When to use it

**Reach for it when:** you support more than one provider or expect to; you want
one place to change models; you need cost accounting across providers; or a
library you depend on only speaks OpenAI (very common).

**Don't when:** one provider, one call shape, no plans to change. It is then a
dependency that buys nothing.

---

## Live experiments

**Break the prefix.** Change `ollama_chat/` to `ollama/` and see what changes —
they are different translators with different tool-calling support.

**Price your own pipeline.** Take module 9's measured 5,061 tokens per incident
and run it through `get_model_info` for three hosted models. That is your real
migration cost.

---

## Checkpoint ✅

- [ ] You can state the difference between LiteLLM and LangChain in one sentence
- [ ] You have called your local model with an OpenAI-shaped call
- [ ] You can find the published price of a hosted model without leaving Python

---

## Discussion questions

**1. You now have two abstraction layers available. Is that one too many?**

<details><summary>Answer</summary>

Usually yes — pick the layer that matches the problem.

If you need agents, tools and orchestration, LangChain/LangGraph already
abstracts providers and adding LiteLLM underneath is a second indirection with
no new capability.

If you only need to call models and want provider independence, LiteLLM alone is
much less machinery than a framework.

The case for both is narrower than it looks: you are on LangChain, and you need
a provider LangChain lacks an integration for, or you want LiteLLM's cost table.
Otherwise it is indirection for its own sake.

</details>

**2. Provider-independent code sounds unambiguously good. What does it cost?**

<details><summary>Answer</summary>

The abstraction is real but leaky, and the leaks are where the value is.

Providers differ in ways a common interface flattens: tool-calling formats,
structured-output support (module 2 measured how differently Ollama behaves),
system-prompt handling, prompt caching, reasoning modes, safety filters. Code
written to the common denominator uses none of it.

There is also a correctness trap. Swapping providers behind an identical
interface changes behaviour — the same prompt produces different results, and
your evals (module 6) are the only thing that will tell you whether the change
was an improvement.

Portability is a real benefit. Just do not confuse "the code runs against both"
with "it works against both".

</details>

---

**Next →** [Bonus 5 — Google ADK](14-google-adk.md): a second agent framework,
pointed at our local model through exactly this adapter.
