"""Show what we sent and what came back.

Every script in this workshop prints at least one full exchange — the exact
messages that went to the model, and the exact text that came back. That rule
exists because an LLM call is the one part of the system you cannot infer by
reading the code: the prompt is assembled from templates and variables, and
what the model does with it is the whole question.

Usage:
    from common.display import show_messages, show_response

    show_messages(messages)                 # what we sent
    response = llm.invoke(messages)
    show_response(response.content)         # what came back
"""

import json

from rich.console import Console
from rich.panel import Panel

console = Console()

# Colour per role, so a long transcript is scannable. A plain dict used as a
# lookup table — Python's equivalent of a switch over string keys.
_ROLE_STYLE = {
    "system": "magenta",
    "human": "cyan",
    "user": "cyan",
    "ai": "green",
    "assistant": "green",
}


def _role_of(message) -> str:
    """Work out a role name for either message format we use.

    LangChain message objects carry a `.type` attribute ("system", "human",
    "ai"). Raw Ollama calls use plain dicts with a "role" key. Supporting both
    means the same helper works in the raw-HTTP script and the LangChain ones.
    """
    # getattr(obj, name, default) reads an attribute that may not exist,
    # instead of raising. So this asks "are you a LangChain message?" without
    # needing to import LangChain here.
    role = getattr(message, "type", None)
    if role is None and isinstance(message, dict):
        role = message.get("role", "?")
    return role or "?"


def _content_of(message) -> str:
    """Same idea for the body of the message."""
    if isinstance(message, dict):
        return str(message.get("content", ""))
    return str(getattr(message, "content", message))


def show_messages(messages, title: str = "Sent to the model") -> None:
    """Print the exact conversation being sent, one labelled block per message.

    `messages` may be LangChain message objects or {"role", "content"} dicts.
    """
    parts = []
    for message in messages:
        role = _role_of(message)
        style = _ROLE_STYLE.get(role, "white")
        content = _content_of(message)

        # Indent continuation lines so multi-line content stays visually
        # attached to its role label. The label is padded to a fixed width so
        # the blocks line up.
        indented = content.replace("\n", "\n" + " " * 12)
        parts.append(f"[{style}][{role:^9}][/{style}] {indented}")

    # "\n".join(list) glues the blocks together with blank lines between them.
    console.print(Panel("\n\n".join(parts), title=f"[dim]{title}[/dim]",
                        border_style="blue", expand=False))


def show_response(text: str, title: str = "Model returned") -> None:
    """Print what came back, pretty-printing it when it happens to be JSON.

    The border matters: it marks exactly where the model's output starts and
    stops, so stray markdown fences or a chatty preamble are unmistakable
    rather than blending into your terminal.
    """
    body = text.strip()
    try:
        # json.loads raises if this isn't JSON; re-dumping with an indent is
        # what turns one dense line into something readable.
        body = json.dumps(json.loads(body), indent=2)
    except (json.JSONDecodeError, TypeError):
        # Not JSON — that is frequently the interesting case. Show it as-is.
        pass

    console.print(Panel(body, title=f"[dim]{title}[/dim]",
                        border_style="green", expand=False))
