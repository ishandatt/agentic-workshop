"""The same agent as 01_adk_agent.py, in the layout `adk web` insists on.

This file exists because of a constraint, and the constraint is the lesson.

Every other script in this workshop is a flat, numbered file you run with
`python modules/NN-name/NN_thing.py`. ADK's tooling — `adk web` and `adk run` —
cannot see files like that. It scans a directory for SUBDIRECTORIES that each
contain an `agent.py` exposing a module-level `root_agent`, and it reports what
it does not find as an empty list rather than an error. Point `adk web` at a
folder of plain scripts and you get a UI with an empty dropdown and no clue why.

So the price of ADK's UI is that your project takes the shape ADK expects. That
is not a criticism — every framework does some version of this, and it is worth
naming out loud, because framework adoption costs are usually paid in layout and
conventions rather than in code.

Two things follow from that, and both are worth noticing:

  * this file duplicates the agent from 01_adk_agent.py rather than importing
    it. It has to: `01_adk_agent.py` starts with a digit (not a legal module
    name) and runs its whole demo at import time, which is the right shape for a
    teaching script and the wrong shape for a library.
  * the tools below are the same three read-only functions, over the same
    `fake_infra` estate module 3 built. Still no `restart_service`.

You do not run this file. See the live experiment in docs/14-google-adk.md.
"""

import sys
from pathlib import Path

# parents[3] is the repo root: sre_assistant/ -> 14-google-adk/ -> modules/ -> .
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
# Reuse module 3's fake estate rather than inventing a third one.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "03-mcp-tools"))

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from common.config import CHAT_MODEL, OLLAMA_BASE_URL

import fake_infra


def get_service_status(service: str) -> dict:
    """Get current health for a service: status, error rate, latency, replicas.

    Use this first when investigating any alert.
    """
    if service not in fake_infra.SERVICES:
        return {"error": f"unknown service {service!r}",
                "known_services": fake_infra.known_services()}
    return {"service": service, **fake_infra.SERVICES[service]}


def get_recent_deploys(service: str) -> dict:
    """List recent deployments to a service, newest first, with commit messages.

    Use this when a problem started suddenly — most incidents follow a change.
    """
    return {"service": service, "deploys": fake_infra.DEPLOYS.get(service, [])}


def get_error_logs(service: str, limit: int = 5) -> dict:
    """Get the most recent error and warning log lines for a service.

    Use this to confirm or kill a hypothesis.
    """
    return {"service": service, "lines": fake_infra.ERROR_LOGS.get(service, [])[:limit]}


# The name `root_agent` is not a style choice — it is the symbol ADK's tooling
# looks for. Rename it and the dropdown goes empty again.
root_agent = LlmAgent(
    name="sre_assistant",
    model=LiteLlm(model=f"ollama_chat/{CHAT_MODEL}", api_base=OLLAMA_BASE_URL),
    description="Investigates production alerts using read-only ops tools.",
    instruction=(
        "You are an SRE assistant investigating a production alert. "
        "Gather evidence with your tools before concluding: check service "
        "status, look for recent deploys, and read error logs. "
        "Then state the most likely root cause, citing the evidence."
    ),
    tools=[get_service_status, get_recent_deploys, get_error_logs],
)
