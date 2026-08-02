"""Bonus 3, step 2: the other two connections this workshop has been sloppy about.

Two more handshakes we have been paying for repeatedly:

    Postgres    every script in modules 4-9 opens a fresh connection per
                operation. module 8's API does it per HTTP request.
    Ollama      the model is loaded into memory on first use and unloaded after
                an idle timeout. Reloading 4.7GB is not free.

Both are measured here rather than asserted.

Run:  python modules/12-connections/02_pools_and_keepalive.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
import psycopg
from psycopg_pool import ConnectionPool
from rich.console import Console
from rich.table import Table

from common.config import CHAT_MODEL, DATABASE_URL, OLLAMA_BASE_URL

console = Console()

QUERIES = 20

# --- Postgres: connect-per-query vs a pool ------------------------------------
console.rule("[bold]Postgres connections[/bold]")

started = time.perf_counter()
for _ in range(QUERIES):
    # This is what every script in modules 4-9 does, once per operation.
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("SELECT 1").fetchone()
per_query_ms = (time.perf_counter() - started) * 1000

# A pool keeps connections open and hands them out. `min_size` connections are
# established when the pool opens; `getconn`/`putconn` (here via `connection()`)
# borrow and return rather than connect and disconnect.
pool = ConnectionPool(DATABASE_URL, min_size=2, max_size=4, open=True)
pool.wait()          # block until min_size connections are actually ready

started = time.perf_counter()
for _ in range(QUERIES):
    with pool.connection() as conn:
        conn.execute("SELECT 1").fetchone()
pooled_ms = (time.perf_counter() - started) * 1000
pool.close()

table = Table(title=f"{QUERIES} trivial queries")
table.add_column("strategy")
table.add_column("total ms", justify="right")
table.add_column("per query", justify="right")
table.add_row("connect each time", f"{per_query_ms:.0f}", f"{per_query_ms / QUERIES:.1f}ms")
table.add_row("pooled", f"{pooled_ms:.0f}", f"{pooled_ms / QUERIES:.1f}ms")
console.print(table)

console.print(
    f"\n[dim]The query itself is `SELECT 1` — essentially free. Everything you "
    f"see is\nconnection setup: TCP, TLS if configured, authentication, session "
    f"state.\n\n"
    f"At {per_query_ms / QUERIES:.0f}ms per connection this is invisible in a "
    f"workshop and expensive in a\nservice handling requests. Module 8's API "
    f"opens two connections per approval;\nthe honest comment in its `db()` "
    f"helper says so.[/dim]\n"
)

# --- Ollama: model loading ----------------------------------------------------
console.rule("[bold]Ollama model loading[/bold]")

console.print("[dim]Ollama loads a model into memory on first use and unloads it "
              "after an idle\ntimeout (5 minutes by default). `keep_alive` "
              "controls that timeout.[/dim]\n")


def generate(keep_alive=None) -> float:
    body = {"model": CHAT_MODEL, "prompt": "Say OK", "stream": False}
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    started = time.perf_counter()
    httpx.post(f"{OLLAMA_BASE_URL}/api/generate", json=body, timeout=300)
    return (time.perf_counter() - started) * 1000


# Warm first, so we are measuring a loaded model.
generate()
warm_ms = generate()

# keep_alive=0 tells Ollama to unload immediately after responding.
generate(keep_alive=0)
time.sleep(2)
cold_ms = generate()          # this one pays the load cost
warm_again_ms = generate()

load = Table(title="Same prompt, three states")
load.add_column("state")
load.add_column("ms", justify="right")
load.add_row("warm (model resident)", f"{warm_ms:.0f}")
load.add_row("cold (after unload)", f"{cold_ms:.0f}")
load.add_row("warm again", f"{warm_again_ms:.0f}")
console.print(load)

overhead = cold_ms - warm_ms
console.print(
    f"\n[bold]Loading cost roughly {overhead:.0f}ms[/bold] on this machine for a "
    f"4.7GB model.\n"
)
console.print(
    "[dim]That is the tax on the first request after an idle period — which, for\n"
    "an incident responder, is nearly every request. Alerts are bursty: nothing\n"
    "for two hours, then four at once. The first one pays.\n\n"
    "Fixes, in order of bluntness: send `keep_alive: -1` to pin the model in\n"
    "memory indefinitely, set OLLAMA_KEEP_ALIVE on the server, or send a cheap\n"
    "warming request on a timer. All of them trade RAM for latency.[/dim]\n"
)

# --- The general point --------------------------------------------------------
console.rule("[bold]The pattern[/bold]")
console.print(
    "[dim]Three different systems, one shape: something expensive is established\n"
    "on first use, and code that treats it as free pays repeatedly.\n\n"
    "  MCP server    ~330ms per reconnect      -> hold the session\n"
    f"  Postgres      ~{per_query_ms / QUERIES:.0f}ms per connect        "
    f"-> use a pool\n"
    f"  Ollama        ~{overhead:.0f}ms per model load     -> keep_alive\n\n"
    "None of this matters at workshop scale, which is exactly why this workshop\n"
    "does none of it and says so in the comments. The failure mode to avoid is\n"
    "not the shortcut — it is taking a shortcut you have not written down, and\n"
    "discovering it in production as 'the agent is slow sometimes'.[/dim]\n"
)
