"""Bonus 6, step 2: an LLM gateway — the router, pulled out into a service.

A gateway is one HTTP service that every application calls instead of calling
model providers directly. It centralises the things you do not want scattered:

    keys        providers' keys live here, not in twenty repos
    routing     "chat" means whatever operations says it means today
    budgets     per team, per key, enforced rather than monitored
    logging     one place that sees every call anyone makes
    fallbacks   one policy, applied to everyone

Because it speaks the OpenAI API, existing clients point at it by changing a
base URL — no code changes, which is the property that makes adoption possible.

This one is about a hundred lines. Production options (LiteLLM Proxy, Portkey,
Kong AI Gateway, cloud equivalents) do far more, but they are this shape, and
having built the small version you will know what they are doing.

Run it:
    python modules/15-llm-gateway/02_gateway.py

Then, from anything that speaks OpenAI:
    curl -s http://127.0.0.1:4000/v1/chat/completions \\
      -H 'Authorization: Bearer sk-team-sre' \\
      -H 'Content-Type: application/json' \\
      -d '{"model":"chat","messages":[{"role":"user","content":"Say OK"}]}' | python3 -m json.tool

    curl -s http://127.0.0.1:4000/admin/usage | python3 -m json.tool
"""

import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

warnings.filterwarnings("ignore")
import litellm

litellm.suppress_debug_info = True

from fastapi import FastAPI, Header, HTTPException
from litellm import Router
from pydantic import BaseModel
from rich.console import Console

from common.config import CHAT_MODEL, OLLAMA_BASE_URL

console = Console()

app = FastAPI(title="LLM gateway", version="0.1.0")

# --- Virtual keys -------------------------------------------------------------
# The gateway issues its own keys. Applications never see a provider key, which
# means rotating a provider credential touches one service, and revoking a
# team's access is a line in a table rather than a redeploy.
#
# A real one keeps these in a database with expiry and scopes. The shape is the
# lesson, not the dict.
VIRTUAL_KEYS = {
    "sk-team-sre":      {"team": "sre",      "max_usd": 5.00, "models": ["chat", "chat-fast"]},
    "sk-team-analytics": {"team": "analytics", "max_usd": 0.50, "models": ["chat-fast"]},
}

# Spend so far, per key. In-memory here; a real gateway persists this, because
# a budget that resets on deploy is not a budget.
SPEND: dict[str, float] = {key: 0.0 for key in VIRTUAL_KEYS}
CALL_LOG: list[dict] = []

router = Router(
    model_list=[
        {"model_name": "chat",
         "litellm_params": {"model": f"ollama_chat/{CHAT_MODEL}", "api_base": OLLAMA_BASE_URL}},
        # A second alias pointing at the same local model. In production this
        # would be a smaller/cheaper deployment — the point is that callers
        # choose by INTENT ("fast") rather than by model name.
        {"model_name": "chat-fast",
         "litellm_params": {"model": f"ollama_chat/{CHAT_MODEL}", "api_base": OLLAMA_BASE_URL}},
    ],
    num_retries=1,
)


class ChatRequest(BaseModel):
    """The subset of the OpenAI request we support.

    Speaking someone else's API is the whole adoption strategy: any client,
    SDK or framework that already talks to OpenAI works against this by
    changing base_url.
    """

    model: str
    messages: list[dict]
    temperature: float | None = 0.1
    max_tokens: int | None = None


def authorise(authorization: str | None) -> tuple[str, dict]:
    """Check the virtual key and return (key, its policy).

    `Authorization: Bearer sk-...` is the OpenAI convention, so clients send it
    without being told to.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    key = authorization.removeprefix("Bearer ").strip()
    policy = VIRTUAL_KEYS.get(key)
    if policy is None:
        raise HTTPException(401, "unknown key")
    return key, policy


@app.get("/health")
def health():
    return {"status": "ok", "aliases": sorted({m["model_name"] for m in router.model_list})}


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest,
                           authorization: str | None = Header(default=None)):
    """The one endpoint that matters. OpenAI-shaped in, OpenAI-shaped out."""
    key, policy = authorise(authorization)

    # --- policy: is this key allowed this alias? ---
    if request.model not in policy["models"]:
        raise HTTPException(
            403,
            f"key for team {policy['team']!r} may not use {request.model!r}; "
            f"allowed: {policy['models']}",
        )

    # --- policy: is this key within budget? ---
    # Checked BEFORE the call. A budget enforced afterwards is a report.
    if SPEND[key] >= policy["max_usd"]:
        raise HTTPException(
            429,
            f"budget exhausted for team {policy['team']!r}: "
            f"${SPEND[key]:.4f} of ${policy['max_usd']:.2f}",
        )

    started = time.perf_counter()
    try:
        response = await router.acompletion(
            model=request.model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
    except Exception as e:
        # Upstream failures become a clean 502 rather than a stack trace.
        raise HTTPException(502, f"upstream error: {type(e).__name__}: {e}")

    elapsed_ms = (time.perf_counter() - started) * 1000
    cost = litellm.completion_cost(completion_response=response) or 0.0
    SPEND[key] += cost

    # --- the log every gateway exists to produce ---
    # One place that knows who called what, how much it cost, and how long it
    # took. Getting this from twenty services individually is the problem a
    # gateway solves.
    CALL_LOG.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "team": policy["team"],
        "alias": request.model,
        "served_by": response.model,
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "cost_usd": round(cost, 6),
        "latency_ms": round(elapsed_ms),
    })
    console.print(f"[dim]{policy['team']:<10} {request.model:<10} "
                  f"{response.usage.total_tokens:>5} tok  {elapsed_ms:>6.0f}ms  "
                  f"${cost:.6f}[/dim]")

    # `model_dump()` turns LiteLLM's response object back into the OpenAI JSON
    # shape the client expects.
    return response.model_dump()


@app.get("/admin/usage")
def usage():
    """Who spent what. The report you cannot produce without a gateway."""
    return {
        "spend": {
            key: {"team": p["team"], "spent_usd": round(SPEND[key], 6),
                  "budget_usd": p["max_usd"]}
            for key, p in VIRTUAL_KEYS.items()
        },
        "calls": len(CALL_LOG),
        "recent": CALL_LOG[-5:],
    }


if __name__ == "__main__":
    import uvicorn

    console.print("[bold]LLM gateway on http://127.0.0.1:4000[/bold]")
    console.print("[dim]keys: sk-team-sre (chat, chat-fast, $5) · "
                  "sk-team-analytics (chat-fast only, $0.50)[/dim]\n")
    uvicorn.run(app, host="127.0.0.1", port=4000, log_level="warning")
