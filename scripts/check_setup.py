"""Verify the whole local stack: Ollama (chat + embeddings) and Postgres+pgvector.

This lives in `scripts/` rather than inside any one module because it checks
the environment every module depends on. Run it whenever something feels
broken, not just on day one.

Run:  python scripts/check_setup.py

Note that nothing here knows or cares that Postgres runs in a Podman
container — it's just a host and port over TCP. That's the point of putting
infra in a container: the thing using it doesn't have to care.
"""

# `sys` gives us access to the interpreter itself (here: the module search
# path, and the exit code we hand back to the shell).
import sys

# `pathlib.Path` is Python's object-oriented file-path type. Path objects
# support the `/` operator for joining, so `Path("a") / "b"` is "a/b".
from pathlib import Path

# --- Making `import common...` work -----------------------------------------
# Python only imports from directories on `sys.path`. When you run
# `python scripts/check_setup.py`, Python puts `scripts/` on that list — not
# the project root — so `from common.config import ...` would fail.
#
# Reading the line below from the inside out:
#   __file__          this file's path, e.g. "scripts/check_setup.py"
#   .resolve()        make it absolute and follow any symlinks
#   .parents[1]       walk up 1 directory: parents[0] is scripts/, [1] is root
#   str(...)          sys.path holds strings, not Path objects
#   sys.path.insert(0, ...)   put it FIRST so our code wins over any
#                             same-named package installed in the venv
#
# Scripts inside modules/NN-name/ are one level deeper, so they use
# parents[2] instead. Same trick, different depth.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Third-party packages, installed into .venv from requirements.txt.
import httpx          # HTTP client — like `requests`, but modern and typed
import psycopg        # PostgreSQL driver (version 3)
from rich.console import Console   # pretty terminal output: colour, tables

# Our own shared code. This is the import that needed the sys.path line above.
from common.config import CHAT_MODEL, DATABASE_URL, EMBED_MODEL, OLLAMA_BASE_URL

# One shared console object handles all printing. `[green]…[/green]` markup in
# the strings below is Rich's styling syntax, not part of Python.
console = Console()

# A plain module-level counter. Every failed check bumps it, and the exit code
# at the bottom depends on it.
failures = 0


def check(label: str, fn):
    """Run one check function, print a tick or a cross, and count failures.

    The `label: str` annotation is a *type hint*. Python does not enforce it at
    runtime — it exists for readers and for tools like mypy and PyCharm.

    `fn` is a function passed as an argument. Functions are ordinary values in
    Python, so we can hand them around like any other object and call them
    later. That's what lets every check below be a one-liner.
    """
    # `global` is required to ASSIGN to a module-level variable from inside a
    # function. Without it, `failures += 1` would create a new local variable
    # and the outer counter would never change.
    global failures
    try:
        # Call the check. Returning a string means "passed, and here's a
        # detail worth printing"; returning None means "passed, no detail".
        detail = fn()
        # An f-string ("formatted string literal") interpolates expressions
        # inside {} directly into the text.
        # The trailing `if/else` is a conditional expression — Python's
        # ternary — reading as: use the detail text if there is one, else "".
        console.print(f"[green]✔[/green] {label}" + (f" [dim]({detail})[/dim]" if detail else ""))
    except Exception as e:
        # Any exception at all means this check failed. We deliberately catch
        # broadly: a connection error, a bad response shape and a missing key
        # are all just "this isn't working yet" to the person running setup.
        failures += 1
        console.print(f"[red]✘[/red] {label} — {e}")


# --- The individual checks --------------------------------------------------
# Each function either returns a detail string (pass) or raises (fail).

def ollama_up():
    """Is the Ollama server answering at all?"""
    r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
    # Raises an exception for any 4xx/5xx status, which `check()` catches.
    r.raise_for_status()
    # `.json()` parses the response body into Python dicts/lists.
    # `.get("models", [])` reads a key with a fallback, so a missing key
    # returns an empty list instead of raising KeyError.
    return f"{len(r.json().get('models', []))} models installed"


