"""Bonus 5, step 2: delegation as a field, not as a graph.

The previous script rebuilt module 3's agent in ADK and concluded the
differences were ergonomic. This is the one place ADK offers something we did
not assemble by hand: `sub_agents`.

The shape is a coordinator that owns no tools and does no work, and two
specialists that do:

    incident_coordinator          decides who should handle this
      |- triage_specialist        no tools; classifies severity from the text
      |- investigation_specialist the three read-only ops tools from module 3

Declaring `sub_agents=[...]` makes ADK generate a `transfer_to_agent` tool on
the coordinator automatically. So delegation is still a tool call underneath —
which is worth seeing, because it means delegation is a MODEL decision with all
the reliability that implies, not a routing table.

That is the honest lesson of this script, and a 7B model is a good way to learn
it. Run it more than once.

Run:  python modules/14-google-adk/02_sub_agents.py
"""

import asyncio
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# Reuse module 3's fake estate, exactly as 01_adk_agent.py does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03-mcp-tools"))

warnings.filterwarnings("ignore")
import litellm

litellm.suppress_debug_info = True

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import InMemoryRunner
from rich.console import Console
from rich.table import Table

from common.config import CHAT_MODEL, OLLAMA_BASE_URL

import fake_infra

console = Console()


def model() -> LiteLlm:
    """One model, three agents. Each agent gets its own adapter instance.

    Nothing stops you giving the coordinator a large model and the specialists
    small ones — which is the usual production reason to split agents at all,
    and something a single-agent design cannot express.
    """
    return LiteLlm(model=f"ollama_chat/{CHAT_MODEL}", api_base=OLLAMA_BASE_URL)


# --- Tools, unchanged from step 1 ---------------------------------------------
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


# --- The specialists ----------------------------------------------------------
# `description` is not documentation. It is what the COORDINATOR reads when it
# decides where to send the work — the same job a tool docstring does in module
# 3. Write it for the model, not for a human reviewer.
triage_specialist = LlmAgent(
    name="triage_specialist",
    model=model(),
    description=(
        "Classifies an alert's severity and writes a one-line summary. "
        "Use for questions about how BAD something is. Has no tools and cannot "
        "look anything up."
    ),
    instruction=(
        "You classify production alerts. Given the alert text alone, state a "
        "severity of low, medium, high or critical, and one sentence saying "
        "why. Do not speculate about root cause — that is not your job."
    ),
)

investigation_specialist = LlmAgent(
    name="investigation_specialist",
    model=model(),
    description=(
        "Investigates root cause using read-only ops tools: service status, "
        "recent deploys, error logs. Use for questions about WHY something is "
        "happening."
    ),
    instruction=(
        "You investigate production alerts. Gather evidence with your tools "
        "before concluding: check service status, look for recent deploys, and "
        "read error logs. Then state the most likely root cause, citing the "
        "evidence you found."
    ),
    tools=[get_service_status, get_recent_deploys, get_error_logs],
)

# --- The coordinator ----------------------------------------------------------
# Note what it does NOT have: tools. Its entire job is choosing a specialist.
# ADK adds `transfer_to_agent` for it because `sub_agents` is populated.
coordinator = LlmAgent(
    name="incident_coordinator",
    model=model(),
    description="Routes incident questions to the right specialist.",
    # Note how specific this has to be about the MECHANISM, not just the
    # intent. An earlier draft said "Transfer to triage_specialist", and the
    # model duly emitted a function call named `triage_specialist` — which does
    # not exist, because the only tool here is `transfer_to_agent`. ADK raised
    # `ValueError: Tool 'triage_specialist' not found`.
    #
    # That is worth keeping in mind rather than editing away: the agent names
    # read like tool names, so a small model conflates them. Naming the tool
    # and its argument explicitly is what fixes it.
    instruction=(
        "You coordinate incident response. You do not answer questions "
        "yourself and you have no tools of your own except transfer_to_agent.\n"
        "To delegate, call the function `transfer_to_agent` with the argument "
        "`agent_name` set to one of these exact strings:\n"
        "  'triage_specialist'         — for questions about severity or impact\n"
        "  'investigation_specialist'  — for questions about root cause, or "
        "anything needing service status, deploys or logs\n"
        "Never call a function named after an agent. The only function you may "
        "call is transfer_to_agent. Transfer immediately, and do not ask "
        "clarifying questions."
    ),
    sub_agents=[triage_specialist, investigation_specialist],
)

runner = InMemoryRunner(agent=coordinator, app_name="workshop-multi")

# Two prompts that should land in different places. The first is a judgement
# call on text; the second cannot be answered without looking things up.
PROMPTS = [
    ("severity question",
     "Alert: payment-service error rate jumped from 0.3% to 12.4% over the last "
     "10 minutes. How severe is this?"),
    ("root cause question",
     "Alert: payment-service error rate jumped from 0.3% to 12.4% over the last "
     "10 minutes. Why is it happening?"),
]


