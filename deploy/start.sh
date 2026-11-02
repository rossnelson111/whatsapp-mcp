#!/usr/bin/env bash
# Runs the three processes in one container. If any of them dies, the
# container exits and the platform restarts it.
set -euo pipefail

: "${ACCESS_KEY:?ACCESS_KEY must be set}"
export DATA_DIR="${DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"

# Bridge (Go): talks to WhatsApp, stores everything under $DATA_DIR/store
export WHATSAPP_BRIDGE_PORT=8080
export WEBHOOK_ENABLED=false
export WHATSAPP_DEVICE_NAME="${WHATSAPP_DEVICE_NAME:-Claude}"

# MCP server (Python): the tools Claude calls, loopback only
export WHATSAPP_DB_PATH="$DATA_DIR/store/messages.db"
export WHATSMEOW_DB_PATH="$DATA_DIR/store/whatsapp.db"
export WHATSAPP_API_URL="http://127.0.0.1:8080/api"
export WHATSAPP_MCP_TRANSPORT=http
export WHATSAPP_MCP_HOST=127.0.0.1
export WHATSAPP_MCP_PORT=8000

BRIDGE_ARGS=()
if [ "${FULL_HISTORY:-false}" = "true" ]; then
  BRIDGE_ARGS+=(--full-history-pair)
fi

(cd "$DATA_DIR" && exec /app/bin/whatsapp-bridge "${BRIDGE_ARGS[@]}") &

# The bridge writes its API token on first start; the MCP server needs it.
for _ in $(seq 1 60); do
  [ -s "$DATA_DIR/store/.bridge-token" ] && break
  sleep 1
done

/app/whatsapp-mcp-server/.venv/bin/python /app/whatsapp-mcp-server/main.py &
/app/gateway-venv/bin/python /app/deploy/gateway.py &

wait -n
echo "A process exited; stopping container so it restarts." >&2
exit 1
