"""Bonus 4, step 2: prove the claim. Change the prefix, change nothing else.

The previous script asserted that `ollama_chat/qwen2.5:7b` becomes
`openai/gpt-4o-mini` and the rest of your code is untouched. Asserting it is
cheap. This script demonstrates it, including the part people are right to be
suspicious of: what happens when you point at a provider you have no key for.

The answer is the interesting bit. You get an **authentication** error — not a
TypeError, not "unsupported provider", not a different function signature. Your
code was already correct; the only thing missing was a credential. That is what
"provider independent" actually buys, stated precisely.

We also run a streamed call through the same interface, because a translation
layer that only handles the simple one-shot case is not much of a layer — and
because module 1 made a point about when usage data becomes available that is
worth seeing survive the abstraction.

Run:  python modules/13-litellm/02_swap_the_prefix.py
"""

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

warnings.filterwarnings("ignore")
import litellm

litellm.suppress_debug_info = True

from rich.console import Console
from rich.table import Table

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

console = Console()

MESSAGES = [
    {"role": "system", "content": "You are a concise SRE assistant."},
    {"role": "user", "content": "In one sentence, what is a connection pool?"},
]

LOCAL_MODEL = f"ollama_chat/{CHAT_MODEL}"

# --- 1. The only thing that changes -------------------------------------------
console.rule("[bold]One call signature, many backends[/bold]")

# Every row below is a valid first argument to the SAME litellm.completion()
# call. Nothing else in the call differs — same messages, same temperature, same
# response handling. That is the entire product.
PROVIDERS = [
    (LOCAL_MODEL,                      "Ollama, this laptop",      "none"),
    ("openai/gpt-4o-mini",             "OpenAI",                   "OPENAI_API_KEY"),
    ("anthropic/claude-sonnet-4-5",    "Anthropic",                "ANTHROPIC_API_KEY"),
    ("bedrock/anthropic.claude-haiku-4-5-20251001-v1:0", "AWS Bedrock", "AWS credentials"),
    ("gemini/gemini-2.0-flash",        "Google AI Studio",         "GEMINI_API_KEY"),
]

table = Table()
table.add_column("model string")
table.add_column("who answers")
table.add_column("credential needed")
for model, who, cred in PROVIDERS:
    table.add_row(model, who, cred)
console.print(table)

console.print(
    "\n[dim]Read the first column as one string that varies and a call that does\n"
    "not. In a real codebase that string comes from config, so 'which provider'\n"
    "stops being a code question.[/dim]\n"
)

# --- 2. The one we can actually call ------------------------------------------
console.rule("[bold]Calling the local one[/bold]")

with track("litellm-swap-local") as m:
    local = litellm.completion(
        model=LOCAL_MODEL,
        messages=MESSAGES,
        api_base=OLLAMA_BASE_URL,
        temperature=0.1,
    )
    m.record_raw(
        input_tokens=local.usage.prompt_tokens,
        output_tokens=local.usage.completion_tokens,
    )

console.print(f"[cyan]{local.choices[0].message.content.strip()}[/cyan]\n")

# --- 3. The one we cannot, and why the failure is the point -------------------
console.rule("[bold]Calling a hosted one with no key[/bold]")

console.print(
    "[dim]Identical call. Only the model string changed, and this workshop has\n"
    "no API keys — so we expect this to fail. Watch HOW it fails.[/dim]\n"
)

try:
    # Deliberately no api_key and no api_base. This is the same function with
    # the same arguments; only the first string is different.
    litellm.completion(
        model="openai/gpt-4o-mini",
        messages=MESSAGES,
        temperature=0.1,
    )
    console.print("[yellow]That succeeded — you have an OPENAI_API_KEY set in your "
                  "environment.[/yellow]\n")
except Exception as exc:
    # `type(exc).__name__` is the class name: AuthenticationError,
    # APIConnectionError, and so on.
    console.print(f"  raised [bold red]{type(exc).__name__}[/bold red]")
    console.print(f"  [dim]{str(exc)[:180]}[/dim]\n")

console.print(
    "[dim]That is an AUTHENTICATION failure, and the distinction matters more\n"
    "than it looks. It is not a TypeError, not 'unsupported provider', not a\n"
    "different function to call. LiteLLM built a correct OpenAI request, sent\n"
    "it, and OpenAI declined to serve an anonymous caller.\n\n"
    "So the honest summary of portability: your CODE is already portable. What\n"
    "is not portable is credentials, cost, latency, and behaviour — and only\n"
    "the first of those has an error message.[/dim]\n"
)

# --- 4. Streaming, through the same interface ---------------------------------
console.rule("[bold]Streaming[/bold]")

console.print("[dim]Same function, `stream=True`. Chunks arrive in the OpenAI\n"
              "streaming shape whatever the backend actually emitted.[/dim]\n")

chunks = []
with track("litellm-swap-stream") as m:
    for chunk in litellm.completion(
        model=LOCAL_MODEL,
        messages=[{"role": "user", "content": "Count from 1 to 5, numbers only."}],
        api_base=OLLAMA_BASE_URL,
        temperature=0.0,
        stream=True,
        # Ask the provider to report usage on the final chunk. Without this,
        # most OpenAI-compatible streams carry no token counts at all.
        stream_options={"include_usage": True},
    ):
        chunks.append(chunk)
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            console.print(delta, end="")
    console.print()

    # Module 1 made this point about LangChain streaming and it survives the
    # translation: usage exists only once generation has stopped, so it can
    # only ever be on the LAST chunk. Search backwards for it.
    usage = next((getattr(c, "usage", None) for c in reversed(chunks)
                  if getattr(c, "usage", None)), None)
    if usage:
        m.record_raw(input_tokens=usage.prompt_tokens,
                     output_tokens=usage.completion_tokens)

console.print(
    f"\n[dim]{len(chunks)} chunks. Usage found on the final chunk: "
    f"{'yes' if usage else 'no — this backend does not report it while streaming'}.\n\n"
    "Same lesson as module 1: you cannot enforce a token budget mid-stream from\n"
    "usage data, because the number does not exist until the model stops.[/dim]\n"
)

# --- 5. What the abstraction does not flatten ---------------------------------
console.rule("[bold]What a common interface cannot give you[/bold]")

leaks = Table()
leaks.add_column("differs by provider")
leaks.add_column("what the shared interface does about it")
leaks.add_row("tool-calling reliability", "nothing — same API, wildly different hit rates")
leaks.add_row("structured output", "nothing — module 2 measured how differently Ollama behaves")
leaks.add_row("prompt caching", "provider-specific parameters, passed through or unsupported")
leaks.add_row("reasoning modes", "provider-specific, arrives late in any adapter")
leaks.add_row("the actual answer", "nothing at all — this is the important row")
console.print(leaks)

console.print(
    "\n[dim]The last row is the one to leave with. Swapping providers behind an\n"
    "identical interface changes BEHAVIOUR, and nothing in this file would tell\n"
    "you. Module 6's evals are the only thing that will.\n\n"
    "'The code runs against both' is not 'it works against both'.[/dim]\n"
)

session_report()
