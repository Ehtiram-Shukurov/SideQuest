"""Hardening for the web app: security headers + CSP, per-IP rate limits, request size limits,
and log redaction so bearer tokens in URLs never reach the access log.

Decisions (documented in docs/SECURITY.md):
- CORS: none. The page and API share an origin; no CORS middleware is installed, so browsers refuse
  cross-origin reads. Do not add one without a reason.
- CSP: scripts only from this origin and unpkg (Leaflet, with SRI). No inline script, no eval.
  Styles still allow 'unsafe-inline' because the markup has inline style attributes.
- Rate limits are in-memory and per process: fine for one server, not a shared limiter.
- Behind a reverse proxy set SIDEQUEST_TRUST_PROXY=1 so the client IP is read from X-Forwarded-For."""
from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://unpkg.com",
    "style-src 'self' 'unsafe-inline' https://unpkg.com",
    "img-src 'self' data: https://unpkg.com https://tile.openstreetmap.org https://*.tile.openstreetmap.org",
    "connect-src 'self'",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",  # OSM tile servers want a referer
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
MAX_BODY = 64 * 1024
BODY_METHODS = {"POST", "PUT", "PATCH"}

# (path regex, limit per minute per IP). First match wins; unmatched mutating requests use the default.
LIMITS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^/api/invites/"), 20),  # joining / peeking at invites: slows token guessing
    (re.compile(r"^/api/shared/"), 60),
    (re.compile(r"^/api/plans$|/plan$|/replan$|/answer$"), 12),  # these start model runs
]
DEFAULT_MUTATING, DEFAULT_READ = 60, 600


def client_ip(request: Request) -> str:
    if os.environ.get("SIDEQUEST_TRUST_PROXY") == "1":
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class Hardening(BaseHTTPMiddleware):
    def __init__(self, app, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(app)
        self._clock = clock
        self._hits: dict[tuple[str, str], tuple[float, int]] = {}

    def _limited(self, ip: str, request: Request) -> bool:
        path, mutating = request.url.path, request.method != "GET"
        limit = next((n for rx, n in LIMITS if rx.search(path) and (mutating or "/invites/" in path or "/shared/" in path)),
                     DEFAULT_MUTATING if mutating else DEFAULT_READ)
        bucket = "mut" if mutating else "read"
        key, now = (ip, f"{bucket}:{limit}:{next((rx.pattern for rx, n in LIMITS if rx.search(path)), '')}"), self._clock()
        start, count = self._hits.get(key, (now, 0))
        if now - start >= 60:
            start, count = now, 0
        self._hits[key] = (start, count + 1)
        if len(self._hits) > 5000:  # keep memory bounded
            self._hits = {k: v for k, v in self._hits.items() if now - v[0] < 60}
        return count + 1 > limit

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith("/api/"):
            if request.method in BODY_METHODS:
                cl = request.headers.get("content-length")
                if cl is None:
                    return self._seal(JSONResponse({"detail": "Content-Length is required"}, status_code=411))
                if not cl.isdigit() or int(cl) > MAX_BODY:
                    return self._seal(JSONResponse({"detail": "Request body too large"}, status_code=413))
            if self._limited(client_ip(request), request):
                return self._seal(JSONResponse({"detail": "Too many requests. Slow down and try again in a minute."},
                                               status_code=429, headers={"Retry-After": "60"}))
        response = await call_next(request)
        return self._seal(response, request.url.path)

    @staticmethod
    def _seal(response: Response, path: str = "/api/") -> Response:
        for k, v in HEADERS.items():
            response.headers[k] = v
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"  # plans and tokens are private
        return response


# --- log redaction ---------------------------------------------------------------------------

_SECRET_PATHS = re.compile(r"(/api/(?:invites|shared)/)[^/\s?\"]+")
_SECRET_QUERY = re.compile(r"([?&](?:share|join)=)[^&\s\"]+")


def redact(text: str) -> str:
    """Mask invite / share tokens in a URL or log line."""
    return _SECRET_QUERY.sub(r"\1[redacted]", _SECRET_PATHS.sub(r"\1[redacted]", text))


class RedactTokens(logging.Filter):
    """Applied to uvicorn's access logger: request lines carry tokens in the path and query."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install_log_redaction() -> None:
    for name in ("uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        if not any(isinstance(f, RedactTokens) for f in lg.filters):
            lg.addFilter(RedactTokens())
