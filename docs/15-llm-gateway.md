# Bonus 6 — LLM gateway: one door for every model call

> **The question this module answers:** every service calling providers directly
> works fine, until there are twenty of them. What replaces that?

**Time:** ~30 min · **Code:** `modules/15-llm-gateway/` · **You need:** module 13

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | Routing and fallbacks in-process | 12 min | `litellm.Router` |
| 2 | Pull it out into a service | 18 min | ~100 lines of gateway |

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

---

## Build it

### Step 1 — Routing and fallbacks, still in-process

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

## Live experiments

**Exhaust a budget.** Drop `sk-team-analytics` to `"max_usd": 0.0` and call it —
429 before the model is touched.

**Add a real second model.** Pull `llama3.1:8b` and point `chat-fast` at it. Now
the aliases mean something, and callers still change nothing.

**Break the upstream.** Stop Ollama and call the gateway: a clean 502 rather
than a stack trace, because the gateway owns that translation.

---

## Checkpoint ✅

- [ ] You can explain what a gateway centralises that a library cannot
- [ ] You have seen a 403 from a key using a model it is not allowed
- [ ] You can say why budgets must be checked before the call
- [ ] You can explain why speaking the OpenAI API matters for adoption

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

---

**Next →** [Bonus 7 — Fine-tuning](16-finetuning.md): the last lever, and the
one most teams reach for first by mistake.
