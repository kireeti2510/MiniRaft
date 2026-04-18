#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  start-local.sh  —  Run MiniRaft without Docker
#
#  Starts:
#    replica1  on :5001
#    replica2  on :5002
#    replica3  on :5003
#    gateway   on :4000
#    frontend  served from a lightweight http-server on :3000
#
#  Usage:
#    chmod +x start-local.sh
#    ./start-local.sh
#
#  Stop everything:
#    ./start-local.sh stop
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Ensure Homebrew Node.js is on PATH (Apple Silicon + Intel Macs)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(DIR=$(dirname "$0"); echo "$DIR")" && pwd)"
PIDFILE="$SCRIPT_DIR/.local-pids"

PEER_REPLICA1_URL="http://localhost:5001"
PEER_REPLICA2_URL="http://localhost:5002"
PEER_REPLICA3_URL="http://localhost:5003"
PEER_REPLICA4_URL="http://localhost:5004"
PEER_REPLICA5_URL="http://localhost:5005"

# ── Helpers ──────────────────────────────────────────────────────────────────
die() { echo "ERROR: $*" >&2; exit 1; }
need() { command -v "$1" &>/dev/null || die "'$1' not found. Install Node.js (https://nodejs.org)."; }

log() { printf "\033[32m[start-local]\033[0m %s\n" "$*"; }
warn() { printf "\033[33m[start-local]\033[0m %s\n" "$*"; }

# ── Stop mode ────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "stop" ]]; then
  if [[ -f "$PIDFILE" ]]; then
    log "Stopping MiniRaft processes..."
    while IFS= read -r pid; do
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" && log "Killed PID $pid"
      fi
    done < "$PIDFILE"
    rm -f "$PIDFILE"
    log "All processes stopped."
  else
    warn "No PID file found. Nothing to stop."
  fi
  exit 0
fi

# ── Prerequisites ─────────────────────────────────────────────────────────────
need node
need npm

# ── Install deps if needed ───────────────────────────────────────────────────
for dir in replica1 replica2 replica3 replica4 replica5 gateway; do
  if [[ ! -d "$SCRIPT_DIR/$dir/node_modules" ]]; then
    log "Installing deps for $dir..."
    (cd "$SCRIPT_DIR/$dir" && npm install --silent)
  fi
done

# ── Cleanup on exit ───────────────────────────────────────────────────────────
PIDS=()
cleanup() {
  log "Shutting down..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  rm -f "$PIDFILE"
}
trap cleanup EXIT INT TERM

# ── Start Replicas ────────────────────────────────────────────────────────────
log "Starting replica1 on :5001..."
(cd "$SCRIPT_DIR/replica1" && \
  REPLICA_ID=replica1 PORT=5001 GATEWAY_URL=http://localhost:4000 \
  PEER_REPLICA1_URL=$PEER_REPLICA1_URL \
  PEER_REPLICA2_URL=$PEER_REPLICA2_URL \
  PEER_REPLICA3_URL=$PEER_REPLICA3_URL \
  PEER_REPLICA4_URL=$PEER_REPLICA4_URL \
  PEER_REPLICA5_URL=$PEER_REPLICA5_URL \
  node index.js 2>&1 | sed 's/^/[replica1] /' ) &
PIDS+=($!)

log "Starting replica2 on :5002..."
(cd "$SCRIPT_DIR/replica2" && \
  REPLICA_ID=replica2 PORT=5002 GATEWAY_URL=http://localhost:4000 \
  PEER_REPLICA1_URL=$PEER_REPLICA1_URL \
  PEER_REPLICA2_URL=$PEER_REPLICA2_URL \
  PEER_REPLICA3_URL=$PEER_REPLICA3_URL \
  PEER_REPLICA4_URL=$PEER_REPLICA4_URL \
  PEER_REPLICA5_URL=$PEER_REPLICA5_URL \
  node index.js 2>&1 | sed 's/^/[replica2] /' ) &
PIDS+=($!)

