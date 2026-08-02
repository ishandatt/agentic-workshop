"""Load the sample alerts from data/sample_alerts/.

Small helper so every script in this module reads alerts the same way, and so
the JSON files stay the single source of truth for what an alert looks like.
"""

import json
from pathlib import Path

from schemas import Alert

# Anchor to this file's location, not the working directory, so the scripts
# work no matter where you run them from.
#   parents[0] = 02-triage, [1] = modules, [2] = project root
ALERTS_DIR = Path(__file__).resolve().parents[2] / "data" / "sample_alerts"


def list_alerts() -> list[str]:
    """Names of the available sample alerts, without the .json extension."""
    # `.glob("*.json")` yields matching paths; `.stem` is the filename without
    # its suffix. sorted() gives a stable order for display.
    return sorted(p.stem for p in ALERTS_DIR.glob("*.json"))


def load_alert(name: str = "payment_error_spike") -> Alert:
    """Read one sample alert file and validate it into an Alert object.

    This is the same validation the API endpoint performs on incoming
    requests, so a broken sample file fails here exactly as a broken HTTP
    payload would fail there.
    """
    path = ALERTS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No sample alert named '{name}'. Available: {', '.join(list_alerts())}"
        )
    # `**` unpacks a dictionary into keyword arguments, so
    # Alert(**{"service": "x", ...}) becomes Alert(service="x", ...).
    # Pydantic validates every field during construction.
    return Alert(**json.loads(path.read_text()))
