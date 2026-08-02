#!/usr/bin/env bash
# The workshop database: Postgres 16 + pgvector, run by Podman.
#
# There is exactly ONE container in this whole workshop, so there is no
# compose file — an orchestrator for a single container is ceremony, and
# `podman compose` is only a shim that needs podman-compose or docker-compose
# installed separately. A plain `podman run` is fewer moving parts on
# workshop-morning wifi, and you can read the entire thing below.
#
# Usage:
#   ./scripts/db.sh machine   # create/start the Podman Linux VM (nothing else)
#   ./scripts/db.sh start     # VM + pull + run + wait until Postgres accepts connections
#   ./scripts/db.sh stop      # stop the container, keep the data
#   ./scripts/db.sh reset     # destroy container AND data volume (fresh vector store)
#   ./scripts/db.sh status    # what's up right now
#   ./scripts/db.sh logs      # follow Postgres logs
#   ./scripts/db.sh psql      # interactive psql inside the container
set -euo pipefail

# --- configuration ----------------------------------------------------------
# The image name is FULLY QUALIFIED on purpose. Podman's default
# short-name-mode is "prompt": given a bare `pgvector/pgvector:pg16` it stops
# and asks which registry you meant, which would hang setup.sh forever.
IMAGE="docker.io/pgvector/pgvector:pg16"
CONTAINER="workshop-db"
VOLUME="pgdata"
DB_USER="workshop"
DB_PASS="workshop"
DB_NAME="workshop"
DB_PORT="5432"

BOLD=$(tput bold 2>/dev/null || true); RESET=$(tput sgr0 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true); RED=$(tput setaf 1 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)

ok()   { echo "${GREEN}✔${RESET} $1"; }
warn() { echo "${YELLOW}⚠${RESET} $1"; }
die()  { echo "${RED}✘ $1${RESET}" >&2; exit 1; }
step() { echo; echo "${BOLD}── $1${RESET}"; }

# --- the Podman VM ----------------------------------------------------------
# Containers are a Linux kernel feature, so on macOS they must run inside a
# Linux VM. Docker Desktop hides that VM from you; Podman makes it explicit.
# This is the same reason Ollama is NOT containerised: there is no Metal/GPU
# passthrough into that VM under either engine, so a containerised Ollama
# would fall back to CPU and run 5–10x slower.
ensure_machine() {
  command -v podman >/dev/null \
    || die "Podman not found — install it with: brew install podman"

  # `podman info` succeeds only when the VM is up AND the CLI can reach it.
  # Probing behaviour beats parsing `podman machine list` output, which has
  # changed shape across Podman releases.
  if podman info >/dev/null 2>&1; then
    ok "Podman machine running"
    return
  fi

  # No machine at all? Create one. This downloads a ~1 GB VM image the first
  # time — the slowest step of a fresh setup, so we let its progress show.
  if ! podman machine list --format '{{.Name}}' 2>/dev/null | grep -q .; then
    warn "No Podman machine found — creating one (downloads a ~1 GB VM image)"
    podman machine init
  fi

  warn "Podman machine is stopped — starting it"
  podman machine start

  podman info >/dev/null 2>&1 \
    || die "Podman machine started but is unreachable. Try: podman machine stop && podman machine start"
  ok "Podman machine running"
}

# Current state of our container: "running", "exited", "created", … or empty
# if it does not exist. The ^…$ anchors matter — Podman's name filter is a
# regex and would otherwise match any container containing this substring.
container_state() {
  podman ps -a --filter "name=^${CONTAINER}$" --format '{{.State}}' 2>/dev/null || true
}

# --- readiness --------------------------------------------------------------
# `podman run` returns as soon as the container is CREATED, but Postgres needs
# a few more seconds to initialise its data directory on first boot. Handing
# off to check_setup.py before then produces a confusing "connection refused".
# We poll pg_isready rather than the healthcheck status because it is exact
# and version-independent.
wait_ready() {
  local tries=60
  echo -n "Waiting for Postgres to accept connections"
  while [ "$tries" -gt 0 ]; do
    if podman exec "$CONTAINER" pg_isready -U "$DB_USER" -q 2>/dev/null; then
      echo
      ok "Postgres ready on :${DB_PORT} (db=${DB_NAME} user=${DB_USER})"
      return
    fi
    echo -n "."
    sleep 1
    tries=$((tries - 1))
  done
  echo
  die "Postgres did not become ready in 60s. Inspect it with: ./scripts/db.sh logs"
}

