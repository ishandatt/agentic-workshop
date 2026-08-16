# Bonus 4 — LiteLLM: one interface for every model

> **The question this module answers:** LangChain normalises providers. So does
> LiteLLM. What is the difference, and when do you want the thinner one?

**Time:** ~30 min · **Code:** `modules/13-litellm/` · **You need:** module 1 finished

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | One interface, one call | 13 min | Call the local model OpenAI-style, and price it |
| 2 | Swap the prefix and prove it | 17 min | Point at a provider you have no key for |

---

## Where we are

Every call so far went through `ChatOllama` — a LangChain class for one
provider. That is fine until you need a second provider, or until a library you
want only speaks OpenAI.

## The problem

Provider APIs are 90% the same and 100% incompatible. Everyone sends a list of
`role`/`content` messages and gets text back; nobody agrees on what to call the
token counts, where the system prompt goes, or how a tool call is shaped.

So three questions arrive together the first time you need a second model:

- How much of your code has to change to call a different provider?
- Where do you find out what a hosted model *costs*, without a spreadsheet
  somebody maintains by hand?
- What do you do about the library you depend on that only speaks OpenAI —
  which, in this ecosystem, is most of them?

LangChain answers these as a side effect of being a framework. LiteLLM answers
only these, and is much less machinery.

## What you'll build

- The same local call you made in module 1, in the OpenAI request/response shape
- A price comparison of that exact call across three hosted models
- A demonstration that changing provider changes one string — including what
  happens when you point at a provider you have no credentials for
- A streamed call through the same interface, and the reason usage data still
  only arrives at the end

---

## Concepts in this module

### The provider prefix

LiteLLM identifies the backend from a prefix on the model string:

```
ollama_chat/qwen2.5:7b        our local model
openai/gpt-4o-mini            OpenAI
anthropic/claude-sonnet-4-5   Anthropic
bedrock/anthropic.claude-…    AWS Bedrock
```

Everything after the prefix is the name that provider knows. The prefix selects
a translator; the rest of the call is unchanged.

### The OpenAI shape as a lingua franca

LiteLLM does not invent a neutral format — it picks OpenAI's and translates
everything into it. That is a deliberate and slightly boring choice, and it is
why it works: an enormous amount of tooling already speaks that shape, so
anything LiteLLM fronts inherits that ecosystem for free. The same reasoning
drives the gateway in bonus 6.

### The model registry

LiteLLM ships a table of published prices and capabilities for hosted models,
queryable offline with `get_model_info`. It is the same idea as our
`REF_PRICE_*` values in `.env`, with someone else maintaining the numbers.

### The distinction worth holding

| | what it is | what it does |
|---|---|---|
| **LangChain** | application framework | prompts, chains, tools, agents, memory |
| **LiteLLM** | model adapter | one call signature, ~100 providers |

They are not competitors. LiteLLM is roughly the *bottom layer* of what
LangChain does, sold separately — and the next module uses it to point Google's
ADK at our local model.

---

## Build it

### Step 1 — One interface, one call

**Why:** before arguing about abstraction layers, see what this one actually
returns.

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

### Step 2 — Swap the prefix, and prove it

**Why:** step 1 *asserted* that changing provider changes one string. Asserting
it is cheap. The interesting case is the one nobody demonstrates: pointing at a
provider you have no key for.

```bash
python modules/13-litellm/02_swap_the_prefix.py
```

**What you should see:** a table of model strings, a real local answer, a
deliberate failure, a streamed answer, and a closing table of what the
abstraction does *not* flatten.

**The failure is the point.**

```
raised AuthenticationError
  OpenAIException - The api_key client option must be set…
```

Read what that is not. It is not a `TypeError`, not "unsupported provider", not
a different function with different arguments. LiteLLM built a correct OpenAI
request, sent it, and OpenAI declined to serve an anonymous caller.

So the honest statement of what portability buys you: **your code is already
portable.** What is not portable is credentials, cost, latency, and behaviour —
and only the first of those has an error message.

