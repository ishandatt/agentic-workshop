# Local LLM Stack — Ollama + Postgres/pgvector

A fully local environment for building LLM applications on macOS. No API keys,
no cloud calls, nothing leaves your machine.

Three pieces:

| Component | What it is | Where it runs | Port |
|---|---|---|---|
| **Ollama** | Serves a chat model and an embedding model over HTTP | Native (Homebrew) | `11434` |
| **PostgreSQL 16 + pgvector** | Vector store for embeddings | Container (Podman) | `5432` |
| **Python 3.11+ venv** | LangChain, LangGraph, FastAPI, MCP, psycopg | Native | — |

Default models are `qwen2.5:7b` (chat, ~4.7 GB) and `nomic-embed-text`
(embeddings, ~274 MB, 768-dim). Total download is roughly 6 GB including the
Podman VM image, so run the install on decent bandwidth.

> **Why is Ollama native instead of containerised?** Containers are a Linux
> kernel feature, so on macOS they run inside a Linux VM — Podman calls it a
> "machine", Docker Desktop hides its own. Neither passes the GPU through, so a
> containerised Ollama drops to CPU. Native Ollama uses Apple's Metal
> acceleration and is 5–10x faster. Rule of thumb: containers for infra, native
> for anything that needs the GPU.

---

## Prerequisites

- macOS (Apple Silicon or Intel)
- [Homebrew](https://brew.sh)
- Python 3.11 or newer
- ~10 GB free disk

---

## Install

Two paths. **Option A** is one command. **Option B** is the same thing done by
hand, if you want to control each piece or already have some of it installed.

### Option A — automated

```bash
./setup.sh
```

It is idempotent: it skips anything already installed and is safe to re-run.
It will install Ollama and Podman via Homebrew, create and start the Podman
machine, pull both models, start Postgres, create `.venv`, install
dependencies, copy `.env.example` to `.env`, and run the verification script.

Then activate the environment:

```bash
source .venv/bin/activate
```

### Option B — manual, step by step

#### 1. Get the code

```bash
git clone <repo-url>
cd agentic-workshop
```

#### 2. Check Python

```bash
python3 -V                    # must be 3.11 or newer
brew install python@3.12      # only if yours is older
```

#### 3. Install Ollama and start the server

```bash
brew install ollama
ollama serve                  # leave this running in its own terminal
```

To run it as a background service that survives reboots instead:

```bash
brew services start ollama
```

Confirm it's up:

```bash
curl -s http://localhost:11434/api/tags
```

#### 4. Pull the models

```bash
ollama pull qwen2.5:7b        # chat model, ~4.7 GB
ollama pull nomic-embed-text  # embedding model, ~274 MB
```

This is the slowest step. `ollama list` shows what you have.

#### 5. Install Podman and create its VM

```bash
brew install podman
podman machine init           # first time only — downloads a ~1 GB VM image
podman machine start
podman info                   # should print config, not an error
```

`podman machine init` is only ever needed once. After a reboot you only need
`podman machine start`.

#### 6. Start Postgres with pgvector

The easy way:

```bash
./scripts/db.sh start
```

Or the raw command it runs, if you'd rather do it yourself:

```bash
podman run -d \
  --name workshop-db \
  -e POSTGRES_USER=workshop \
  -e POSTGRES_PASSWORD=workshop \
  -e POSTGRES_DB=workshop \
  -p 5432:5432 \
  -v pgdata:/var/lib/postgresql/data \
  --health-cmd "pg_isready -U workshop" \
  --health-interval 5s \
  --health-timeout 3s \
  --health-retries 10 \
  docker.io/pgvector/pgvector:pg16
```

The image name is fully qualified on purpose. Podman's default
`short-name-mode` is `prompt`, so a bare `pgvector/pgvector:pg16` stops and
asks which registry you meant — which hangs any non-interactive script.

Wait for it to accept connections, then enable the extension:

```bash
podman exec workshop-db pg_isready -U workshop
podman exec workshop-db psql -U workshop -d workshop -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

#### 7. Create the Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

#### 8. Create your config file

```bash
cp .env.example .env
```

---

## Verify the install

### Everything at once

```bash
python scripts/check_setup.py
```

Seven checks: Ollama reachable, both models present, chat responds, embeddings
respond, Postgres reachable, pgvector enabled. All seven must be green.

### Check the chat model directly

```bash
ollama run qwen2.5:7b "Explain quantum computing in one sentence."
```

A single sentence should stream back within a few seconds. A couple more worth
trying:

```bash
# Does it follow instructions?
ollama run qwen2.5:7b "Reply with exactly one word: OK"

# Does it produce clean JSON? (7B models often don't — good to know early)
ollama run qwen2.5:7b 'Return only JSON, no prose: {"service":"payments","severity":"high"}'
```

Same thing through the HTTP API, which is what the Python code actually uses:

```bash
curl -s http://localhost:11434/api/generate -d '{
  "model": "qwen2.5:7b",
  "prompt": "Explain quantum computing in one sentence.",
  "stream": false
}' | python3 -m json.tool
```

The response includes `prompt_eval_count` (input tokens) and `eval_count`
(output tokens) alongside the text.

### Check the embedding model

Embedding models have no chat interface — call the API:

```bash
curl -s http://localhost:11434/api/embed -d '{
  "model": "nomic-embed-text",
  "input": "hello"
}' | python3 -c "import json,sys; print(len(json.load(sys.stdin)['embeddings'][0]), 'dimensions')"
```

Expect `768 dimensions`.

### Check the database

```bash
./scripts/db.sh status
podman exec workshop-db psql -U workshop -d workshop -c "SELECT '[1,2,3]'::vector;"
```

---

## Everyday commands

The database does not restart itself after a reboot. `scripts/db.sh` is the
handle for it:

```bash
./scripts/db.sh start     # start the VM and the container, wait until ready
./scripts/db.sh stop      # stop the container, keep the data
./scripts/db.sh status    # VM state, container health, connection check
./scripts/db.sh psql      # a psql shell inside the container — nothing to install
./scripts/db.sh logs      # follow Postgres logs
./scripts/db.sh reset     # destroy the container AND its data volume
./scripts/db.sh machine   # just create/start the Podman VM
```

Ollama equivalents:

```bash
ollama list               # installed models
ollama ps                 # models currently loaded in memory
ollama rm <model>         # free disk space
```

---

## Configuration

Everything is driven by `.env` in the project root, read by `common/config.py`.

| Key | Default | Purpose |
|---|---|---|
| `CHAT_MODEL` | `qwen2.5:7b` | Chat model name |
| `EMBED_MODEL` | `nomic-embed-text` | Embedding model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `DATABASE_URL` | `postgresql://workshop:workshop@localhost:5432/workshop` | Postgres connection string |
| `REF_PRICE_INPUT_PER_1M` | `3.00` | Reference cloud price, USD per 1M input tokens |
| `REF_PRICE_OUTPUT_PER_1M` | `15.00` | Reference cloud price, USD per 1M output tokens |