def model_present(name):
    """Build a check that asserts a specific model has been pulled.

    This returns a *function* rather than doing the work itself, because
    `check()` expects something it can call. `_inner` remembers the `name`
    argument from its enclosing scope even after `model_present` has returned —
    that combination of function plus captured variables is called a closure.
    """
    def _inner():
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        # A list comprehension: "for every model dict m in the list, collect
        # m['name']". Equivalent to a for-loop that appends, but one line.
        names = [m["name"] for m in r.json().get("models", [])]
        # `any(...)` is True if at least one item is truthy. We use startswith
        # because Ollama reports "qwen2.5:7b" while a user may have asked for
        # "qwen2.5" — a prefix match accepts both.
        if not any(n.startswith(name) for n in names):
            raise RuntimeError(f"not pulled — run: ollama pull {name}")
        return None
    return _inner


def chat_works():
    """Can the chat model actually generate text? (Not just be installed.)"""
    r = httpx.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        # `json=` tells httpx to serialise this dict and set the content-type.
        json={"model": CHAT_MODEL, "prompt": "Say OK", "stream": False},
        # Generous timeout: the very first call also loads the model into RAM.
        timeout=120,
    )
    r.raise_for_status()
    # Ollama reports durations in nanoseconds; 1e9 converts to seconds.
    return f"replied in {r.json().get('total_duration', 0) / 1e9:.1f}s"


def embed_works():
    """Can the embedding model turn text into a vector?"""
    r = httpx.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": "hello"},
        timeout=60,
    )
    r.raise_for_status()
    # The response holds a list of vectors (one per input). We sent one input,
    # so [0] is our vector, and its length is the dimensionality.
    dims = len(r.json()["embeddings"][0])
    return f"{dims}-dim vectors"


def postgres_up():
    """Can we open a TCP connection and run SQL?"""
    # `with` is a context manager: it guarantees the connection is closed when
    # the block ends, even if an exception is raised inside. Python's
    # equivalent of try/finally, or Go's `defer`.
    with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
        # `.fetchone()` returns the first row as a tuple; [0] is its first
        # column — here the long PostgreSQL version banner.
        ver = conn.execute("SELECT version()").fetchone()[0]
    # Trim "PostgreSQL 16.14 on aarch64-…" down to just the version part.
    return ver.split(" on ")[0]


def pgvector_available():
    """Is the vector extension installed and usable?"""
    with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
        # Idempotent: creates the extension the first time, no-ops afterwards.
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # Casting a literal to `vector` proves the type is actually registered.
        conn.execute("SELECT '[1,2,3]'::vector")
    return "extension enabled"


# --- Run them all -----------------------------------------------------------
# Note we pass the function itself (`ollama_up`) with NO parentheses — passing
# `ollama_up()` would call it immediately and hand `check` the result instead.
console.print("\n[bold]Verifying local stack[/bold]\n")
check("Ollama server reachable", ollama_up)
check(f"Chat model '{CHAT_MODEL}' installed", model_present(CHAT_MODEL))
check(f"Embedding model '{EMBED_MODEL}' installed", model_present(EMBED_MODEL))
check("Chat model responds", chat_works)
check("Embedding model responds", embed_works)
check("Postgres reachable", postgres_up)
check("pgvector extension", pgvector_available)

console.print()
if failures:
    console.print(f"[red bold]{failures} check(s) failed.[/red bold] Fix the above, then re-run.")
    console.print(
        "[dim]Ollama problems?  ollama serve  ·  ollama pull <model>\n"
        "Postgres problems? ./scripts/db.sh status  ·  ./scripts/db.sh start[/dim]"
    )
    # A non-zero exit code tells the shell (and setup.sh) that this failed.
    sys.exit(1)
console.print("[green bold]All checks passed — your environment is ready.[/green bold]")
