# Security notes

What is protected, what is not, and the decisions behind it. Last reviewed with the code at the P7
hardening commit.

## Decisions

| Area | Decision | Why |
|---|---|---|
| CORS | None. No CORS middleware; the page and API share an origin. | Cross-origin pages cannot read plans or call the API with a user's tokens. A test asserts no `Access-Control-Allow-Origin`. |
| CSP | `script-src 'self' https://unpkg.com` (no inline script, no eval); `style-src` allows `'unsafe-inline'`; `frame-ancestors 'none'`; `object-src 'none'`; `base-uri 'none'`. | Tokens live in `localStorage`, so XSS is the main risk. The JS and CSS were moved to `/static/app.js` and `/static/app.css` so scripts can be locked down. Styles keep `'unsafe-inline'` because the markup has inline `style` attributes. |
| Other headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` (OSM tile servers want a referer), `Permissions-Policy` (geolocation only), `Cross-Origin-Opener-Policy`. `Cache-Control: no-store` on every `/api/` response. | Plans, tokens and locations are private. |
| Rate limits | Per IP, per process, one-minute windows: 12/min for calls that start a model run, 20/min for invite/join, 60/min for writes, 600/min for reads. 429 with `Retry-After`. | Protects the free-tier model quota and slows token guessing. In memory only: not shared across processes. |
| Request size | `/api/` writes need `Content-Length` (411) and at most 64 KB (413). Fields also have their own max lengths. | Bounds memory and abuse. |
| Tokens | Solo owner tokens and group member tokens are stored only as SHA-256 hashes. Invite and share tokens are stored in plain text so the owner can copy them again; both are revocable. Roles are resolved from the token on the server. | A database leak cannot be replayed as an owner/member token. |
| Output | All dynamic text is rendered with `textContent` / text nodes. Links from data are only made for `https://` URLs. | Blocks injected markup from map data or other members. |
| Logging | Invite and share tokens in URLs (`/api/invites/...`, `/api/shared/...`, `?share=`, `?join=`) are masked in uvicorn's access log. Request bodies are never logged. | URLs are logged by default and tokens are credentials. |
| Dependencies | `pip-audit` on the 18 runtime dependencies reported no known vulnerabilities. The only frontend dependency is Leaflet 1.9.4 from unpkg, pinned with an integrity hash. | Checked on 2026-10-03; re-run before any release. |

## Behind a reverse proxy
Set `SIDEQUEST_TRUST_PROXY=1` so the client IP comes from `X-Forwarded-For`. Terminate HTTPS at the
proxy. Do not set it when the app is exposed directly, or clients could spoof their IP.

## Known gaps (not fixed)
- Rate limiting is in memory and per process.
- Tokens in `localStorage` are readable by any script on the origin; the CSP is the mitigation.
- Style attributes still need `'unsafe-inline'`.
- The Gemini API key is read from `.env` on the server; there is no secrets manager.
- Free-tier Gemini may use prompts to improve Google's products. Use paid-tier or synthetic data for
  anything private.
- No automated browser (Playwright) tests: installing that was not approved. Behaviour was checked in
  the in-app browser and with the API test client.
- No deployment has been done. Do not expose the app publicly without HTTPS and a review of this list.
