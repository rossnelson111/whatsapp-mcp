# --- Build the Go bridge (needs CGO for SQLite) ---
FROM golang:1.26-bookworm AS bridge
WORKDIR /src
COPY whatsapp-bridge/go.mod whatsapp-bridge/go.sum ./
RUN go mod download
COPY whatsapp-bridge/ ./
RUN CGO_ENABLED=1 go build -trimpath -ldflags="-s -w" -o /out/whatsapp-bridge .

# --- Runtime ---
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=bridge /out/whatsapp-bridge /app/bin/whatsapp-bridge

# MCP server with its locked dependencies
COPY whatsapp-mcp-server/ /app/whatsapp-mcp-server/
RUN cd /app/whatsapp-mcp-server && UV_PROJECT_ENVIRONMENT=.venv uv sync --frozen --no-dev

# Gateway (the only public port)
RUN uv venv /app/gateway-venv && \
    uv pip install --python /app/gateway-venv/bin/python "aiohttp==3.12.15" "segno==1.6.6"
COPY deploy/ /app/deploy/
RUN chmod +x /app/deploy/start.sh

ENV DATA_DIR=/data
EXPOSE 8888
CMD ["/app/deploy/start.sh"]
