"""Bonus 6, step 1: routing and fallbacks, before there is a gateway at all.

Every call in this workshop names one model and hopes. That is fine on a laptop
and it is not what production looks like, where you want:

    routing     cheap model for easy work, expensive model for hard work
    fallbacks   provider is down or rate-limiting -> try another
    retries     transient failures handled once, centrally
    budgets     a hard stop before the bill surprises someone

`litellm.Router` does all of that in-process — a list of deployments and some
policy, with the same `.completion()` call on top.

Run:  python modules/15-llm-gateway/01_router.py
"""

import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

warnings.filterwarnings("ignore")
import litellm

litellm.suppress_debug_info = True
litellm.set_verbose = False

from litellm import Router
from rich.console import Console
from rich.table import Table

from common.config import CHAT_MODEL, OLLAMA_BASE_URL

console = Console()

# A deployment list. In production these would be different providers, regions
# or accounts; here we have one real model and one deliberately broken entry so
# the failure paths are demonstrable.
#
# Note `model_name` is the alias YOUR code asks for. `litellm_params.model` is
# what actually gets called. That indirection is the whole point: application
# code says "chat", operations decides what "chat" means today.
router = Router(
    model_list=[
        {
            "model_name": "chat",
            "litellm_params": {
                "model": f"ollama_chat/{CHAT_MODEL}",
                "api_base": OLLAMA_BASE_URL,
            },
        },
        {
            "model_name": "chat-broken",
            "litellm_params": {
                "model": "ollama_chat/model-that-does-not-exist",
                "api_base": OLLAMA_BASE_URL,
            },
        },
    ],
    # If "chat-broken" fails, try "chat". This is the single most valuable line
    # in a multi-provider setup.
    fallbacks=[{"chat-broken": ["chat"]}],
    num_retries=1,
)

MESSAGES = [{"role": "user", "content": "Reply with exactly one word: OK"}]


def call(alias: str) -> tuple[str, str, float]:
    started = time.perf_counter()
    response = router.completion(model=alias, messages=MESSAGES)
    return (
        response.choices[0].message.content.strip()[:20],
        response.model,                       # which deployment actually served it
        (time.perf_counter() - started) * 1000,
    )

console.rule("[bold]Aliases and indirection[/bold]")
text, served_by, ms = call("chat")
console.print(f"  asked for [cyan]chat[/cyan] → served by [green]{served_by}[/green] "
              f"({ms:.0f}ms): {text!r}")
console.print(
    "\n[dim]Your code asked for 'chat'. It has no idea which model answered, and\n"
    "that is the design — swapping qwen2.5 for llama3.1, or for GPT-4o, is a\n"
    "config change in one place rather than a code change everywhere.[/dim]\n"
)

console.rule("[bold]Fallbacks[/bold]")
text, served_by, ms = call("chat-broken")
console.print(f"  asked for [cyan]chat-broken[/cyan] → served by [green]{served_by}[/green] "
              f"({ms:.0f}ms): {text!r}")
console.print(
    "\n[dim]The requested deployment does not exist. The router tried it, failed,\n"
    "and fell through to a working one — and the caller got an answer rather\n"
    "than an exception.\n\n"
    "Notice the latency: the failure was paid for before the fallback ran.\n"
    "Fallbacks buy availability with latency, which is usually the right trade\n"
    "and is never a free one.[/dim]\n"
)

# --- Budgets ------------------------------------------------------------------
console.rule("[bold]Budgets[/bold]")
console.print(
    "[dim]Router can enforce spend limits per deployment. Ours is local and free,\n"
    "so the limit never trips — but the mechanism is worth seeing, because it is\n"
    "the difference between 'we should watch the spend' and 'it cannot exceed\n"
    "this'.[/dim]\n"
)

budgeted = Router(
    model_list=[{
        "model_name": "chat",
        "litellm_params": {"model": f"ollama_chat/{CHAT_MODEL}",
                           "api_base": OLLAMA_BASE_URL},
        # A real deployment would carry max_budget and budget_duration here.
        "model_info": {"id": "local-qwen", "max_budget": 0.05},
    }],
)
r = budgeted.completion(model="chat", messages=MESSAGES)
spend = litellm.completion_cost(completion_response=r)
console.print(f"  call cost: [green]${spend:.6f}[/green] against a $0.05 cap "
              f"[dim](local inference is free, so this never trips)[/dim]\n")

# --- Summary ------------------------------------------------------------------
table = Table(title="What a router adds over a bare call")
table.add_column("capability")
table.add_column("without it")
table.add_column("with it")
table.add_row("model choice", "hard-coded at every call site", "one alias, config decides")
table.add_row("provider outage", "your feature is down", "falls through to another")
table.add_row("transient errors", "each caller retries differently", "one retry policy")
table.add_row("spend", "discovered on the invoice", "capped per deployment")
console.print(table)

console.print(
    "\n[dim]All of that is still IN YOUR PROCESS. Every service that wants these\n"
    "properties has to import the router and be configured with the same keys,\n"
    "the same limits and the same model list — and they will drift.\n\n"
    "Pulling it out into a service everyone shares is the next script, and it is\n"
    "what people mean by an LLM gateway.[/dim]\n"
)