The reference prices are for cost estimation only — local inference is free,
but `common/metrics.py` converts token counts into what the same calls would
cost on a paid API.

**To switch models**, edit `.env` and re-run `./setup.sh` — it pulls whatever
`.env` names. `.env` is the single source of truth: both the setup script and
all Python code read it, so they can't drift. A real shell environment variable
still wins over `.env` if you need a one-off:

```bash
CHAT_MODEL=llama3.1:8b ./setup.sh
```

---

## Layout

```
.
├── setup.sh                  # automated install (Option A above)
├── scripts/db.sh             # Postgres container control
├── requirements.txt
├── .env.example              # copy to .env
├── common/
│   ├── config.py             # loads .env; import config from here
│   └── metrics.py            # token/latency/cost tracking for LLM calls
├── modules/                  # example code, numbered to run in order
├── docs/                     # written guides, one per module
├── runbook/                  # source documents for the vector store
└── data/
    ├── sample_alerts/        # JSON payloads to POST at the services
    ├── evals.jsonl           # labelled evaluation cases
    ├── past_incidents.jsonl  # corpus for the memory module
    └── runs.jsonl            # written by the pipeline on each run
```

---

## Troubleshooting

**`Cannot connect to Podman`** — the VM is stopped. `podman machine start`, or
`./scripts/db.sh start` which does it for you.

**Port 5432 already in use** — something else holds it. Find it with
`lsof -nP -iTCP:5432 -sTCP:LISTEN`. A Homebrew Postgres is the usual culprit:
`brew services stop postgresql@16`.

**`check_setup.py` says a model isn't pulled** — the name in `.env` doesn't
match anything in `ollama list`. Either `ollama pull <that model>` or fix
`.env`.

**Ollama connection refused** — the server isn't running. `ollama serve`, or
`brew services start ollama`.

**Database is broken and you want a clean slate** — `./scripts/db.sh reset`
destroys the container and its volume; the next `start` builds an empty
database. This deletes all stored vectors.

**Podman machine won't start** — `podman machine stop && podman machine start`.
If it stays broken, `podman machine rm && podman machine init` re-downloads the
VM image.
