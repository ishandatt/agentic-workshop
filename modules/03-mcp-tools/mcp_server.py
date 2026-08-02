"""An MCP server exposing our fake ops tools.

This is a **separate program**. Nothing in it imports LangChain, LangGraph, or
knows that an LLM exists. That separation is the entire point of MCP: tools are
published by a server, and any client that speaks the protocol can use them.

You never run this by hand. The agent scripts start it as a subprocess and talk
to it over stdin/stdout — the "stdio transport". Two ordinary pipes.

To poke at it yourself:
    python modules/03-mcp-tools/04_mcp_agent.py     # starts it for you

MCP (Model Context Protocol) in one paragraph: a small JSON-RPC protocol for
telling a client "here are the tools I have, here are their argument schemas",
and for that client to say "call this one with these arguments". That is nearly
all of it. The value is not the wire format; it is that the tool no longer has
to live inside your agent's codebase.
"""

import sys
from pathlib import Path

# Make `fake_infra` importable when this file is launched as a subprocess from
# a different working directory. `parents[0]` is this file's own folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from mcp.server.fastmcp import FastMCP

import fake_infra

# `log_level="ERROR"` keeps the server quiet. By default it logs every request
# to stderr, which in a workshop drowns out the thing you are trying to watch.
server = FastMCP("ops-tools", log_level="ERROR")


# `@server.tool()` registers the function below as an MCP tool. Three things
# are published to any client that connects, and all three come from ordinary
# Python that you would have written anyway:
#
#   the name         the function name
#   the arguments    the type hints (service: str) become a JSON Schema
#   the description  the docstring
#
# That last one matters enormously. Unlike the Pydantic field descriptions in
# module 2 — which Ollama never showed the model — a tool's description IS sent
# to the model, because it has to be: the model cannot choose a tool it knows
# nothing about. Write these as if for a new teammate.
@server.tool()
def get_service_status(service: str) -> dict:
    """Get current health for a service: status, error rate, latency, replica counts.

    Use this first when investigating any alert, to confirm what the alert
    claims is still true right now.
    """
    if service not in fake_infra.SERVICES:
        # Returning an error as DATA rather than raising is deliberate. The
        # model reads this string and can correct itself — an exception would
        # just crash the agent loop.
        return {"error": f"unknown service {service!r}",
                "known_services": fake_infra.known_services()}
    return {"service": service, **fake_infra.SERVICES[service]}


@server.tool()
def get_recent_deploys(service: str) -> dict:
    """List recent deployments to a service, newest first, with commit messages.

    Use this when a problem started suddenly. Most incidents are caused by a
    change, and the commit message is often the fastest route to a hypothesis.
    """
    if service not in fake_infra.DEPLOYS:
        return {"error": f"unknown service {service!r}",
                "known_services": fake_infra.known_services()}
    return {"service": service, "deploys": fake_infra.DEPLOYS[service]}


@server.tool()
def get_error_logs(service: str, limit: int = 5) -> dict:
    """Get the most recent error and warning log lines for a service.

    Use this to confirm or kill a hypothesis. `limit` caps how many lines come
    back — the default of 5 is deliberately small, because log lines are
    expensive in tokens and a model reading 500 of them reasons worse, not
    better.
    """
    if service not in fake_infra.ERROR_LOGS:
        return {"error": f"unknown service {service!r}",
                "known_services": fake_infra.known_services()}
    # Slicing a list with [:limit] takes at most that many items — it does not
    # raise when the list is shorter, unlike indexing.
    return {"service": service, "lines": fake_infra.ERROR_LOGS[service][:limit]}


@server.tool()
def restart_service(service: str) -> dict:
    """Restart a service. DISRUPTIVE: drops in-flight requests.

    Only use this when you have identified a cause that a restart addresses,
    and never as a first step.
    """
    if service not in fake_infra.SERVICES:
        return {"error": f"unknown service {service!r}",
                "known_services": fake_infra.known_services()}

    # Simulated. Nothing restarts; we record the intent so it can be inspected.
    #
    # Note this tool is a different KIND of thing from the three above: they
    # read, this one writes. Nothing in MCP marks that distinction for us, and
    # nothing stops the model calling it. Hold that thought.
    fake_infra.ACTION_LOG.append({"action": "restart", "service": service})
    return {
        "service": service,
        "result": "restart initiated (simulated)",
        "note": "no real infrastructure was touched",
    }


if __name__ == "__main__":
    # `transport="stdio"` means: read JSON-RPC requests from stdin, write
    # responses to stdout. It is why the client can start this file as a plain
    # subprocess with no ports, no network, and no configuration.
    server.run(transport="stdio")
