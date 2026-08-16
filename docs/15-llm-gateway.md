# Bonus 6 — LLM gateway: one door for every model call

> **The question this module answers:** every service calling providers directly
> works fine, until there are twenty of them. What replaces that?

**Time:** ~40 min · **Code:** `modules/15-llm-gateway/` · **You need:** module 13

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | Routing and fallbacks in-process | 10 min | `litellm.Router` |
| 2 | Pull it out into a service | 16 min | ~100 lines of gateway |
| 3 | Be the client | 14 min | Change a base URL, change nothing else |

---

## Where we are

Every call in this workshop names one model and hopes. Bonus 4 made the model
swappable; this makes the *policy around* model calls something you own in one
place instead of twenty.

## The problem

Once more than one team calls models, the same questions appear everywhere and
get answered differently:

- Which model does "the cheap one" mean this week?
- Where do provider keys live, and who can rotate them?
- What happens when a provider rate-limits?
- Who spent what, and how do we stop a runaway loop billing thousands?

Solved per-service, these drift. A **gateway** is one HTTP service everything
calls instead of calling providers directly, so the answers live in one place.

## What you'll build

- A router with aliases and fallbacks, in-process, and the latency a fallback
  costs
- A ~100-line gateway: virtual keys, per-team budgets enforced before the call,
  model aliases, and one call log
- A client that reaches it with nothing but a URL and a key — including the
  requests it is refused

---

## Concepts in this module

### Alias

The name callers ask for (`chat`, `chat-fast`) as distinct from the model that
answers. Callers express **intent**; operations decides what that intent maps to
today. Swapping models becomes config rather than a code change in twenty repos.

### Fallback

A second deployment to try when the first fails. It buys availability with
latency, because the failure is paid for in full before the retry starts. Never
free, usually worth it.

### Virtual key

A credential the gateway itself issues, carrying a team, a budget and a list of
aliases it may use. Applications hold one of these and never see a provider key
— so rotating a provider credential touches one service, and revoking a team is
a row in a table.

### Budget enforcement, before versus after

Spend checked before the call is a **control**; spend checked afterwards is a
**report**. The distinction is the entire reason a budget lives in the gateway
rather than in a dashboard.

### OpenAI-compatible

Speaking someone else's already-ubiquitous API instead of designing a better
one. It is an adoption decision, not a technical one: it makes migrating a
one-line change, and any gateway that requires an SDK change does not get
adopted.

---

## Build it

### Step 1 — Routing and fallbacks, still in-process

**Why:** the gateway's *policy* is worth understanding before its *packaging*.
All of it works as a library first.

```bash
python modules/15-llm-gateway/01_router.py
```

```
asked for chat        → served by ollama_chat/qwen2.5:7b (370ms): 'OK'
asked for chat-broken → served by ollama_chat/qwen2.5:7b (2143ms): 'OK'
```

The second request named a deployment that does not exist. The router tried it,
failed, and fell through to a working one — the caller got an answer instead of
an exception.

**Note the latency: 370ms against 2143ms.** The failure was paid for before the
fallback ran. Fallbacks buy availability with latency, which is usually the
right trade and never a free one.

The indirection is the point: your code asks for `"chat"` and has no idea what
answered. Swapping models becomes config, not code.

**But all of this is in your process.** Every service wanting these properties
must import the router and be configured with the same model list, keys and
limits — and they will drift.

---

### Step 2 — The gateway

**Why:** the properties in step 1 are only worth having if every service gets
them without opting in.

```bash
python modules/15-llm-gateway/02_gateway.py
```

About a hundred lines: FastAPI, the router, virtual keys, budgets, a call log.
It speaks `/v1/chat/completions`, so **any client that talks to OpenAI works by
changing a base URL** — which is the property that makes adoption possible.

```bash
curl -s http://127.0.0.1:4000/v1/chat/completions \
  -H 'Authorization: Bearer sk-team-sre' \
  -H 'Content-Type: application/json' \
  -d '{"model":"chat","messages":[{"role":"user","content":"Say OK"}]}'
```

Verified behaviour:

