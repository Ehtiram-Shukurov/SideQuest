# Implementation status

Honest status as of the P7 hardening commit. "Verified" says how: with the scripted model and
synthetic data, in the in-app browser, or against a live service. See `docs/LIVE_RESULTS.md` for
runs on real Gemini.

## What exists

| Area | Status | Notes |
|---|---|---|
| Contracts, validator (14 checks), schedule builder | done | Pass / fail / unknown for every hard check; unknown stays unknown. |
| Evidence-aware confidence | done | `verified` / `community_data` / `mixed`, plus a "verify before you go" list with source links. Labelling only: verdicts never change. |
| Agent loop | done | Bounded; cancellable; compact protocol (about 3 tool calls per plan); seven focused skills loaded into the prompt; group skill only for group trips. |
| Model adapter (Gemini) | done | Free-tier friendly: daily-quota fallback across models, burst retries, no waiting on daily limits. |
| Places / weather | live | OpenStreetMap (Overpass) and Open-Meteo, keyless. Hours/prices are community data. |
| Routes | live | FOSSGIS OSRM (foot/bike/car), one batched request per plan, cached, 1 req/s limit; labelled straight-line fallback. |
| Solo journey | done | Required location; answerable questions; replan with diff and accept/reject; rain preference; quest tracking in the browser. |
| Persistence | done | Plan versions, history, restore (re-validated, saved as a new version), accepted pointer and lock flags survive a restart. |
| Sharing / export | done | Revocable read-only link (no start location, budget, totals, issues or model text); Google Maps directions; `.ics`. |
| Group planning | done (basic) | Invites, shared availability, overlap window, questions to people left out, privacy, stale-proposal protection, persistence, share/export/delete. |
| Progress | done | Server-sent events with polling fallback. |
| Evaluation | done (opt-in) | Six held-out scenarios; `python -m server.eval.run`. |
| Hardening | done | CSP without inline scripts, rate/size limits, no CORS, token redaction in logs. See `docs/SECURITY.md`. |
| UI | done | Responsive workspace, light/dark, mobile tabs; accessibility audit (labels, headings, contrast, targets). |

## Not built
- Group: split activities, per-person start locations, group map, answerable planner questions, live (non-polling) updates, a leak guard on the model's explanation.
- Multi-day trips, lodging, events, intercity; real prices and verified hours (needs paid providers).
- Deployment; automated browser tests; a shared rate limiter.
- A pending replan proposal and a paused model conversation do not survive a restart (they appear in history as superseded).

## Tests
`uv run pytest -q`: all tests use the scripted model, synthetic data and mocked HTTP; no keys or network.

## Integration status

| Integration | Status |
|---|---|
| Gemini | verified live: solo plan, clarifying question + structured answer, replan + accept, each as a single run on `gemini-3.5-flash-lite` (see `docs/LIVE_RESULTS.md`); group flow and the fallback models not run live |
| Overpass (places) | verified live |
| Open-Meteo (weather) | verified live |
| FOSSGIS routing | verified live (matrix endpoint, all three profiles) |
| Postgres / Supabase | not used (SQLite instead) |