log "Starting replica3 on :5003..."
(cd "$SCRIPT_DIR/replica3" && \
  REPLICA_ID=replica3 PORT=5003 GATEWAY_URL=http://localhost:4000 \
  PEER_REPLICA1_URL=$PEER_REPLICA1_URL \
  PEER_REPLICA2_URL=$PEER_REPLICA2_URL \
  PEER_REPLICA3_URL=$PEER_REPLICA3_URL \
  PEER_REPLICA4_URL=$PEER_REPLICA4_URL \
  PEER_REPLICA5_URL=$PEER_REPLICA5_URL \
  node index.js 2>&1 | sed 's/^/[replica3] /' ) &
PIDS+=($!)

log "Starting replica4 on :5004..."
(cd "$SCRIPT_DIR/replica4" && \
  REPLICA_ID=replica4 PORT=5004 GATEWAY_URL=http://localhost:4000 \
  PEER_REPLICA1_URL=$PEER_REPLICA1_URL \
  PEER_REPLICA2_URL=$PEER_REPLICA2_URL \
  PEER_REPLICA3_URL=$PEER_REPLICA3_URL \
  PEER_REPLICA4_URL=$PEER_REPLICA4_URL \
  PEER_REPLICA5_URL=$PEER_REPLICA5_URL \
  node index.js 2>&1 | sed 's/^/[replica4] /' ) &
PIDS+=($!)

log "Starting replica5 on :5005..."
(cd "$SCRIPT_DIR/replica5" && \
  REPLICA_ID=replica5 PORT=5005 GATEWAY_URL=http://localhost:4000 \
  PEER_REPLICA1_URL=$PEER_REPLICA1_URL \
  PEER_REPLICA2_URL=$PEER_REPLICA2_URL \
  PEER_REPLICA3_URL=$PEER_REPLICA3_URL \
  PEER_REPLICA4_URL=$PEER_REPLICA4_URL \
  PEER_REPLICA5_URL=$PEER_REPLICA5_URL \
  node index.js 2>&1 | sed 's/^/[replica5] /' ) &
PIDS+=($!)

# Wait for replicas to boot before starting gateway
sleep 1

# ── Start Gateway ─────────────────────────────────────────────────────────────
log "Starting gateway on :4000..."
(cd "$SCRIPT_DIR/gateway" && \
  PORT=4000 \
  PEER_REPLICA1_URL=$PEER_REPLICA1_URL \
  PEER_REPLICA2_URL=$PEER_REPLICA2_URL \
  PEER_REPLICA3_URL=$PEER_REPLICA3_URL \
  PEER_REPLICA4_URL=$PEER_REPLICA4_URL \
  PEER_REPLICA5_URL=$PEER_REPLICA5_URL \
  node index.js 2>&1 | sed 's/^/[gateway]  /' ) &
PIDS+=($!)

# ── Serve Frontend ────────────────────────────────────────────────────────────
log "Serving frontend on :3000..."
# Use npx http-server (no install needed) to serve the static frontend
npx --yes http-server "$SCRIPT_DIR/frontend" -p 3000 --silent &
PIDS+=($!)

# ── Save PIDs ─────────────────────────────────────────────────────────────────
printf "%s\n" "${PIDS[@]}" > "$PIDFILE"

log ""
log "╔══════════════════════════════════════════════════╗"
log "║  MiniRaft running (no Docker) — 5 replicas       ║"
log "║                                                  ║"
log "║  Frontend   →  http://localhost:3000             ║"
log "║  Gateway    →  http://localhost:4000/health      ║"
log "║  Replica 1  →  http://localhost:5001/status      ║"
log "║  Replica 2  →  http://localhost:5002/status      ║"
log "║  Replica 3  →  http://localhost:5003/status      ║"
log "║  Replica 4  →  http://localhost:5004/status      ║"
log "║  Replica 5  →  http://localhost:5005/status      ║"
log "║                                                  ║"
log "║  Press Ctrl+C to stop all processes.             ║"
log "╚══════════════════════════════════════════════════╝"

# Keep script alive; cleanup fires on Ctrl+C via trap
wait
