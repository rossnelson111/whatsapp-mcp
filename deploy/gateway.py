"""Public front door for the WhatsApp MCP container.

The only thing exposed to the internet. Everything else (the Go bridge on
:8080 and the MCP server on :8000) listens on 127.0.0.1 inside the container.

Routes (everything else returns 404):
  GET  /healthz                 -> "ok" (no details, for the platform health check)
  ANY  /<ACCESS_KEY>/mcp        -> proxied to the MCP server (streamable HTTP)
  GET  /<ACCESS_KEY>/setup      -> link status page with the WhatsApp QR code
  POST /<ACCESS_KEY>/setup/relink -> ask the bridge for a fresh QR code
"""

import hmac
import html
import os
import sys

import aiohttp
import segno
from aiohttp import web

ACCESS_KEY = os.environ.get("ACCESS_KEY", "").strip()
PORT = int(os.environ.get("PORT", "8888"))
DATA_DIR = os.environ.get("DATA_DIR", "/data")
TOKEN_PATH = os.path.join(DATA_DIR, "store", ".bridge-token")
MCP_UPSTREAM = "http://127.0.0.1:8000"
BRIDGE_UPSTREAM = "http://127.0.0.1:8080"

if len(ACCESS_KEY) < 32:
    sys.exit("ACCESS_KEY must be set and at least 32 characters long.")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


def key_ok(request: web.Request) -> bool:
    supplied = request.match_info.get("key", "")
    return hmac.compare_digest(supplied.encode(), ACCESS_KEY.encode())


def not_found() -> web.Response:
    return web.Response(status=404, text="Not found")


def bridge_token() -> str:
    with open(TOKEN_PATH, encoding="utf-8") as fh:
        return fh.read().strip()


async def healthz(_: web.Request) -> web.Response:
    return web.Response(text="ok")


async def proxy_mcp(request: web.Request) -> web.StreamResponse:
    if not key_ok(request):
        return not_found()

    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    # FastMCP only accepts loopback Host headers (DNS rebinding protection).
    headers["Host"] = "127.0.0.1:8000"
    body = await request.read()

    session: aiohttp.ClientSession = request.app["session"]
    async with session.request(
        request.method,
        f"{MCP_UPSTREAM}/mcp",
        params=request.query,
        headers=headers,
        data=body if body else None,
        allow_redirects=False,
    ) as upstream:
        resp = web.StreamResponse(status=upstream.status)
        for k, v in upstream.headers.items():
            if k.lower() not in HOP_BY_HOP:
                resp.headers[k] = v
        await resp.prepare(request)
        # Stream chunk by chunk so server-sent events reach Claude immediately.
        async for chunk in upstream.content.iter_any():
            await resp.write(chunk)
        await resp.write_eof()
        return resp


async def bridge_call(request: web.Request, method: str, path: str) -> dict:
    session: aiohttp.ClientSession = request.app["session"]
    async with session.request(
        method,
        f"{BRIDGE_UPSTREAM}{path}",
        headers={"Authorization": f"Bearer {bridge_token()}"},
    ) as r:
        return await r.json(content_type=None)


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>WhatsApp link</title>
<style>
 body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 480px;
        margin: 40px auto; padding: 0 16px; color: #111; background: #fff; }}
 .qr {{ background: #fff; padding: 16px; display: inline-block; }}
 .state {{ font-weight: 600; }}
 button {{ font-size: 16px; padding: 10px 16px; margin-top: 16px; }}
</style></head><body>
<h1>WhatsApp link</h1>
<p>State: <span class="state">{state}</span></p>
<p>{message}</p>
{qr}
<form method="post" action="setup/relink"><button>Get a new QR code</button></form>
<p><small>This page refreshes every 5 seconds.</small></p>
</body></html>"""


async def setup_page(request: web.Request) -> web.Response:
    if not key_ok(request):
        return not_found()
    try:
        status = await bridge_call(request, "GET", "/api/connection")
    except Exception as exc:  # bridge still starting, or token not written yet
        status = {"state": "starting", "message": f"Bridge not ready yet ({type(exc).__name__})."}

    qr_html = ""
    if status.get("qrCode") and not status.get("connected"):
        svg = segno.make(status["qrCode"], error="l").svg_inline(scale=6)
        qr_html = (
            f'<div class="qr">{svg}</div>'
            "<p>On your phone: WhatsApp &gt; Settings &gt; Linked Devices &gt; Link a device.</p>"
        )
    elif status.get("connected"):
        qr_html = "<p>Linked and connected. You can close this page.</p>"

    page = PAGE.format(
        state=html.escape(str(status.get("state", "unknown"))),
        message=html.escape(str(status.get("message", ""))),
        qr=qr_html,
    )
    return web.Response(text=page, content_type="text/html",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


async def relink(request: web.Request) -> web.Response:
    if not key_ok(request):
        return not_found()
    try:
        await bridge_call(request, "POST", "/api/connection/reconnect")
    except Exception:
        pass
    raise web.HTTPSeeOther(location=f"/{ACCESS_KEY}/setup")


async def on_startup(app: web.Application) -> None:
    app["session"] = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
        auto_decompress=False,
    )


async def on_cleanup(app: web.Application) -> None:
    await app["session"].close()


def main() -> None:
    app = web.Application(client_max_size=50 * 1024 * 1024)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/healthz", healthz)
    app.router.add_route("*", "/{key}/mcp", proxy_mcp)
    app.router.add_get("/{key}/setup", setup_page)
    app.router.add_post("/{key}/setup/relink", relink)
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None)


if __name__ == "__main__":
    main()