**Streaming survives the translation**, which matters because a shim that only
handles the simple one-shot case is not much of a shim:

```python
for chunk in litellm.completion(..., stream=True,
                                stream_options={"include_usage": True}):
```

Note `stream_options` — without it most OpenAI-compatible streams carry no token
counts at all. And note where the counts turn up when you do ask: on the **final
chunk**, exactly as module 1 found with LangChain. The number cannot exist until
generation stops, so no abstraction can hand it to you earlier.

**Then the last table, which is the one to leave with:**

```
 differs by provider        what the shared interface does about it
 tool-calling reliability   nothing — same API, wildly different hit rates
 structured output          nothing — module 2 measured this
 prompt caching             provider-specific parameters
 reasoning modes            provider-specific, late in any adapter
 the actual answer          nothing at all
```

**What just happened:** you proved the portability claim and bounded it in the
same run. The code moves between providers. Whether it still *works* is a
question only module 6's evals can answer.

> **Instructor:** the last row is worth saying out loud. Teams adopt an adapter,
> swap a model, ship it, and are surprised when quality moves — because the
> interface promised sameness and delivered only compatibility. "The code runs
> against both" is not "it works against both".

---

## What we just built

A demonstration that provider independence is real, is one string wide, and
stops precisely at the network boundary — plus a price list for models you have
not called.

---

## When to use it

**Reach for it when:** you support more than one provider or expect to; you want
one place to change models; you need cost accounting across providers; or a
library you depend on only speaks OpenAI (very common).

**Don't when:** one provider, one call shape, no plans to change. It is then a
dependency that buys nothing.

---

## Live experiments (10 min)

**Break the prefix.** Change `ollama_chat/` to `ollama/` and see what changes —
they are different translators with different tool-calling support.

**Price your own pipeline.** Take module 9's measured 5,061 tokens per incident
and run it through `get_model_info` for three hosted models. That is your real
migration cost.

**Drop `stream_options`.** Remove it from step 2 and re-run. The stream still
works and the usage numbers vanish — a silent failure that would quietly zero
your cost tracking in production.

**Point it at the wrong port.** Set `api_base` to `http://localhost:11435` and
compare the exception class with the no-key one. Connection failures and
authentication failures are different problems and it is worth seeing that the
adapter keeps them distinct.

---

## Homework

**Add a provider to module 1's script.** Rewrite `02_raw_ollama.py`'s raw HTTP
call as a `litellm.completion()` call, keeping the metrics line identical. Then
make the model string come from `.env` rather than from code.

Then answer: **you now have three ways to call a model** — raw HTTP, LangChain's
`ChatOllama`, and LiteLLM. Write one sentence per layer saying what it would
cost you to remove it. The one you cannot justify is the one to drop.

---

## Checkpoint ✅

- [ ] You can state the difference between LiteLLM and LangChain in one sentence
- [ ] You have called your local model with an OpenAI-shaped call
- [ ] You can find the published price of a hosted model without leaving Python
- [ ] You have seen a hosted call fail on *credentials* rather than on code
- [ ] You can say why streamed usage data only arrives on the last chunk

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

**3. Step 2's hosted call failed on authentication. Why is that reassuring
rather than annoying?**

<details><summary>Answer</summary>

Because it tells you exactly how far the abstraction got, and it got all the
way.

A `TypeError` would mean the call shape differs and your code needs branching. A
"provider not supported" would mean the adapter never had a translator. An
`AuthenticationError` means the request was assembled correctly, transmitted
correctly, and rejected by policy at the other end — the one failure that is not
an engineering problem at all.

It also draws a useful line for planning a migration. The work is provisioning:
keys, quotas, billing, network egress, data-residency review. The code was never
the hard part, which is the opposite of what most migration estimates assume.

</details>

---

**Next →** [Bonus 5 — Google ADK](14-google-adk.md): a third agent framework,
pointed at our local model through exactly this adapter.
