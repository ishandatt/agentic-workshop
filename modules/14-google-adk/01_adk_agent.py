"""Bonus 5: the same agent in Google's ADK, still running entirely locally.

Module 3 built an agent loop by hand, then rebuilt it as a LangGraph graph.
This is a third framework doing the same job, so you can see what is genuinely
common to agent frameworks and what is one vendor's opinion.

**Agent Development Kit** is Google's open-source agent framework. It defaults
to Gemini, which would mean an API key and a cloud call — both forbidden here.
So we point it at our local Ollama model through LiteLLM (bonus 4), which is
exactly the job LiteLLM exists for.

What is the same as LangGraph:
  * tools are plain Python functions with docstrings
  * the loop is call model -> maybe call tools -> feed results back -> repeat
  * you get an event stream you can inspect

What is different:
  * the agent is a declarative object, not a graph you wire yourself
  * sessions, state and artifacts are built in rather than assembled
  * multi-agent delegation is a first-class field (`sub_agents`)

Run:  python modules/14-google-adk/01_adk_agent.py
"""

import asyncio
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# Reuse module 3's fake estate rather than inventing a second one.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03-mcp-tools"))

warnings.filterwarnings("ignore")
import litellm

litellm.suppress_debug_info = True

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import InMemoryRunner
from rich.console import Console

from common.config import CHAT_MODEL, OLLAMA_BASE_URL

import fake_infra

console = Console()


# --- Tools --------------------------------------------------------------------
# Identical in spirit to module 3's: a plain function, type hints, and a
# docstring the model reads. ADK builds the schema from the signature, exactly
# as LangChain's @tool and FastMCP's @server.tool() do.
#
# Note there is no decorator at all here. ADK takes the bare functions.
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


# --- The agent ----------------------------------------------------------------
# `LiteLlm` is ADK's adapter for anything that is not a Google model. The string
# is LiteLLM's, so "ollama_chat/qwen2.5:7b" means exactly what it did in the
# previous module.
agent = LlmAgent(
    name="sre_assistant",
    model=LiteLlm(model=f"ollama_chat/{CHAT_MODEL}", api_base=OLLAMA_BASE_URL),
    description="Investigates production alerts using read-only ops tools.",
    instruction=(
        "You are an SRE assistant investigating a production alert. "
        "Gather evidence with your tools before concluding: check service "
        "status, look for recent deploys, and read error logs. "
        "Then state the most likely root cause, citing the evidence."
    ),
    # Only read-only tools. Module 9 learned this the hard way: an agent given a
    # mutating tool will eventually use it, and a prompt saying otherwise is not
    # a control.
    tools=[get_service_status, get_recent_deploys, get_error_logs],
)

# InMemoryRunner executes the agent and keeps sessions in memory. ADK also ships
# database-backed session services — the same concern LangGraph's checkpointer
# handles in module 8.
runner = InMemoryRunner(agent=agent, app_name="workshop")

PROMPT = ("Alert: payment-service error rate jumped from 0.3% to 12.4% over the "
          "last 10 minutes. What is going on?")

console.print(f"[bold]Agent:[/bold] {agent.name}")
console.print(f"[bold]Model:[/bold] ollama_chat/{CHAT_MODEL} [dim](local, via LiteLLM)[/dim]")
console.print(f"[bold]Tools:[/bold] {', '.join(t.__name__ for t in [get_service_status, get_recent_deploys, get_error_logs])}\n")
console.print(f"[bold]Prompt:[/bold] {PROMPT}\n")


async def main():
    """ADK is async throughout, so the whole run sits inside one asyncio.run.

    That is a real difference from our LangChain code, which is synchronous by
    default — and a reminder that "which framework" is partly a question about
    what colour your functions have to be.
    """
    console.rule("[bold]Events[/bold]")

    events = await runner.run_debug(PROMPT)

    final_text = None
    for event in events:
        author = getattr(event, "author", "?")
        for part in (getattr(event.content, "parts", None) or []):
            # An event carries parts: a tool request, a tool result, or text.
            if getattr(part, "function_call", None):
                call = part.function_call
                console.print(f"  [magenta]→ calling[/magenta] [bold]{call.name}[/bold]"
                              f"({dict(call.args)})")
            elif getattr(part, "function_response", None):
                result = str(part.function_response.response)
                console.print(f"  [green]← returned[/green] [dim]{result[:110]}[/dim]")
            elif getattr(part, "text", None) and part.text.strip():
                final_text = part.text.strip()

    console.rule("[bold]Answer[/bold]")
    console.print(f"[cyan]{(final_text or '(no text produced)')[:900]}[/cyan]\n")


asyncio.run(main())

console.print(
    "[bold]Three frameworks, one shape.[/bold]\n"
    "[dim]Compare this with module 3. The hand-written loop, the LangGraph\n"
    "graph and this ADK agent all do the same thing: offer the model some\n"
    "tools, run whatever it asks for, feed results back, repeat until it stops\n"
    "asking. The differences are ergonomic, not conceptual.\n\n"
    "Which is the useful takeaway from learning a second framework — it makes\n"
    "the underlying pattern visible, and the pattern is what transfers.[/dim]\n"
)

console.print(
    "[bold]What ADK gives you that we assembled by hand[/bold]\n"
    "[dim]  sessions & state     built in, with pluggable storage backends\n"
    "  artifacts            file outputs handled as first-class objects\n"
    "  sub_agents           delegation between agents as a declared field\n"
    "  adk web              a local UI for stepping through runs\n"
    "  callbacks            hooks before/after model and tool calls\n\n"
    "[/dim][bold]And what it costs[/bold]\n"
    "[dim]  a Google-shaped view of the world, async everywhere, and a\n"
    "  dependency that pulls in google-genai whether or not you use Gemini.\n\n"
    "  Note also what it does NOT change: the model is still qwen2.5:7b, so the\n"
    "  answers are the same quality as module 3's. Frameworks move the\n"
    "  ergonomics, not the intelligence.[/dim]\n"
)
