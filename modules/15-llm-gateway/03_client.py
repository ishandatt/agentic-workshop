"""Bonus 6, step 3: be the client. Change a base URL, change nothing else.

The gateway's whole adoption argument is that it speaks the OpenAI API, so
existing code points at it by changing one setting. That argument is easy to
make and worth checking, because it is the difference between a gateway teams
adopt and a gateway teams route around.

So this script is a client. It never imports the gateway, shares no code with
it, and knows only a URL and a key — which is exactly the relationship a real
application has.

Start the gateway in another terminal first:

    python modules/15-llm-gateway/02_gateway.py

Then:  python modules/15-llm-gateway/03_client.py
"""

import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

warnings.filterwarnings("ignore")
import litellm

litellm.suppress_debug_info = True

from rich.console import Console
from rich.table import Table

console = Console()

GATEWAY = "http://127.0.0.1:4000"

# The two keys 02_gateway.py issues. Note what a client holds: a key the GATEWAY
# minted, never a provider credential. That is the point of virtual keys — this
# file could be committed to a public repo and no provider account is exposed.
SRE_KEY = "sk-team-sre"              # may use: chat, chat-fast   budget $5.00
ANALYTICS_KEY = "sk-team-analytics"  # may use: chat-fast only    budget $0.50

MESSAGES = [{"role": "user", "content": "In one sentence, what is a connection pool?"}]

# --- Is it up? ----------------------------------------------------------------
try:
    health = httpx.get(f"{GATEWAY}/health", timeout=5.0).json()
except Exception as exc:
    console.print(f"[red]No gateway at {GATEWAY}[/red] ({type(exc).__name__})\n")
    console.print("Start it in another terminal:\n"
                  "  [bold]python modules/15-llm-gateway/02_gateway.py[/bold]\n")
    sys.exit(1)

console.print(f"[bold]Gateway up[/bold] at {GATEWAY} · "
              f"aliases: {', '.join(health['aliases'])}\n")

# --- 1. Any HTTP client ---------------------------------------------------------
console.rule("[bold]1. A plain HTTP client[/bold]")

console.print("[dim]No SDK, no framework. A POST to /v1/chat/completions with a\n"
              "bearer token — the OpenAI wire format, which is why this works.[/dim]\n")

response = httpx.post(
    f"{GATEWAY}/v1/chat/completions",
    headers={"Authorization": f"Bearer {SRE_KEY}"},
    json={"model": "chat", "messages": MESSAGES, "temperature": 0.1},
    timeout=120.0,
)
body = response.json()
console.print(f"  HTTP {response.status_code}")

# Check the status before reaching into the body. `/health` answering only
# proved the gateway process is up — it says nothing about Ollama behind it,
# and a gateway that cannot reach its upstream returns 502 with a `detail`
# field and no `choices`. Indexing straight in would raise KeyError('choices')
# and bury the actual problem under a traceback.
if response.status_code != 200:
    # FastAPI puts HTTPException messages in "detail".
    console.print(f"  [red]{body.get('detail', response.text)}[/red]")
    console.print("\n[dim]That is the gateway reporting an upstream failure, not a bug\n"
                  "in this script. Check Ollama is running: `ollama list`.[/dim]\n")
    sys.exit(1)

console.print(f"  [cyan]{body['choices'][0]['message']['content'].strip()}[/cyan]")
console.print(f"  [dim]served by {body['model']} · "
              f"{body['usage']['total_tokens']} tokens[/dim]\n")

console.print(
    "[dim]The client asked for 'chat'. It does not know, and cannot find out,\n"
    "which model answered — operations decides that. Compare with every other\n"
    "script in this workshop, each of which names qwen2.5:7b directly.[/dim]\n"
)

# --- 2. The same call as module 13, one string different -----------------------
console.rule("[bold]2. LiteLLM, pointed at the gateway[/bold]")

console.print(
    "[dim]Module 13 called `litellm.completion(model='ollama_chat/qwen2.5:7b',\n"
    "api_base='http://localhost:11434')`. Here is that call again with the\n"
    "provider set to `openai/` and the base URL moved. The model name is now an\n"
    "ALIAS the gateway resolves.[/dim]\n"
)