# --- subcommands ------------------------------------------------------------
cmd_start() {
  ensure_machine

  local state
  state=$(container_state)

  if [ "$state" = "running" ]; then
    ok "Container '${CONTAINER}' already running"
    wait_ready
    return
  fi

  if [ -n "$state" ]; then
    # Container exists but is stopped — restart it. The named volume means
    # everything you ingested in module 4 is still there.
    step "Restarting existing container '${CONTAINER}' (state: ${state})"
    podman start "$CONTAINER" >/dev/null
  else
    # Fresh container. Check the port first: a stray Postgres from Homebrew
    # or another project produces a cryptic bind error otherwise.
    if lsof -nP -iTCP:"$DB_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      die "Port ${DB_PORT} is already in use by another process.
   Find it with:  lsof -nP -iTCP:${DB_PORT} -sTCP:LISTEN
   Common culprits: 'brew services stop postgresql@16', or another project's database."
    fi

    step "Pulling ${IMAGE} (~450 MB on first run)"
    podman pull "$IMAGE"

    step "Starting Postgres + pgvector"
    podman run -d \
      --name "$CONTAINER" \
      -e POSTGRES_USER="$DB_USER" \
      -e POSTGRES_PASSWORD="$DB_PASS" \
      -e POSTGRES_DB="$DB_NAME" \
      -p "${DB_PORT}:5432" \
      -v "${VOLUME}:/var/lib/postgresql/data" \
      --health-cmd "pg_isready -U ${DB_USER}" \
      --health-interval 5s \
      --health-timeout 3s \
      --health-retries 10 \
      "$IMAGE" >/dev/null
  fi

  wait_ready
}

cmd_stop() {
  if [ -z "$(container_state)" ]; then
    warn "Container '${CONTAINER}' does not exist — nothing to stop"
    return
  fi
  podman stop "$CONTAINER" >/dev/null
  ok "Stopped '${CONTAINER}' (data volume '${VOLUME}' kept)"
}

# Destroys the data too. You will want this from module 4 onward whenever you
# re-ingest the runbook and want a genuinely empty vector store.
cmd_reset() {
  podman rm -f "$CONTAINER" >/dev/null 2>&1 || true
  podman volume rm "$VOLUME" >/dev/null 2>&1 || true
  ok "Removed container '${CONTAINER}' and volume '${VOLUME}' — next start is a clean database"
}

cmd_logs() {
  [ -n "$(container_state)" ] || die "Container '${CONTAINER}' does not exist — run: ./scripts/db.sh start"
  podman logs -f "$CONTAINER"
}

# psql without installing psql: it already lives inside the image. Module 4
# uses this to look at the raw embedding vectors in the table.
cmd_psql() {
  [ "$(container_state)" = "running" ] || die "Container '${CONTAINER}' is not running — run: ./scripts/db.sh start"
  podman exec -it "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME"
}

cmd_status() {
  command -v podman >/dev/null || die "Podman not found — install it with: brew install podman"

  local machines
  machines=$(podman machine list --format '  {{.Name}}  running={{.Running}}  last-up={{.LastUp}}' 2>/dev/null || true)

  echo "${BOLD}Podman machine${RESET}"
  if [ -n "$machines" ]; then
    echo "$machines"
  else
    echo "  (none — run: ./scripts/db.sh machine)"
  fi

  echo
  echo "${BOLD}Container${RESET}"
  if ! podman info >/dev/null 2>&1; then
    echo "  (machine down — run: ./scripts/db.sh start)"
    return
  fi
  if [ -z "$(container_state)" ]; then
    echo "  (not created — run: ./scripts/db.sh start)"
    return
  fi
  podman ps -a --filter "name=^${CONTAINER}$" \
    --format '  {{.Names}}  {{.Status}}  {{.Ports}}'

  echo
  echo "${BOLD}Postgres${RESET}"
  local ready
  if ready=$(podman exec "$CONTAINER" pg_isready -U "$DB_USER" 2>/dev/null); then
    echo "  $ready"
  else
    echo "  not accepting connections"
  fi
}

case "${1:-}" in
  machine) ensure_machine ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  reset)   cmd_reset ;;
  logs)    cmd_logs ;;
  psql)    cmd_psql ;;
  status)  cmd_status ;;
  *)
    echo "usage: ./scripts/db.sh {machine|start|stop|reset|status|logs|psql}" >&2
    exit 1
    ;;
esac
