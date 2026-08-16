#!/usr/bin/env bash
# The n8n container, for bonus 8 only.
#
# This is the SECOND container in a workshop that otherwise makes a point of
# having exactly one. That is deliberate and it is opt-in: nothing in modules
# 0-16 needs this, and if you never open bonus 8 you never pull it. The
# precedent is module 16, which likewise carries its own prerequisites rather
# than taxing everyone who is not doing it.
#
# Usage:
#   ./scripts/n8n.sh start     # pull + run + wait until the editor answers
#   ./scripts/n8n.sh stop      # stop it, keep your workflows
#   ./scripts/n8n.sh reset     # destroy the container AND its data volume
#   ./scripts/n8n.sh status    # what's up right now
#   ./scripts/n8n.sh logs      # follow n8n logs
set -euo pipefail

# --- configuration ----------------------------------------------------------
# Fully qualified for the same reason db.sh is: Podman's default
# short-name-mode is "prompt", which hangs a non-interactive script.
#
# The tag is PINNED rather than :latest on purpose. This module's workflow.json
# is written against a specific node schema, and n8n ships several releases a
# week — an unpinned image would silently move under the material.
IMAGE="docker.io/n8nio/n8n:2.34.2"
CONTAINER="workshop-n8n"
VOLUME="n8ndata"
PORT="5678"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Same credential-helper workaround db.sh needs — the n8n image also comes from
# docker.io, so without this the pull fails identically on any machine with a
# credHelpers entry in ~/.docker/config.json.
# shellcheck source=./_podman_auth.sh
. "$HERE/_podman_auth.sh"

BOLD=$(tput bold 2>/dev/null || true); RESET=$(tput sgr0 2>/dev/null || true)
GREEN=$(tput setaf 2 2>/dev/null || true); RED=$(tput setaf 1 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)

ok()   { echo "${GREEN}✔${RESET} $1"; }
warn() { echo "${YELLOW}⚠${RESET} $1"; }
die()  { echo "${RED}✘ $1${RESET}" >&2; exit 1; }
step() { echo; echo "${BOLD}── $1${RESET}"; }

container_state() {
  podman ps -a --filter "name=^${CONTAINER}$" --format '{{.State}}' 2>/dev/null || true
}

wait_ready() {
  echo -n "Waiting for the n8n editor to answer"
  for _ in $(seq 1 60); do
    # -f matters: without it curl exits 0 for ANY response, so a 502 from a
    # half-started n8n would count as ready and the browser would show an
    # error page we just told you was fine.
    if curl -sf -o /dev/null "http://127.0.0.1:${PORT}/rest/settings"; then
      echo; ok "n8n ready on http://localhost:${PORT}"
      return
    fi
    echo -n "."; sleep 1
  done
  echo
  die "n8n did not become ready in 60s. Inspect it with: ./scripts/n8n.sh logs"
}

cmd_start() {
  # The Podman VM is db.sh's job; there is no reason to reimplement it here.
  "$HERE/db.sh" machine

  local state
  state=$(container_state)

  if [ "$state" = "running" ]; then
    ok "Container '${CONTAINER}' already running"
    wait_ready
    return
  fi

  # Checked on BOTH paths. A stopped container is no protection: something else
  # can have taken the port while it was down, and `podman start` would then
  # fail with a raw Podman error instead of this message.
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    die "Port ${PORT} is already in use by another process.
   Find it with:  lsof -nP -iTCP:${PORT} -sTCP:LISTEN"
  fi

  if [ -n "$state" ]; then
    step "Restarting existing container '${CONTAINER}' (state: ${state})"
    # `podman start` is a no-op on a paused container; unpause first so a
    # machine that was suspended mid-workshop recovers on its own.
    if [ "$state" = "paused" ]; then
      podman unpause "$CONTAINER" >/dev/null
    else
      podman start "$CONTAINER" >/dev/null
    fi
  else
    step "Pulling ${IMAGE} (~500 MB on first run)"
    podman pull "$IMAGE"

    step "Starting n8n"
    podman run -d \
      --name "$CONTAINER" \
      -p "${PORT}:5678" \
      -v "${VOLUME}:/home/node/.n8n" \
      -e N8N_SECURE_COOKIE=false \
      -e N8N_RUNNERS_ENABLED=true \
      -e N8N_DIAGNOSTICS_ENABLED=false \
      -e N8N_VERSION_NOTIFICATIONS_ENABLED=false \
      -e N8N_PERSONALIZATION_ENABLED=false \
      "$IMAGE" >/dev/null
  fi

  wait_ready
}

cmd_stop() {
  [ -n "$(container_state)" ] || { warn "No container named '${CONTAINER}'"; return; }
  podman stop "$CONTAINER" >/dev/null
  ok "Stopped '${CONTAINER}' (workflows kept in volume '${VOLUME}')"
}

cmd_reset() {
  podman rm -f "$CONTAINER" >/dev/null 2>&1 || true
  podman volume rm "$VOLUME" >/dev/null 2>&1 || true
  ok "Destroyed '${CONTAINER}' and volume '${VOLUME}' — your workflows are gone"
}

cmd_logs() {
  # Guarded, because wait_ready() points people here precisely when startup has
  # failed — which is the one moment the container might not exist.
  [ -n "$(container_state)" ] || die "Container '${CONTAINER}' does not exist — run: ./scripts/n8n.sh start"
  podman logs -f "$CONTAINER"
}

cmd_status() {
  command -v podman >/dev/null || die "Podman not found — install it with: brew install podman"
  if ! podman info >/dev/null 2>&1; then
    warn "Podman machine is not reachable. Start it with: ./scripts/db.sh machine"
    return
  fi
  local state; state=$(container_state)
  if [ -z "$state" ]; then
    warn "No container named '${CONTAINER}' — run: ./scripts/n8n.sh start"
    return
  fi
  echo "container: ${CONTAINER}  state=${state}"
  if curl -sf -o /dev/null "http://127.0.0.1:${PORT}/rest/settings"; then
    ok "editor answering on http://localhost:${PORT}"
  else
    warn "container exists but the editor is not answering on :${PORT}"
  fi
}

case "${1:-}" in
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  reset)  cmd_reset ;;
  logs)   cmd_logs ;;
  status) cmd_status ;;
  *)      echo "Usage: $0 {start|stop|reset|logs|status}"; exit 1 ;;
esac