async def run_one(label: str, prompt: str) -> dict:
    """Run one prompt through the coordinator and report where it ended up."""
    console.rule(f"[bold]{label}[/bold]")
    console.print(f"[dim]{prompt}[/dim]\n")

    # ADK raises rather than recovering when the model names a function that
    # does not exist, and with a 7B coordinator that is a live possibility on
    # any given run. Catching it here turns a crashed script into a reported
    # outcome — which is the honest result, and the one this module is about.
    try:
        events = await runner.run_debug(prompt)
    except ValueError as exc:
        if "not found" not in str(exc):
            raise
        bad = str(exc).split("'")[1] if "'" in str(exc) else "?"
        console.print(f"  [red]✘ routing failed[/red] — the model called a "
                      f"function named [bold]{bad}[/bold], which does not exist")
        console.print("  [dim]The only tool a coordinator has is "
                      "`transfer_to_agent`. The model conflated the agent name\n"
                      "  with a tool name — a hallucinated function call, and "
                      "ADK treats that as fatal.[/dim]\n")
        return {"label": label, "transfers": [], "tools": [],
                "answered_by": "[red]hallucinated tool[/red]"}

    transfers: list[str] = []
    tool_calls: list[str] = []
    authors: list[str] = []
    final_text = None

    for event in events:
        author = getattr(event, "author", "?")
        for part in (getattr(event.content, "parts", None) or []):
            if getattr(part, "function_call", None):
                call = part.function_call
                if call.name == "transfer_to_agent":
                    # The delegation itself, visible as an ordinary tool call.
                    target = dict(call.args).get("agent_name", "?")
                    transfers.append(target)
                    console.print(f"  [yellow]⇢ {author} transfers to[/yellow] "
                                  f"[bold]{target}[/bold]")
                else:
                    tool_calls.append(call.name)
                    console.print(f"  [magenta]→ {author} calls[/magenta] "
                                  f"[bold]{call.name}[/bold]({dict(call.args)})")
            elif getattr(part, "function_response", None):
                result = str(part.function_response.response)
                console.print(f"  [green]← returned[/green] [dim]{result[:100]}[/dim]")
            elif getattr(part, "text", None) and part.text.strip():
                final_text = part.text.strip()
                # Track who spoke last. A transfer means the answer should come
                # from a specialist; if the coordinator's name shows up here,
                # it answered a question it was told not to answer.
                authors.append(author)

    console.print(f"\n[cyan]{(final_text or '(no text produced)')[:600]}[/cyan]\n")
    return {"label": label, "transfers": transfers, "tools": tool_calls,
            # Fall back to whoever the last event came from, so this column is
            # never a bare "?" when the run did in fact produce events.
            "answered_by": authors[-1] if authors
                           else (transfers[-1] if transfers else coordinator.name)}


async def main():
    results = []
    for label, prompt in PROMPTS:
        results.append(await run_one(label, prompt))

    console.rule("[bold]Where the work went[/bold]")
    table = Table()
    table.add_column("prompt")
    table.add_column("transferred to")
    table.add_column("tools used", justify="right")
    table.add_column("answered by")
    for r in results:
        table.add_row(
            r["label"],
            " → ".join(r["transfers"]) or "[dim]nobody — coordinator answered[/dim]",
            str(len(r["tools"])),
            r["answered_by"],
        )
    console.print(table)


asyncio.run(main())

console.print(
    "\n[bold]Read that table sceptically — both rows are usually wrong.[/bold]\n"
    "[dim]The intended routing is severity -> triage_specialist with no tools,\n"
    "and root cause -> investigation_specialist. On qwen2.5:7b you will most\n"
    "likely see neither.\n\n"
    "[bold]Row 1: the coordinator answers it itself.[/bold] Its instruction says, in\n"
    "so many words, that it does not answer questions. It answers anyway —\n"
    "because it CAN, and a severity judgement needs no tools. Module 7's lesson\n"
    "arriving in a new costume: an instruction is a request, not a control. If\n"
    "the coordinator must never answer, do not ask it nicely — give it no way\n"
    "to reply, or check the author of the final event and reject the run.\n\n"
    "[bold]Row 2: it routes to the wrong specialist, which then re-routes.[/bold]\n"
    "The root-cause question goes to triage_specialist first. That agent reads\n"
    "its own instruction, sees the question is not its job, and transfers on to\n"
    "investigation_specialist — because in ADK a sub-agent gets\n"
    "`transfer_to_agent` too, and can hand work sideways.\n\n"
    "The answer is right and it cost an extra model call to get there. Self-\n"
    "correcting chains are a genuine strength of multi-agent designs and a\n"
    "genuine way to burn tokens; both are true at once.\n\n"
    "[bold]And notice it is the same every run.[/bold] Unreliable does not mean\n"
    "random — this is a systematic bias, which is the more dangerous kind. A\n"
    "noisy router announces itself the first time you test it. A consistently\n"
    "wrong one passes your smoke test and fails on the input you never tried.[/dim]\n"
)

comparison = Table(title="Delegation, three ways")
comparison.add_column("")
comparison.add_column("how you express it")
comparison.add_column("who decides the route")
comparison.add_row("hand-written loop (mod 3)", "an if-statement you write", "you, deterministically")
comparison.add_row("LangGraph (mod 8/9)", "a conditional edge on state", "you, deterministically")
comparison.add_row("ADK sub_agents", "a field on the parent agent", "the model, per request")
console.print(comparison)

console.print(
    "\n[dim]Neither column is better. They answer different questions.\n\n"
    "Module 9's pipeline routes on a POLICY decision — settlement window, action\n"
    "whitelist — and that must never be a model decision, which is why it is a\n"
    "conditional edge in code. Routing an open-ended question to the specialist\n"
    "best placed to answer it is the opposite: there is no rule to write, and\n"
    "asking the model is the only option on the table.\n\n"
    "The failure mode to avoid is using a model router for something you could\n"
    "have written an if-statement for.[/dim]\n"
)
