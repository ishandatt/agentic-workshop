# Payment Service — On-Call Runbook

**Owner:** Payments Platform · **Last reviewed:** 2026-07-14 · **Doc ID:** RB-PAY-001

> This runbook is fiction, written for a workshop. Every rule, name and number
> in it was invented. That is the point: no model has been trained on any of
> this, so anything an agent tells you from this document, it can only have
> learned by *retrieving* it.

---

## 1. Before you touch anything: the settlement window

**The payment service must never be restarted between 14:00 and 16:00 IST.**

This is the daily settlement window, when the settlement worker pool batches
the day's authorised transactions and submits them to the card processor.
Workers hold uncommitted batches in memory for up to 90 seconds before flushing.

Restarting during the window drops those batches mid-flight. The processor has
already seen the authorisation, so the reconciliation job re-submits them the
next morning — and customers are charged twice. We did this on 2025-11-19 and
refunded 4,812 duplicate charges over the following week.

Outside the window a restart is routine and safe.

**If a restart genuinely cannot wait until 16:00 IST**, it requires explicit
sign-off from the Payments Platform lead (see §6), and the settlement queue must
be drained first:

```
paycli settlement drain --wait-for-empty --timeout 300
```

Note that IST is UTC+05:30, so the window is 08:30–10:30 UTC. Alert timestamps
are in UTC; convert before deciding.

---

## 2. First check for any payment-service 5xx spike

**Always check the Redis connection pool before anything else.**

Roughly seven out of ten payment-service error-rate incidents come down to
`redis-sessions` pool exhaustion. It is the single most common cause and the
cheapest to rule out, so it goes first regardless of what the alert says.

```
paycli pool status --service payment-service --pool redis-sessions
```

Healthy looks like: `size >= 40`, `waiting == 0`.

Pool exhaustion looks like: `active == size` with a non-zero `waiting` count,
and log lines reading `TimeoutError: could not acquire connection from pool`.

**Do not restart the service to clear an exhausted pool.** The pool refills on
restart and the error disappears for ten minutes, which reads like a fix and is
not one. The pool will exhaust again, and you will have burned a restart and
lost the diagnostic state. Find out *why* demand exceeded the pool.

The usual reasons, in order of frequency:

1. A deploy reduced `pool.size` (see §3)
2. A downstream slowdown is holding connections open longer than usual
3. Genuine traffic growth — check whether request volume actually rose

---

## 3. Connection pool configuration changes

Any deploy that changes `settlement.pool.size` **must** go out as a canary at
10% for a minimum of one hour. This is enforced by policy, not by tooling, so it
gets skipped under time pressure.

The safe floor for `settlement.pool.size` in production is **40**. Anything
below that cannot sustain peak settlement throughput, which is roughly 340
transactions per second between 14:00 and 16:00 IST.

If you find a recent deploy that lowered the pool below 40, **roll it back
rather than restarting**:

```
paycli deploy rollback --service payment-service --to-previous
```

Rollback restores the previous pool configuration without dropping in-flight
settlements, which is why it is preferred over a restart even outside the
settlement window.

---

## 4. Service quirks worth knowing

**payment-service** takes approximately 90 seconds after start to warm its
routing caches. Error rates stay elevated for that period. Do not interpret the
first 90 seconds of post-restart metrics as a failed restart, and do not restart
again — that is how a five-minute incident becomes an hour.

**checkout-service** p99 latency rises sharply during marketing campaigns and
this is expected, not an incident. The promo banner renders server-side and adds
1.5–2.0 seconds to p99 while a campaign is live. Confirm against the campaign
calendar before escalating. Latency alone, with no change in error rate, is not
a page.

**log-aggregator** disk grows at roughly 2% per day against a 14-day retention
policy. It is designed to sit in the 75–85% band, and the 80% alert threshold is
mis-tuned. Below 90% this needs no action. Above 90%, reduce retention to 7 days
rather than expanding the volume:

```
paycli logs set-retention --days 7
```

**card-processor** is a third party. When it is genuinely down, our error rate
is accompanied by `upstream_status=503` in the logs. If the processor reports no
incident and we see no `upstream_status` errors, the problem is ours.

---

## 5. Actions and their blast radius

| Action | Safe to automate? | Notes |
|---|---|---|
| Read metrics, logs, deploy history | Yes | No side effects |
| `paycli pool status` | Yes | Read-only |
| `paycli deploy rollback` | **No** — needs human approval | Preferred over restart |
| `paycli service restart` | **No** — needs human approval | Forbidden 14:00–16:00 IST |
| `paycli logs set-retention` | **No** — needs human approval | Deletes data |
| `paycli settlement drain` | **No** — needs human approval | Blocks new settlements |

Anything in the "No" column requires a named human to approve, and the approval
must be recorded against the incident. An agent may propose these actions. It
may not take them.

---

## 6. Escalation

1. **First 15 minutes** — post in `#payments-oncall` with the alert and what you
   have checked so far.
2. **After 15 minutes with no root cause** — page the Payments Platform lead,
   **Priya Raghavan**, via PagerDuty schedule `payments-platform-primary`.
3. **Customer-visible impact over 30 minutes, or any suspected duplicate
   charge** — page **Tom Oyelaran** (Director, Payments) directly and open a
   Sev-1. Duplicate charges are always Sev-1 regardless of volume.
4. **Card processor confirmed at fault** — Priya opens the vendor ticket. Do not
   contact the processor directly; our contract routes all incidents through the
   named vendor contact.

Out of hours, the PagerDuty schedule is authoritative. Slack is not an
escalation path after 20:00 IST.

---

## 7. Post-incident

Every payment-service incident with customer-visible impact needs a written
review within three working days, filed against the incident ID. Duplicate
charges additionally require a reconciliation report from Finance before the
incident can be closed.