try:
    via_litellm = litellm.completion(
        # `openai/` means "speak the OpenAI protocol"; the gateway is not OpenAI
        # and does not need to be. Anything OpenAI-shaped answers to this.
        model="openai/chat",
        messages=MESSAGES,
        api_base=f"{GATEWAY}/v1",
        api_key=SRE_KEY,
        temperature=0.1,
    )
    console.print(f"  [cyan]{via_litellm.choices[0].message.content.strip()}[/cyan]")
    console.print(f"  [dim]{via_litellm.usage.prompt_tokens} in / "
                  f"{via_litellm.usage.completion_tokens} out tokens[/dim]\n")
except Exception as exc:
    console.print(f"  [yellow]{type(exc).__name__}[/yellow]: "
                  f"[dim]{str(exc)[:160]}[/dim]\n")

console.print(
    "[dim]Two settings changed: a base URL and a key. No new library, no new\n"
    "call shape, no code touched anywhere else.\n\n"
    "The same one-line change works for the OpenAI SDK\n"
    "(`OpenAI(base_url=..., api_key=...)`) and for LangChain\n"
    "(`ChatOpenAI(base_url=..., api_key=..., model='chat')`, which needs\n"
    "`langchain-openai` — deliberately not in this workshop's requirements, to\n"
    "keep the pinned stack in the state module 3 and 9 were verified against).\n\n"
    "That is the adoption argument, and it is the reason gateways speak\n"
    "somebody else's API instead of designing a better one. A gateway that\n"
    "required an SDK change would not get adopted.[/dim]\n"
)

# --- 3. The policy the client cannot talk its way out of -----------------------
console.rule("[bold]3. Policy, enforced at the door[/bold]")

# Each row is a request a client might reasonably make. Three of them are
# refused, and the refusal costs no model call at all — the gateway decides
# before it touches an upstream.
attempts = [
    ("sre asks for chat",            SRE_KEY,        "chat"),
    ("analytics asks for chat-fast", ANALYTICS_KEY,  "chat-fast"),
    ("analytics asks for chat",      ANALYTICS_KEY,  "chat"),
    ("unknown key",                  "sk-nope",      "chat"),
    ("no alias by that name",        SRE_KEY,        "chat-enormous"),
]

table = Table()
table.add_column("request")
table.add_column("status", justify="right")
table.add_column("what the gateway said")

for label, key, alias in attempts:
    r = httpx.post(
        f"{GATEWAY}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": alias, "messages": [{"role": "user", "content": "Say OK"}]},
        timeout=120.0,
    )
    if r.status_code == 200:
        detail = r.json()["choices"][0]["message"]["content"].strip()[:40]
        colour = "green"
    else:
        # FastAPI puts HTTPException messages in "detail".
        detail = str(r.json().get("detail", r.text))[:60]
        colour = "red" if r.status_code < 500 else "yellow"
    table.add_row(label, f"[{colour}]{r.status_code}[/{colour}]", detail)

console.print(table)

console.print(
    "\n[dim]Note where those refusals happened. `analytics asks for chat` never\n"
    "reached a model — no tokens, no latency, no spend. A permission checked\n"
    "after the call is an audit finding; checked before it, it is a control.\n\n"
    "Note also that the last row is a 403 rather than a 404. The gateway will\n"
    "not tell an unauthorised caller which aliases exist.[/dim]\n"
)

# --- 4. The report you cannot produce without one ------------------------------
console.rule("[bold]4. One log, all teams[/bold]")

usage = httpx.get(f"{GATEWAY}/admin/usage", timeout=10.0).json()

spend = Table()
spend.add_column("team")
spend.add_column("spent", justify="right")
spend.add_column("budget", justify="right")
for key, row in usage["spend"].items():
    spend.add_row(row["team"], f"${row['spent_usd']:.6f}", f"${row['budget_usd']:.2f}")
console.print(spend)

console.print(f"\n[bold]{usage['calls']} calls logged.[/bold] Most recent:")
console.print_json(json.dumps(usage["recent"][-1] if usage["recent"] else {}))

console.print(
    "\n[dim]Local inference is free, so every figure above is $0.000000 — and the\n"
    "shape is the lesson, not the number. Point one deployment at a hosted\n"
    "model and this table becomes the answer to 'what did we spend on LLMs last\n"
    "month, by team', which is a question most organisations cannot answer at\n"
    "all.\n\n"
    "Assembling it from twenty services that each call providers directly is the\n"
    "problem the gateway exists to solve.[/dim]\n"
)
