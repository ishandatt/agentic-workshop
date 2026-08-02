"""Step 0: talk to the LLM with NOTHING but HTTP.

Before any framework, see what an LLM API actually is: a POST request that
returns text plus token counts. Every framework you will ever use sits on top
of exactly this.

Run:  python modules/01-foundations/02_raw_ollama.py
"""

import sys
from pathlib import Path

# --- Making `import common...` work -----------------------------------------
# Python imports from directories listed in `sys.path`. Running this file puts
# its own folder (modules/01-foundations/) on that list, not the project root,
# so `from common.config import ...` would fail with ModuleNotFoundError.
#
#   __file__      this file
#   .resolve()    absolute path, symlinks followed
#   .parents[2]   up two levels: [0]=01-foundations, [1]=modules, [2]=root
#   insert(0, …)  put it first so our code takes priority
#
# Every script under modules/ starts with this exact line.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx                        # HTTP client
from rich.console import Console    # coloured terminal output

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.display import show_messages, show_response
from common.metrics import session_report, track

console = Console()

# UPPER_CASE marks this as a constant by convention. Change it and re-run to
# see the token counts move.
PROMPT = "In two sentences: what is an AI agent, versus a plain chatbot?"

console.print(f"[bold]POST {OLLAMA_BASE_URL}/api/chat[/bold]  model={CHAT_MODEL}\n")

# Show the messages array before sending it. This is the entire "AI call" —
# worth seeing in full at least once, since every framework you ever use is
# building exactly this underneath.
show_messages([{"role": "user", "content": PROMPT}])

# `with track(...) as m:` starts a timer, and stops it when the indented block
# ends — however it ends. `m` is the tracker object we report usage to.
# In Python, indentation IS the block: there are no braces.
with track("raw-http-chat") as m:
    resp = httpx.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        # `json=` serialises this dictionary to a JSON body and sets the
        # Content-Type header. This dict is the ENTIRE "AI call" — a model
        # name, a list of messages, and a streaming flag.
        json={
            "model": CHAT_MODEL,
            # Each message has a role ("user", "assistant", "system") and
            # content. This role/content shape is near-universal across
            # providers; LangChain's message classes wrap this same idea.
            "messages": [{"role": "user", "content": PROMPT}],
            "stream": False,   # False = one complete reply, not token-by-token
        },
        # Generous, because the first call also loads the model into memory.
        timeout=120,
    )
    # Raise an exception on any 4xx/5xx response. Without this, an error page
    # would be silently parsed as if it were a successful answer.
    resp.raise_for_status()
    # Parse the JSON body into a Python dict.
    data = resp.json()
    # Ollama reports token usage in these fields. `.get(key, 0)` returns 0
    # rather than raising if the key is absent.
    m.record_raw(
        input_tokens=data.get("prompt_eval_count", 0),
        output_tokens=data.get("eval_count", 0),
    )

# Reach into the nested response structure to get the generated text.
console.print(f"\n[cyan]{data['message']['content']}[/cyan]\n")

console.print("[bold]The raw response also contains:[/bold]")
# Loop over a tuple of keys and print each one's value.
for key in ("prompt_eval_count", "eval_count", "total_duration", "load_duration"):
    console.print(f"  {key} = {data.get(key)}")

console.print(
    "\n[dim]prompt_eval_count = input tokens, eval_count = output tokens,\n"
    "durations are nanoseconds. Every provider exposes some version of this —\n"
    "it is the raw material for ALL cost and latency tracking.[/dim]\n"
)

# Print the cumulative table for everything tracked in this process.
session_report()
