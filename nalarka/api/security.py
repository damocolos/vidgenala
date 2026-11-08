"""Local-first request guard: Host/Origin validation and optional API token.

Why: the API has no user accounts and can read/write provider API keys and start paid
renders. Any web page open in the user's browser could call ``http://127.0.0.1:8000``
(CSRF / DNS rebinding). This middleware only lets requests through that address the
server by a known host name and, when an Origin header is present, come from a known origin.

Env:
  NALARKA_ALLOWED_HOSTS    comma list of extra host names (default: localhost,127.0.0.1,::1)
  NALARKA_ALLOWED_ORIGINS  comma list of extra full origins (e.g. https://studio.example.com)
  NALARKA_API_TOKEN        if set, /api/* needs it (X-Nalarka-Token / Bearer / nalarka_token cookie)
"""
import hmac
import os
from typing import Iterable, Optional, Set
from urllib.parse import urlsplit, parse_qs

from starlette.responses import JSONResponse, Response

DEFAULT_HOSTS = ("localhost", "127.0.0.1", "::1")
TOKEN_COOKIE = "nalarka_token"


def _split(env: str) -> Set[str]:
    return {x.strip().lower() for x in os.environ.get(env, "").split(",") if x.strip()}


def _host_only(host_header: str) -> str:
    h = (host_header or "").strip().lower()
    if h.startswith("["):  # [::1]:8000
        return h[1:].split("]")[0]
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


class LocalSecurityMiddleware:
    def __init__(self, app, allowed_hosts: Optional[Iterable[str]] = None,
                 allowed_origins: Optional[Iterable[str]] = None,
                 token: Optional[str] = None):
        self.app = app
        self.hosts = {h.lower() for h in (allowed_hosts if allowed_hosts is not None else DEFAULT_HOSTS)}
        self.hosts |= _split("NALARKA_ALLOWED_HOSTS")
        self.origins = {o.lower().rstrip("/") for o in (allowed_origins or [])} | _split("NALARKA_ALLOWED_ORIGINS")
        self.token = token if token is not None else os.environ.get("NALARKA_API_TOKEN", "")

    def _origin_ok(self, origin: str) -> bool:
        o = origin.lower().rstrip("/")
        if o in self.origins:
            return True
        parts = urlsplit(o)
        return parts.scheme in ("http", "https") and (parts.hostname or "") in self.hosts

    def _token_ok(self, headers: dict, cookie: str) -> bool:
        supplied = headers.get("x-nalarka-token", "")
        auth = headers.get("authorization", "")
        if not supplied and auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        if not supplied:
            for part in cookie.split(";"):
                k, _, v = part.strip().partition("=")
                if k == TOKEN_COOKIE:
                    supplied = v
        return bool(supplied) and hmac.compare_digest(supplied.encode(), self.token.encode())

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        deny: Optional[Response] = None

        if "*" not in self.hosts and _host_only(headers.get("host", "")) not in self.hosts:
            deny = JSONResponse({"detail": "Host not allowed."}, status_code=400)
        elif "origin" in headers and not self._origin_ok(headers["origin"]):
            deny = JSONResponse({"detail": "Cross-origin request blocked."}, status_code=403)
        elif self.token and scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/auth":  # exchange ?token= for an HttpOnly cookie
                supplied = parse_qs(scope.get("query_string", b"").decode()).get("token", [""])[0]
                if supplied and hmac.compare_digest(supplied.encode(), self.token.encode()):
                    resp = Response(status_code=204)
                    resp.set_cookie(TOKEN_COOKIE, supplied, httponly=True, samesite="strict")
                else:
                    resp = JSONResponse({"detail": "Invalid token."}, status_code=401)
                return await resp(scope, receive, send)
            if path.startswith("/api/") and scope.get("method") != "OPTIONS" \
                    and not self._token_ok(headers, headers.get("cookie", "")):
                deny = JSONResponse({"detail": "Missing or invalid API token."}, status_code=401)

        if deny is not None:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await deny(scope, receive, send)
        await self.app(scope, receive, send)
