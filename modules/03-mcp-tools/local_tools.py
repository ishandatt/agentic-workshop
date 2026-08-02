"""The same four ops tools, defined as ordinary in-process Python functions.

Steps 1-3 use these. Step 4 throws them away and pulls identical tools from the
MCP server instead, without touching the agent — which only works because the
two definitions behave the same. Keep them in sync if you edit either.

This is also the "before" picture MCP is arguing against: the tools live inside
the agent's codebase, so anything that wants them has to import this module,
run this language, and be deployed together with it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

# `@tool` turns a plain function into a LangChain tool. Like the MCP server's
# decorator, it reads the name, the type hints and the docstring — the same
# three things, because the model needs the same three things either way.
from langchain_core.tools import tool

import fake_infra


@tool
def get_service_status(service: str) -> dict:
    """Get current health for a service: status, error rate, latency, replica counts.

    Use this first when investigating any alert, to confirm what the alert
    claims is still true right now.
    """
    if service not in fake_infra.SERVICES:
        return {"error": f"unknown service {service!r}",
                "known_services": fake_infra.known_services()}
    return {"service": service, **fake_infra.SERVICES[service]}


@tool
def get_recent_deploys(service: str) -> dict:
    """List recent deployments to a service, newest first, with commit messages.

    Use this when a problem started suddenly. Most incidents are caused by a
    change, and the commit message is often the fastest route to a hypothesis.
    """
    if service not in fake_infra.DEPLOYS:
        return {"error": f"unknown service {service!r}",
                "known_services": fake_infra.known_services()}
    return {"service": service, "deploys": fake_infra.DEPLOYS[service]}


@tool
def get_error_logs(service: str, limit: int = 5) -> dict:
    """Get the most recent error and warning log lines for a service.

    Use this to confirm or kill a hypothesis. `limit` caps how many lines come
    back — the default of 5 is deliberately small, because log lines are
    expensive in tokens and a model reading 500 of them reasons worse.
    """
    if service not in fake_infra.ERROR_LOGS:
        return {"error": f"unknown service {service!r}",
                "known_services": fake_infra.known_services()}
    return {"service": service, "lines": fake_infra.ERROR_LOGS[service][:limit]}


@tool
def restart_service(service: str) -> dict:
    """Restart a service. DISRUPTIVE: drops in-flight requests.

    Only use this when you have identified a cause that a restart addresses,
    and never as a first step.
    """
    if service not in fake_infra.SERVICES:
        return {"error": f"unknown service {service!r}",
                "known_services": fake_infra.known_services()}
    fake_infra.ACTION_LOG.append({"action": "restart", "service": service})
    return {"service": service, "result": "restart initiated (simulated)",
            "note": "no real infrastructure was touched"}


# A plain list, so scripts can write `from local_tools import TOOLS`.
TOOLS = [get_service_status, get_recent_deploys, get_error_logs, restart_service]