| request | result |
|---|---|
| `sk-team-sre` asking for `chat` | **200**, answer returned, spend recorded |
| `sk-team-analytics` asking for `chat` | **403** — that key may only use `chat-fast` |
| `sk-nope` | **401** — unknown key |
| `GET /admin/usage` | per-team spend against budget, recent calls |

**Four things that only a gateway can give you:**

**Virtual keys.** Applications never see a provider key. Rotating a provider
credential touches one service; revoking a team is a row in a table.

**Budgets checked *before* the call.** A budget enforced afterwards is a report.

**Aliases.** Callers ask for `chat` or `chat-fast` — intent, not model names.
Operations decides what those mean today.

**One log.** Who called what, what it cost, how long it took. Assembling that
from twenty services individually is the problem the gateway solves.

> **Instructor:** the adoption argument is worth stating plainly — because it
> speaks the OpenAI API, teams migrate by changing one environment variable.
> Any gateway that requires an SDK change will not get adopted.

---

### Step 3 — Be the client

**Why:** step 2 *claimed* that any OpenAI client works by changing a base URL,
and demonstrated it with `curl`. `curl` proves the wire format and nothing about
the developer experience, which is the thing actually being sold.

**Leave the gateway running** and open a second terminal:

```bash
python modules/15-llm-gateway/03_client.py
```

This script shares no code with the gateway and never imports it. It holds a URL
and a key — the same relationship a real application has.

**The demonstration that matters is the second block.** Module 13 called:

```python
litellm.completion(model="ollama_chat/qwen2.5:7b",
                   api_base="http://localhost:11434", ...)
```

The client calls:

```python
litellm.completion(model="openai/chat",
                   api_base="http://127.0.0.1:4000/v1",
                   api_key="sk-team-sre", ...)
```

Two settings changed. No new library, no new call shape, no code touched
anywhere else — and `openai/` here does not mean OpenAI, it means "speak that
protocol". The same one-line change works for the OpenAI SDK
(`OpenAI(base_url=…, api_key=…)`) and for LangChain (`ChatOpenAI(base_url=…,
api_key=…, model="chat")`; that needs `langchain-openai`, deliberately not in
this workshop's requirements so the pinned stack stays exactly as modules 3 and
9 were verified against).

**Then the policy table, which is the payoff:**

```
 request                        status   what the gateway said
 sre asks for chat                 200   OK! Is there anything specific you'd lik…
 analytics asks for chat-fast      200   OK! Is there anything specific you'd lik…
 analytics asks for chat           403   key for team 'analytics' may not use 'chat'; allowed: ['chat…
 unknown key                       401   unknown key
 no alias by that name             403   key for team 'sre' may not use 'chat-enormous'; allowed: ['c…
```

**Read where those refusals happened.** `analytics asks for chat` never reached
a model: no tokens, no latency, no spend. A permission checked after the call is
an audit finding; checked before it, it is a control.

Note also that the last row is a **403 rather than a 404**. The gateway will not
tell an unauthorised caller which aliases exist — a small thing, and the kind of
small thing that only has one place to live once you have a gateway.

The run ends with `/admin/usage`: per-team spend against budget, and the last
logged call. Every figure is `$0.000000` because local inference is free. The
shape is the lesson, not the number — point one alias at a hosted model and this
becomes the answer to "what did we spend on LLMs last month, by team", which most
organisations genuinely cannot answer.

**What just happened:** you migrated a client to a gateway by changing two
settings, and got refused three times without spending a token.

---

## What we did not use, and why

**LiteLLM Proxy is the production version of this** — the same idea with a
database, a UI, caching, guardrails and far more. We did not run it here for an
honest reason: `litellm.proxy` is incompatible with the FastAPI version this
workshop uses (it imports `get_flat_dependant`, removed in FastAPI 0.141), and
its CLI has a bare-import bug in 1.95.0.

Pinning FastAPI down to suit it would break `google-adk`, which requires
`fastapi>=0.133`. So we built the small version instead — which is this
workshop's habit anyway, and means you now know what the big one is doing.

Other production options worth knowing: **Portkey**, **Kong AI Gateway**,
**Cloudflare AI Gateway**, and the cloud providers' own.

