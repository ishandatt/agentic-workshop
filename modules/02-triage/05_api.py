"""Step 3: put triage behind an HTTP endpoint, so alerts can actually arrive.

Until now we've been loading alerts from disk. Real alerts are pushed by a
monitoring system over HTTP. This is the service that receives them.

Run it:
    python modules/02-triage/05_api.py

Then, in a second terminal:
    curl -s -X POST http://127.0.0.1:8000/alert \\
      -H 'Content-Type: application/json' \\
      -d @data/sample_alerts/payment_error_spike.json | python3 -m json.tool

Interactive API docs, generated automatically from the schemas:
    open http://127.0.0.1:8000/docs
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# FastAPI is a web framework built around type hints. Its key property for us:
# it uses the SAME Pydantic models we already wrote, so the HTTP contract and
# the validation rules cannot drift apart.
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rich.console import Console

from common.config import CHAT_MODEL

from schemas import Alert, TriageResult
from triage import TriageError, triage

console = Console()

# The application object. title/description/version show up in the generated
# documentation at /docs.
app = FastAPI(
    title="Incident triage",
    description="Receives monitoring alerts and returns structured triage.",
    version="0.1.0",
)


class TriageResponse(BaseModel):
    """What the endpoint returns: the validated alert plus our assessment.

    Echoing the alert back makes the response self-contained — a log line or a
    downstream consumer can see exactly what was judged, not just the verdict.
    """

    alert: Alert
    triage: TriageResult


# `@app.get(...)` is a decorator: it registers the function below as the
# handler for that route. The function name is irrelevant to routing; the path
# in the decorator is what matters.
@app.get("/health")
def health():
    """Liveness check. Returns the model this service is configured to use."""
    # Returning a plain dict is fine — FastAPI serialises it to JSON.
    return {"status": "ok", "model": CHAT_MODEL}


# `response_model=` tells FastAPI what comes back. It validates our own output
# before sending it, and documents the shape at /docs. Catching your own bugs
# on the way out is as valuable as catching the client's on the way in.
@app.post("/alert", response_model=TriageResponse)
def receive_alert(alert: Alert):
    """Receive an alert and return structured triage.

    Note the signature: `alert: Alert`. That single annotation makes FastAPI
    read the request body, parse the JSON, and validate it against the Alert
    model — automatically returning HTTP 422 with a precise field-level error
    if the payload is wrong. We never write parsing code, and a malformed
    alert never reaches our logic.

    Note also `def`, not `async def`. Our triage() call blocks while the model
    thinks. FastAPI runs plain `def` handlers in a thread pool, so a slow call
    doesn't stall the whole server. Declaring `async def` around blocking code
    is a common and painful mistake — it would freeze the event loop.
    """
    console.print(
        f"[bold]→ alert received[/bold] {alert.service} "
        f"{alert.metric}={alert.value} (reported {alert.severity})"
    )

    try:
        result = triage(alert, max_attempts=3)
    except TriageError as e:
        # The model could not produce valid output after every retry. That is
        # a real, expected failure mode, and it deserves an honest status code:
        # 503 means "try again later", not "your request was wrong".
        console.print(f"[red]✘ triage failed:[/red] {e}")
        raise HTTPException(status_code=503, detail=str(e))

    console.print(
        f"[green]✔ {result.severity}[/green] (confidence {result.confidence:.2f})"
    )
    return TriageResponse(alert=alert, triage=result)


# `__name__` is a built-in variable holding the module's name. It equals
# "__main__" only when this file is run directly, and equals "03_api" when it
# is imported by something else. So this block runs on
# `python modules/02-triage/05_api.py` but not on import — the standard Python
# way to give a file both a library and a script identity.
if __name__ == "__main__":
    # Imported here rather than at the top: uvicorn is only needed when we are
    # the ones starting the server.
    import uvicorn

    console.print("[bold]Starting triage API on http://127.0.0.1:8000[/bold]")
    console.print("[dim]Interactive docs: http://127.0.0.1:8000/docs[/dim]\n")

    # uvicorn is the ASGI server that actually speaks HTTP; FastAPI only
    # describes the application. Same split as Flask and gunicorn.
    uvicorn.run(app, host="127.0.0.1", port=8000)
