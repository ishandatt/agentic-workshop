#!/usr/bin/env bash
# Agentic Workflows Workshop — one-shot setup for macOS
set -euo pipefail

# Everything below assumes the repo root, so go there regardless of where
# this script was invoked from.
cd "$(dirname "$0")"

BOLD=$(tput bold 2>/dev/null || true); RESET=$(tput sgr0 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true); RED=$(tput setaf 1 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)

ok()   { echo "${GREEN}✔${RESET} $1"; }
warn() { echo "${YELLOW}⚠${RESET} $1"; }
die()  { echo "${RED}✘ $1${RESET}"; exit 1; }
step() { echo; echo "${BOLD}── $1${RESET}"; }

# --- model selection --------------------------------------------------------
# .env is the single source of truth for which models we use. common/config.py
# reads it via load_dotenv(), so setup.sh has to read it too — otherwise the
# models we PULL here drift from the models the Python code USES, and
# check_setup.py fails with "not pulled" on a model you never asked for.
#
# Created here, at the top, because the pull step below needs it. (It used to
# be created near the end of this script, after the pull had already guessed.)
[ -f .env ] || cp .env.example .env

# Precedence must match load_dotenv(override=False): a real shell environment
# variable beats .env, which beats the fallback. Sourcing alone would invert
# that, so stash any pre-existing values first and reapply them after.
_env_chat="${CHAT_MODEL:-}"
_env_embed="${EMBED_MODEL:-}"
set -a; . ./.env; set +a          # .env must be valid shell: KEY=value
CHAT_MODEL="${_env_chat:-${CHAT_MODEL:-qwen2.5:7b}}"
EMBED_MODEL="${_env_embed:-${EMBED_MODEL:-nomic-embed-text}}"

step "1/7 Checking prerequisites"
command -v brew   >/dev/null || die "Homebrew not found — install from https://brew.sh"
command -v podman >/dev/null || die "Podman not found — install it with: brew install podman"
ok "Homebrew and Podman present"

PY=python3
$PY -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
  || die "Python 3.11+ required (found $($PY -V)). Try: brew install python@3.12"
ok "Python $($PY -V | cut -d' ' -f2)"
ok "Models (from .env): $CHAT_MODEL (chat), $EMBED_MODEL (embeddings)"

# Done early and on its own line because a first-time `podman machine init`
# downloads a ~1 GB Linux VM image — you want to see that happening, not
# discover it stalled inside some later step.
step "2/7 Preparing the Podman machine (the Linux VM containers run in)"
./scripts/db.sh machine

step "3/7 Installing Ollama (native, NOT in a container — needs Metal GPU access)"
if ! command -v ollama >/dev/null; then
  brew install ollama
fi
if ! curl -s http://localhost:11434/api/tags >/dev/null; then
  warn "Ollama server not running — starting it in the background"
  (ollama serve >/tmp/ollama.log 2>&1 &)
  sleep 3
fi
curl -s http://localhost:11434/api/tags >/dev/null || die "Ollama server failed to start"
ok "Ollama server running on :11434"

step "4/7 Pulling models (this is the slow part — ~5 GB total)"
ollama pull "$CHAT_MODEL"
ollama pull "$EMBED_MODEL"
ok "Models ready: $CHAT_MODEL, $EMBED_MODEL"

step "5/7 Starting Postgres + pgvector (Podman)"
# db.sh blocks until Postgres actually accepts connections, so the
# verification step below can't race the database's first-boot init.
./scripts/db.sh start

step "6/7 Python virtual environment"
if [ ! -d .venv ]; then $PY -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
ok "Dependencies installed into .venv"

step "7/7 Verifying the full stack"
python scripts/check_setup.py

echo
echo "${BOLD}${GREEN}Setup complete.${RESET} Next:"
echo "  source .venv/bin/activate"
echo "  open docs/01-foundations.md"
echo
echo "The database doesn't restart itself after a reboot — bring it back with:"
echo "  ./scripts/db.sh start    (and ./scripts/db.sh status to check on it)"
