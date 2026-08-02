"""Bonus 4, step 1: one interface for every model, including the local one.

Module 1 argued that LangChain earns its keep partly by normalising providers.
LiteLLM does that job and almost nothing else — it is a thin translation layer
that speaks the OpenAI API shape to about a hundred backends.

Which makes it a different kind of tool from LangChain, and worth understanding
separately:

    LangChain   an application framework — prompts, chains, tools, agents
    LiteLLM     a model adapter — one call signature, many providers

You can use either, both, or neither. This workshop's agents use LangChain; the
next module's agent uses Google's ADK; both of them can reach our local Ollama
model through LiteLLM without knowing anything about Ollama.

Run:  python modules/13-litellm/01_one_interface.py
"""

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# LiteLLM is chatty on import and during calls. Quieten it before anything else
# so the workshop output stays readable.
warnings.filterwarnings("ignore")
import litellm

litellm.suppress_debug_info = True

from rich.console import Console
from rich.table import Table

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

console = Console()

# The provider prefix is the whole trick. "ollama_chat/" tells LiteLLM which
# translator to use; everything after it is the model name that provider knows.
#
#   ollama_chat/qwen2.5:7b        our local model
#   openai/gpt-4o-mini            OpenAI
#   anthropic/claude-sonnet-4-5   Anthropic
#   bedrock/anthropic.claude-...  AWS Bedrock
#
# Same function call for all of them. Only this string changes.
LOCAL_MODEL = f"ollama_chat/{CHAT_MODEL}"

MESSAGES = [
    {"role": "system", "content": "You are a concise SRE assistant."},
    {"role": "user", "content": "In one sentence, what is a connection pool?"},
]

console.print(f"[bold]Calling[/bold] {LOCAL_MODEL}")
console.print("[dim]No API key. No cloud. The same call signature you would use "
              "for GPT or Claude.[/dim]\n")

with track("litellm-local") as m:
    response = litellm.completion(
        model=LOCAL_MODEL,
        messages=MESSAGES,
        api_base=OLLAMA_BASE_URL,
        temperature=0.1,
    )
    # LiteLLM normalises usage into the OpenAI shape, whatever the backend
    # actually reported.
    m.record_raw(
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
    )

console.print(f"[cyan]{response.choices[0].message.content.strip()}[/cyan]\n")

# --- The response shape -------------------------------------------------------
console.print("[bold]What came back is an OpenAI-shaped response object[/bold]")
table = Table()
table.add_column("field")
table.add_column("value")
table.add_row("choices[0].message.content", response.choices[0].message.content.strip()[:52] + "…")
table.add_row("choices[0].finish_reason", str(response.choices[0].finish_reason))
table.add_row("model", str(response.model))
table.add_row("usage.prompt_tokens", str(response.usage.prompt_tokens))
table.add_row("usage.completion_tokens", str(response.usage.completion_tokens))
console.print(table)

console.print(
    "\n[dim]Ollama's native API returns `prompt_eval_count` and `eval_count`,\n"
    "as module 1 showed. Anthropic returns `input_tokens`/`output_tokens`.\n"
    "LiteLLM translated whichever one it got into this shape.\n\n"
    "That is the entire product: a translation layer, so your code has one\n"
    "response format to handle instead of a dozen.[/dim]\n"
)

# --- Cost tracking, for free --------------------------------------------------
console.rule("[bold]It knows what things cost[/bold]")

# LiteLLM ships a cost table for hosted models. Local models cost nothing, so
# it reports 0.0 — which is correct and also why our own metrics helper exists:
# we want the REFERENCE cost, "what this would cost on a cloud API".
actual = litellm.completion_cost(completion_response=response)
console.print(f"  actual cost of that call: [green]${actual:.6f}[/green] "
              f"[dim](local inference is free)[/dim]")

# What the identical call would have cost elsewhere. `cost_per_token` looks up
# the published price for a model name without calling anything.
console.print("\n[bold]The same tokens on hosted models:[/bold]")
compare = Table()
compare.add_column("model")
compare.add_column("input $/1M", justify="right")
compare.add_column("output $/1M", justify="right")
compare.add_column("this call", justify="right")

for name in ("gpt-4o-mini", "gpt-4o", "anthropic.claude-haiku-4-5-20251001-v1:0"):
    try:
        info = litellm.get_model_info(name)
        in_rate = info.get("input_cost_per_token") or 0
        out_rate = info.get("output_cost_per_token") or 0
        this_call = (response.usage.prompt_tokens * in_rate
                     + response.usage.completion_tokens * out_rate)
        compare.add_row(name, f"${in_rate * 1_000_000:.2f}",
                        f"${out_rate * 1_000_000:.2f}", f"${this_call:.6f}")
    except Exception as e:
        compare.add_row(name, "—", "—", f"[dim]{type(e).__name__}[/dim]")
console.print(compare)

console.print(
    "\n[dim]That table is why LiteLLM shows up in cost-conscious teams. It keeps\n"
    "a price list for hosted models, so 'what would this pipeline cost on GPT-4o'\n"
    "is a lookup rather than a spreadsheet.\n\n"
    "It is the same idea as our REF_PRICE_* values in .env, with someone else\n"
    "maintaining the numbers.[/dim]\n"
)

console.print(
    "[bold]When to reach for it[/bold]\n"
    "[dim]  * you support more than one provider, or expect to\n"
    "  * you want one place to change models, not twenty call sites\n"
    "  * you need cost accounting across providers\n"
    "  * a library you use only speaks OpenAI (this is very common)\n\n"
    "  And when not to: a single provider, a single call shape, no plans to\n"
    "  change. Then it is a dependency that buys you nothing.[/dim]\n"
)

session_report()
