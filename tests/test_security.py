"""P7: security headers/CSP, rate limits, size limits, no CORS, and log redaction."""
from __future__ import annotations

import logging
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.agent.scripted import ScriptedProvider
from server.api.app import create_app
from server.api.security import CSP, Hardening, RedactTokens, redact

from .test_api import BODY, NOW


def client():
    return TestClient(create_app(lambda: ScriptedProvider([]), lambda: NOW))


def test_headers_csp_and_the_page_has_no_inline_script_or_event_handler():
    c = client()
    r = c.get("/")
    assert r.headers["content-security-policy"] == CSP
    script_src = next(d for d in CSP.split("; ") if d.startswith("script-src"))
    assert "'unsafe-inline'" not in script_src and "unsafe-eval" not in CSP  # scripts: this origin + unpkg only
    assert "frame-ancestors 'none'" in CSP and "object-src 'none'" in CSP and "base-uri 'none'" in CSP
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "geolocation=(self)" in r.headers["permissions-policy"]  # the app needs location, nothing else
    html = r.text
    inline = [m for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>", html)]
    assert not inline and '<script src="/static/app.js">' in html  # no inline script at all
    assert not re.search(r"\son[a-z]+=\"", html) and "javascript:" not in html
    assert c.get("/static/app.js").status_code == 200 and c.get("/static/app.css").status_code == 200
    assert "no-store" in c.get("/api/health").headers["cache-control"]  # API responses are never cached


def test_no_cors_is_granted_to_other_origins():
    c = client()
    pre = c.options("/api/plans", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in pre.headers
    assert "access-control-allow-origin" not in c.get("/api/health", headers={"Origin": "https://evil.example"}).headers


def test_request_size_and_content_length_are_enforced_on_api_writes():
    c = client()
    big = {**BODY, "request": "x" * 70_000}
    assert c.post("/api/plans", json=big).status_code == 413
    ok = c.post("/api/plans", json={**BODY, "request": "a short walk"})
    assert ok.status_code in (200, 503)  # accepted past the size gate (503 only if no model is configured)
    assert c.post("/api/plans", content=b"", headers={"transfer-encoding": "chunked"}).status_code in (411, 422, 400, 413)


def test_rate_limits_apply_per_ip_per_bucket_and_reset_after_a_minute():
    now = [0.0]
    app = FastAPI()
    app.add_middleware(Hardening, clock=lambda: now[0])

    @app.post("/api/plans")
    def plans() -> dict:
        return {"ok": True}

    @app.get("/api/invites/{token}")
    def invite(token: str) -> dict:
        return {"ok": True}

    c = TestClient(app)
    codes = [c.post("/api/plans", json={}).status_code for _ in range(14)]
    assert codes[:12] == [200] * 12 and codes[12:] == [429, 429]  # the 12/min bucket for model-starting calls
    limited = c.post("/api/plans", json={})
    assert limited.headers["retry-after"] == "60" and "Too many" in limited.json()["detail"]
    assert [c.get("/api/invites/t").status_code for _ in range(22)].count(429) == 2  # 20/min on invite peeks
    now[0] = 61.0
    assert c.post("/api/plans", json={}).status_code == 200  # a new window


def test_tokens_are_redacted_from_urls_and_access_log_lines():
    assert redact("/api/invites/abc123XYZ/join") == "/api/invites/[redacted]/join"
    assert redact("GET /api/shared/tok-en_9/export.ics") == "GET /api/shared/[redacted]/export.ics"
    assert redact("/?share=SECRET&x=1") == "/?share=[redacted]&x=1" and redact("/?join=SECRET") == "/?join=[redacted]"
    assert redact("/api/trips/trip-1") == "/api/trips/trip-1"  # ordinary ids are untouched
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:1", "GET", "/?share=SECRET", "1.1", 200), None)
    assert RedactTokens().filter(rec) and "SECRET" not in rec.getMessage() and "[redacted]" in rec.getMessage()
