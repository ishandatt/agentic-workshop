"""An MCP server exposing our fake ops tools.

This is a **separate program**. Nothing in it imports LangChain, LangGraph, or
knows that an LLM exists. That separation is the entire point of MCP: tools are
published by a server, and any client that speaks the protocol can use them.

For modules 3 and 12 you never run this by hand. The agent scripts start it as
a subprocess and talk to it over stdin/stdout — the "stdio transport". Two
ordinary pipes.

To poke at it yourself:
    python modules/03-mcp-tools/04_mcp_agent.py     # starts it for you

Bonus 8 is the exception. n8n runs in a container and cannot be handed a pipe
into a process on your host, so there is a second transport behind a flag:

    python modules/03-mcp-tools/mcp_server.py --http    # streamable HTTP, :8765

Nothing above the `if __name__` block changes between the two. Same server
object, same four tools, same docstrings — which is the point.

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
from mcp.server.transport_security import TransportSecuritySettings

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
    # Bonus 8 needs this same server reachable from a container, which stdio
    # cannot do — you cannot pipe stdin to a process in another machine's
    # namespace. So there is a second transport, and NOTHING above this line
    # changes for it: same server object, same four tools, same docstrings.
    #
    # That is worth pausing on, because it is module 3's whole claim made
    # testable. The tools do not know what transport carries them, any more
    # than they know whether the client is LangGraph, ADK, or a drag-and-drop
    # canvas run by someone who does not write Python.
    if "--http" in sys.argv:
        # 0.0.0.0, not the 127.0.0.1 default: a container reaching the host
        # arrives on a different interface, and a server bound to loopback is
        # invisible to it. This is the single most common reason "n8n cannot
        # see my tools".
        server.settings.host = "0.0.0.0"
        # Explicit, because FastMCP's default is 8000 — which modules 2, 5 and
        # 8 already use for their FastAPI services. Two servers silently
        # fighting over a port is a bad first five minutes.
        server.settings.port = 8765
        # DNS-rebinding protection: the transport validates the Host header and
        # rejects anything it was not told to expect. A browser on a malicious
        # page cannot then trick your machine into driving a local MCP server,
        # which is a real attack and a good default.
        #
        # What FastMCP actually does here is worth knowing exactly, because the
        # summaries online get it wrong in both directions:
        #
        #   * constructed on a loopback host (our default, 127.0.0.1), it turns
        #     protection ON and fills in a LOOPBACK allow-list for you —
        #     ["127.0.0.1:*", "localhost:*", "[::1]:*"]. Not an empty list.
        #   * constructed on any other host, it passes None, and the middleware
        #     then defaults protection OFF for backwards compatibility.
        #
        # So the loopback default is why `host.containers.internal:8765` — which
        # is how a container reaches the host — is refused with a rather cryptic
        # **421 Misdirected Request**. The fix is to name the hosts you expect,
        # NOT to switch the protection off, which is the first suggestion you
        # will find online.
        #
        # Read once, and note the ordering: we set `host` above AFTER the server
        # object was built, so the loopback default was already computed. We are
        # replacing it wholesale here rather than editing it.
        server.settings.transport_security = TransportSecuritySettings(
            allowed_hosts=[
                f"localhost:{server.settings.port}",
                f"127.0.0.1:{server.settings.port}",
                # how a Podman/Docker container addresses the host it runs on
                f"host.containers.internal:{server.settings.port}",
                f"host.docker.internal:{server.settings.port}",
            ],
            # NOT ["*"] — that looks like a wildcard and is not one. The matcher
            # does an exact string compare, then accepts only patterns ending in
            # ":*", so a bare "*" matches nothing except a literal `Origin: *`
            # header. It appears to work with n8n purely because a server-side
            # client sends no Origin at all, and an absent Origin is allowed.
            # Point a browser-based client or MCP Inspector at it and you get a
            # 403 that looks exactly like the 421 above.
            allowed_origins=[
                "http://localhost:*",
                "http://127.0.0.1:*",
            ],
        )
        # And the honest caveat, since module 7 spends forty-five minutes on
        # this distinction: none of the above is authentication. An allow-list
        # of Host headers stops a browser, because the browser sets that header
        # and the page cannot lie about it. Any non-browser client sets it to
        # whatever it likes. On `0.0.0.0` with no auth, anyone who can route to
        # port 8765 can call `restart_service`. That is survivable here only
        # because fake_infra.py is a dictionary — nothing real is reachable.
        # A server that touched production would need a token, not a hostname.
        print(f"ops-tools MCP server on http://localhost:{server.settings.port}"
              f"{server.settings.streamable_http_path}", file=sys.stderr)
        server.run(transport="streamable-http")
    else:
        # `transport="stdio"` means: read JSON-RPC requests from stdin, write
        # responses to stdout. It is why the client can start this file as a
        # plain subprocess with no ports, no network, and no configuration.
        # This is still the default, so modules 3 and 12 are unaffected.
        server.run(transport="stdio")
