"""Central configuration. Every module imports from here so that switching
models or endpoints is a one-line .env change, never a code hunt.

Read this file first — everything else depends on it.
"""

# `os` is the standard-library module for talking to the operating system.
# We use it here only for environment variables.
import os

# `Path` is the object-oriented way to handle file paths (see the note below
# on why we don't just hardcode "../.env").
from pathlib import Path

# Third-party: reads a .env file and loads its KEY=value pairs into the
# process environment, so os.getenv() can see them.
from dotenv import load_dotenv

# --- Find the .env file, wherever the script was launched from --------------
# A relative path like "./.env" would resolve against the *current working
# directory*, so `python modules/01-foundations/02_raw_ollama.py` and
# `cd modules && python ...` would look in different places. Anchoring to this
# file's own location makes it work identically from anywhere.
#
#   __file__      this file, i.e. "…/common/config.py"
#   .resolve()    make it absolute, following symlinks
#   .parent       "…/common"
#   .parent       "…/"          <- the project root
REPO_ROOT = Path(__file__).resolve().parent.parent

# `/` is overloaded on Path objects to join path segments — this is
# REPO_ROOT + "/.env", written the Pythonic way.
#
# Important: load_dotenv defaults to `override=False`, meaning a variable
# already set in the real environment WINS over the .env file. So the
# precedence is: shell environment -> .env -> the fallbacks below.
# setup.sh deliberately reproduces this same order.
load_dotenv(REPO_ROOT / ".env")

# --- The settings themselves ------------------------------------------------
# os.getenv(NAME, default) reads an environment variable, returning `default`
# if it isn't set. These constants are UPPER_CASE by convention: Python has no
# `const` keyword, so capitals are how we signal "don't reassign this".
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:7b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://workshop:workshop@localhost:5432/workshop"
)

# Reference cloud pricing (USD per 1M tokens) purely for cost intuition —
# Ollama is free, but every pipeline you build here has a real-world price.
#
# Environment variables are ALWAYS strings, so "3.00" must be converted to a
# number explicitly. float() does that; without it, arithmetic later would
# either fail or silently do string repetition.
REF_PRICE_INPUT_PER_1M = float(os.getenv("REF_PRICE_INPUT_PER_1M", "3.00"))
REF_PRICE_OUTPUT_PER_1M = float(os.getenv("REF_PRICE_OUTPUT_PER_1M", "15.00"))
