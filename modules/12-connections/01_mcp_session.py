"""Bonus 3, step 1: stop paying to reconnect. Measured.

Module 3's MCP bridge opens a fresh connection for every tool call: spawn a
subprocess, negotiate the protocol, call one tool, tear it all down. That was a
deliberate simplification so the agent scripts could stay ordinary synchronous
Python — and the file says so.

This script measures what it costs, then does it properly.

Run:  python modules/12-connections/01_mcp_session.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03-mcp-tools"))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from rich.console import Console
from rich.table import Table

console = Console()

SERVER = str(Path(__file__).resolve().parents[1] / "03-mcp-tools" / "mcp_server.py")
PARAMS = StdioServerParameters(command=sys.executable, args=[SERVER])

CALLS = [
    ("get_service_status", {"service": "payment-service"}),
    ("get_recent_deploys", {"service": "payment-service"}),
    ("get_error_logs", {"service": "payment-service", "limit": 5}),
    ("get_service_status", {"service": "checkout-service"}),
    ("get_error_logs", {"service": "checkout-service", "limit": 3}),
    ("get_service_status", {"service": "log-aggregator"}),
]


# --- The module 3 approach: reconnect every time -----------------------------
async def call_once(name: str, args: dict) -> str:
    """Spawn the server, initialise, call one tool, shut it all down."""
    async with stdio_client(PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
            return result.content[0].text if result.content else ""


async def spawn_per_call() -> list[float]:
    timings = []
    for name, args in CALLS:
        started = time.perf_counter()
        await call_once(name, args)
        timings.append((time.perf_counter() - started) * 1000)
    return timings


# --- The right way: one session, held open ------------------------------------
async def one_session() -> tuple[float, list[float]]:
    """Open once, call many times, close once.

    Note where the `async with` blocks sit: OUTSIDE the loop. That single
    structural change is the entire optimisation.
    """
    setup_started = time.perf_counter()
    timings = []
    async with stdio_client(PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            setup_ms = (time.perf_counter() - setup_started) * 1000

            for name, args in CALLS:
                started = time.perf_counter()
                await session.call_tool(name, args)
                timings.append((time.perf_counter() - started) * 1000)
    return setup_ms, timings


console.print(f"[bold]{len(CALLS)} tool calls, two connection strategies[/bold]\n")

per_call = asyncio.run(spawn_per_call())
setup_ms, pooled = asyncio.run(one_session())

table = Table()
table.add_column("call")
table.add_column("reconnect each time (ms)", justify="right")
table.add_column("one session (ms)", justify="right")
for i, (name, _) in enumerate(CALLS):
    table.add_row(name, f"{per_call[i]:.0f}", f"{pooled[i]:.1f}")
table.add_section()
table.add_row("[bold]total[/bold]", f"[bold]{sum(per_call):.0f}[/bold]",
              f"[bold]{sum(pooled) + setup_ms:.0f}[/bold]")
console.print(table)

speedup = sum(per_call) / max(sum(pooled) + setup_ms, 0.001)
console.print(
    f"\n[bold]{speedup:.0f}× faster[/bold], and the gap widens with every call: "
    f"the session pays\nits {setup_ms:.0f}ms setup once, the other approach pays "
    f"it {len(CALLS)} times.\n"
)

console.print(
    "[dim]Per call, reconnecting costs roughly "
    f"{sum(per_call) / len(CALLS):.0f}ms against "
    f"{sum(pooled) / len(CALLS):.1f}ms. Next to a\n"
    "four-second model call that looked like noise, which is exactly why the\n"
    "shortcut survived three modules. It stops being noise when the agent makes\n"
    "many tool calls, or when the server is over a network rather than a pipe.[/dim]\n"
)

console.print(
    "[bold]What you give up[/bold]\n"
    "[dim]Holding a session means your code has to be async, or you need a\n"
    "background event loop to bridge it into synchronous code. That is the real\n"
    "reason module 3 took the shortcut — not performance, but keeping the diff\n"
    "between step 3 and step 4 down to a single line.\n\n"
    "It also means owning a lifecycle: the subprocess can die, and a long-lived\n"
    "session needs reconnect logic that a spawn-per-call design gets for free.\n"
    "Persistent connections are faster and they are not simpler.[/dim]\n"
)

console.print(
    "[bold]The general shape[/bold]\n"
    "[dim]Anything with a handshake — TLS, a database, an MCP server, an HTTP\n"
    "connection — is cheaper held open than reopened, and every one of them\n"
    "trades that speed for a lifecycle you now have to manage. Measure before\n"
    "you decide, then be honest in a comment about which one you chose and\n"
    "why.[/dim]\n"
)