---

## What we just built

One door: aliases so callers state intent, keys that carry policy, budgets that
stop a runaway loop before it bills anything, and a single log of every model
call anyone made — reachable from any OpenAI client by changing a URL.

---

## Live experiments (10 min)

**Exhaust a budget.** Drop `sk-team-analytics` to `"max_usd": 0.0` and call it —
429 before the model is touched.

**Add a real second model.** Pull `llama3.1:8b` and point `chat-fast` at it. Now
the aliases mean something, and callers still change nothing.

**Break the upstream.** Stop Ollama and call the gateway: a clean 502 rather
than a stack trace, because the gateway owns that translation.

**Move a caller.** Point `modules/13-litellm/01_one_interface.py` at the gateway
by editing its two arguments. Everything downstream of that line is unchanged,
and the call now appears in `/admin/usage` — which is what "migrating a service"
actually looks like.

---

## Homework

**Add per-key rate limiting.** Give each virtual key a requests-per-minute
ceiling and return 429 when it is crossed. Then run `03_client.py` in a loop and
watch it trip.

Then answer: **what should the gateway do when it rate-limits — reject, or
queue?** A rejection is honest and pushes the problem to the caller. A queue
hides it and turns your gateway into the thing that is slow. Write down which
you chose and what the caller is supposed to do about it.

---

## Checkpoint ✅

- [ ] You can explain what a gateway centralises that a library cannot
- [ ] You have seen a 403 from a key using a model it is not allowed
- [ ] You can say why budgets must be checked before the call
- [ ] You can explain why speaking the OpenAI API matters for adoption
- [ ] You have pointed a real client at the gateway by changing two settings
- [ ] You can state what a refused request costs in tokens

---

## Discussion questions

**1. A gateway is a single point of failure for every AI feature. Worth it?**

<details><summary>Answer</summary>

It is a real risk and it is the same risk you already accept for authentication,
service meshes and API gateways — with the same mitigations: run several,
health-check them, and give clients a documented direct-to-provider fallback for
genuine emergencies.

What you get in exchange is a single point of *control*, and the asymmetry
matters. Without one, revoking a leaked key, capping a runaway loop, or
answering "what did we spend on LLMs last month" all require touching every
service. With one, each is a single change.

The failure mode to design against is not the gateway being down; it is the
gateway becoming a bottleneck nobody owns — a service with no team, no SLO and
a config file twelve people edit.

</details>

**2. Where should guardrails live — the gateway or the application?**

<details><summary>Answer</summary>

Both, and it is worth being precise about which belongs where, because module 7
drew this line already.

The gateway is the right home for anything **universal and content-agnostic**:
rate limits, budgets, key scoping, PII redaction, request logging, blocking
models nobody should call.

Module 7's guards are the opposite — they are **application semantics**. "Do not
restart payment-service between 14:00 and 16:00 IST" cannot live in a gateway,
because the gateway sees a chat completion and has no idea an action is being
proposed. It does not know what a settlement window is and should not.

The rule: the gateway constrains *the call*; the application constrains *the
consequence*. Anything about what the model's output will cause belongs next to
the code that acts on it.

</details>

**3. The client in step 3 could bypass the gateway by calling Ollama directly.
What actually stops it?**

<details><summary>Answer</summary>

Nothing in this code, and that is worth being honest about, because it is the
gap between a gateway that works and a gateway that is a control.

In production the enforcement is not in the gateway at all — it is in the
network and in credential management. Provider keys exist *only* on the gateway,
so a service that bypasses it has nothing to authenticate with; egress rules
stop direct calls to provider domains; the gateway is the only thing with a
route out.

The gateway is where policy is *expressed*. It is not, by itself, where policy
is *enforced* — and a team that ships one without the network work has built a
convenient library with an HTTP interface.

Same shape as module 7's lesson, one layer down: a rule is only a rule if
something makes it impossible to ignore.

</details>

---

**Next →** [Bonus 7 — Fine-tuning](16-finetuning.md): the last lever, and the
one most teams reach for first by mistake.
