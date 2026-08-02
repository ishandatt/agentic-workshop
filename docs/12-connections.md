# Bonus 3 — Connections: the handshakes you are paying for twice

> **The question this module answers:** this workshop reconnects to everything,
> constantly. What does that actually cost?

**Time:** ~30 min · **Code:** `modules/12-connections/` · **You need:** modules 3 and 8 finished

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | The MCP session | 15 min | Reconnect-per-call vs one session, measured |
| 2 | Pools and model loading | 15 min | Postgres and Ollama, same shape |

---

## Where we are

Three times in this workshop a comment says some version of *"this is simpler
than it should be, and here is what it costs"*:

- `mcp_bridge.py` opens a fresh connection per tool call
- module 8's API opens a Postgres connection per operation
- nothing anywhere thinks about whether the model is loaded

Those were deliberate, and each is written down where it happens. This module
measures all three, because a shortcut you have quantified is an engineering
decision and a shortcut you have not is a bug waiting to be discovered by a
user.

---

## Concepts in this module

### Connection setup cost

Anything with a handshake — a subprocess, TCP, TLS, authentication, protocol
negotiation — costs meaningfully more to establish than to use. Reopening per
operation pays that cost every time.

### Connection pool

Keep N connections open, hand them out, take them back. The borrow is
microseconds; the connect was milliseconds.

### Model residency

Ollama loads a model into RAM on first use and unloads it after an idle timeout
(5 minutes by default). `keep_alive` controls that timeout.

---

## Build it

### Step 1 — The MCP session

```bash
python modules/12-connections/01_mcp_session.py
```

Six tool calls, two strategies:

```
 call                 reconnect each time (ms)   one session (ms)
 get_service_status                        348                1.9
 get_recent_deploys                        322                0.7
 get_error_logs                            322                0.7
 get_service_status                        321                0.8
 get_error_logs                            331                1.0
 get_service_status                        335                0.7
 total                                    1979                291
```

**330 ms per call against 1.0 ms.** Three hundred times, per call — and the
structural change is moving two `async with` blocks outside the loop.

Next to a four-second model call, 330 ms looked like noise, which is exactly why
the shortcut survived three modules. It stops being noise when the agent makes
many tool calls, or when the server is across a network rather than a pipe.

**And be clear about what a session costs you.** Holding one means your code is
async, or you maintain a background event loop to bridge into sync code — which
was the real reason module 3 avoided it, not performance. It also means owning a
lifecycle: the subprocess can die, and long-lived sessions need reconnect logic
that spawn-per-call gets for free.

> **Instructor:** the honest summary is that persistent connections are faster
> and *not simpler*. Module 3 chose simplicity so that the diff between "local
> tools" and "MCP tools" stayed at one line, which was worth more pedagogically
> than 300ms.

---

### Step 2 — Pools and model loading

```bash
python modules/12-connections/02_pools_and_keepalive.py
```

**Postgres**, twenty trivial queries:

```
 strategy            total ms   per query
 connect each time        149       7.5ms
 pooled                    15       0.8ms
```

The query is `SELECT 1` — free. Everything measured is connection setup. At
7.5 ms it is invisible in a script and expensive in a service; module 8's API
opens two connections per approval.

**Ollama**, the same prompt in three states:

```
 state                     ms
 warm (model resident)    640
 cold (after unload)     4279
 warm again               459
```

**Loading cost ~3.6 seconds** for a 4.7 GB model.

That is the tax on the first request after an idle period — which, for an
incident responder, is nearly every request. Alerts are bursty: nothing for two
hours, then four at once. The first one pays 3.6 seconds before any thinking
starts.

Fixes, in order of bluntness: `keep_alive: -1` to pin the model in memory,
`OLLAMA_KEEP_ALIVE` on the server, or a cheap warming request on a timer. All of
them trade RAM for latency.

> **Instructor:** this one lands with anyone who has demoed a local model and
> watched the first response crawl. It is almost always model loading, not the
> model being slow.

---

## What we just built

Numbers for three shortcuts:

| what | cost of doing it naively | fix |
|---|---|---|
| MCP server | ~330 ms per call | hold the session |
| Postgres | ~7.5 ms per connect | connection pool |
| Ollama | ~3.6 s per model load | `keep_alive` |

---

## Live experiments (10 min)

**Scale the MCP comparison.** Change `CALLS` to twenty entries. The gap widens
linearly — the session still pays setup once.

**Starve the pool.** Set `max_size=1` and run the queries from two threads. Watch
them serialise. Pools have their own failure mode: exhaustion.

**Pin the model.** Send `keep_alive: -1`, then re-run the cold/warm test. The
cold row disappears — and the RAM does not come back.

---

## Homework

**Fix `mcp_bridge.py` properly.** Give it a persistent session: open on first
use, reuse, close at exit. Decide whether to make the callers async or to run an
event loop in a background thread, and write down which you chose and why.

Then answer: **what happens when the server subprocess dies mid-run?** Handle it,
because spawn-per-call handled it for free and you have just given that up.

---

## Checkpoint ✅

- [ ] You can state the per-call cost of reconnecting to the MCP server
- [ ] You can explain what a connection pool actually saves
- [ ] You have measured cold vs warm model latency on your own machine
- [ ] You can name what a persistent connection costs you in complexity
- [ ] You can find the three comments in this repo that admit these shortcuts

---

## Discussion questions

**1. If pooling is this much faster, why is it not the default everywhere?**

<details><summary>Answer</summary>

Because a pool is state, and state has a lifecycle.

It must be created before use and closed after; it does not survive a fork
cleanly; it holds connections a database counts against `max_connections`; and
under concurrency it introduces a queue that can become the bottleneck.
Connect-per-operation has none of those properties — it is stateless, obviously
correct, and impossible to leak.

The right default depends on shape. A script that runs once and exits should
connect directly. A service handling requests should pool. The mistake is
carrying a script's pattern into a service, which is exactly what module 8's
API does and says so in a comment.

</details>

**2. The workshop takes all three shortcuts. Was that wrong?**

<details><summary>Answer</summary>

No, and the reason generalises beyond workshops.

Each shortcut bought something specific. Spawn-per-call kept every script
synchronous, which kept module 3's headline diff at one line. Connect-per-query
kept module 8's API readable. Ignoring `keep_alive` kept a variable out of every
example.

What makes them defensible is not that they are cheap — it is that **each one is
written down at the point where it happens**, with what it costs. That converts
a shortcut into a decision.

The failure mode to avoid is not taking shortcuts. It is taking one you have not
recorded, and rediscovering it in production as "the agent is slow sometimes".

</details>

**3. Which of the three matters most in production?**

<details><summary>Answer</summary>

Model loading, by an order of magnitude — 3.6 seconds against 330 ms and 7.5 ms.

It is also the least discussed, because it does not appear in code. There is no
line to review, no obvious place for a comment. It is a property of how your
runtime was configured, and it shows up as a latency distribution with a
horrible tail rather than as an error.

Which is the broader point: the connection costs you can see in code are the
ones you will eventually optimise. The expensive one here is invisible until
somebody measures it.

</details>

---

**That is the end of the material.** The core workshop is modules 0–9; these
three bonuses pick up the threads it deliberately left hanging. If you want a
next step: modules 4 and 6 together (retrieval quality and how you would know)
repay more effort than anything else here.
