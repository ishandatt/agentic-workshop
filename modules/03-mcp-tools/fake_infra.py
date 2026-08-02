"""A fake production estate: canned, plausible, and deliberately consistent.

Nothing here talks to anything real. The point of the workshop is the agent, not
the infrastructure, so the "ops platform" is a dictionary.

The data is not random, though. It tells a story that hangs together, so an
agent that actually looks things up can reach a conclusion it could not have
guessed from the alert text alone:

    payment-service is throwing 5xx errors      (the alert says so)
      -> a deploy went out 25 minutes earlier   (get_recent_deploys)
      -> that deploy changed connection pooling (the commit message)
      -> the logs are full of pool timeouts     (get_error_logs)

That chain is the whole reason tools exist. Keep it intact if you edit this.
"""

# A module-level dict acting as our database. Keys are service names; the
# values are ordinary Python dicts, which the MCP tools return as JSON.
SERVICES = {
    "payment-service": {
        "status": "degraded",
        "error_rate_percent": 12.4,
        "p99_latency_ms": 1840,
        "replicas": {"desired": 6, "ready": 6},
        "dependencies": ["postgres-primary", "redis-sessions", "card-processor"],
    },
    "checkout-service": {
        "status": "healthy",
        "error_rate_percent": 0.2,
        "p99_latency_ms": 2840,
        "replicas": {"desired": 4, "ready": 4},
        "dependencies": ["payment-service", "catalog-service"],
    },
    "log-aggregator": {
        "status": "healthy",
        "error_rate_percent": 0.0,
        "p99_latency_ms": 95,
        "replicas": {"desired": 3, "ready": 3},
        "dependencies": ["object-storage"],
    },
}

# Deploy history, newest first. The payment-service entry at 13:58 is the
# smoking gun for the alert that fires at 14:23.
DEPLOYS = {
    "payment-service": [
        {
            "sha": "9f2a41c",
            "deployed_at": "2026-08-01T13:58:00Z",
            "author": "priya.raghavan",
            "message": "perf: reduce settlement worker connection pool 50 -> 5",
        },
        {
            "sha": "3c7b910",
            "deployed_at": "2026-07-31T09:12:00Z",
            "author": "tom.oyelaran",
            "message": "chore: bump card-processor client to 4.2.1",
        },
    ],
    "checkout-service": [
        {
            "sha": "5d1e883",
            "deployed_at": "2026-07-30T16:40:00Z",
            "author": "mei.tanaka",
            "message": "feat: add promo banner to cart page",
        },
    ],
    "log-aggregator": [],
}

# Canned log lines. Realistic enough to reason about, short enough to read.
ERROR_LOGS = {
    "payment-service": [
        "2026-08-01T14:22:58Z ERROR settlement.worker  TimeoutError: could not acquire connection from pool (size=5, active=5, waiting=37)",
        "2026-08-01T14:22:51Z ERROR settlement.worker  TimeoutError: could not acquire connection from pool (size=5, active=5, waiting=31)",
        "2026-08-01T14:22:44Z WARN  settlement.worker  pool wait time 4820ms exceeds threshold 500ms",
        "2026-08-01T14:22:19Z ERROR api.charges       POST /v1/charges -> 503 (upstream settlement timeout)",
        "2026-08-01T14:21:02Z INFO  deploy.watcher    running revision 9f2a41c",
    ],
    "checkout-service": [
        "2026-08-01T09:05:12Z WARN  cart.handler      GET /cart served in 2841ms (cache miss)",
        "2026-08-01T09:04:58Z INFO  cart.handler      promo banner render added opaque 1.9s to p99",
    ],
    "log-aggregator": [
        "2026-08-01T03:11:40Z WARN  disk.monitor      volume /var/log at 81.5% capacity",
    ],
}

# Mutating actions get recorded here rather than doing anything. Later modules
# care a great deal about which tools mutate state and which only read it.
ACTION_LOG = []


def known_services() -> list[str]:
    """Service names this fake estate knows about.

    `list(SERVICES)` iterates a dict's KEYS — a Python shorthand that trips up
    newcomers. It is the same as `list(SERVICES.keys())`.
    """
    return list(SERVICES)
