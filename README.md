# SideQuest

Turns your time, budget, transport, interests and real location into an outing plan that code has checked, and tells you what it could not verify. A model proposes stops; deterministic code schedules them and validates every hard constraint as pass, fail or unknown.

## What it does
- **Plans near you:** needs your location; real places from OpenStreetMap, a real forecast (Open-Meteo), real walking/bike/car route times (FOSSGIS OSRM).
- **Checks, doesn't guess:** time window, the way home, opening hours, your budget, accessibility, rain limit, locked stops. Missing facts stay "unknown"; plans say whether they rest on community map data.
- **Interactive:** answer the planner's questions with one click, swap/remove/lock stops, change time or budget, review a diff before accepting a change.
- **Saved:** plan versions and history survive a restart; restore an old version (it is re-checked first); share a read-only link; export to Google Maps directions or a calendar file.
- **Group trips (basic):** invite friends, collect everyone's availability and preferences, plan in the window that fits the most people, ask anyone left out. Budgets and needs stay private.
- **Honest modes:** Live data, labeled Demo data (invented venues), and a labeled Replay that needs no keys.

## Team
**Team name:** Offscript. See `docs/SUBMISSION.md` for members.

## Summary
SideQuest's model proposes; deterministic code checks. Every plan is validated pass / fail / unknown against hard constraints, and the plan says how well each fact is sourced. See `docs/CHECKLIST.md` for exactly what is built and verified, `docs/SECURITY.md` for hardening decisions, and `docs/LIVE_RESULTS.md` for runs on real Gemini.

## How to run
Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest                      # no keys or network needed
cp .env.example .env               # then set GEMINI_API_KEY (never commit .env)
uv run uvicorn server.api.app:app --port 8000   # open http://localhost:8000
```

- **No key?** Open `http://localhost:8000/?replay=1` for a recorded demo, or pick "Demo" data (still needs a model key).
- **Free tier:** the default model is `gemini-3.5-flash-lite`; when a model's daily quota runs out, new runs move to the models in `GEMINI_FALLBACK_MODELS`.
- **Evaluate:** `uv run python -m server.eval.run` runs six scenarios against your model (spends quota).
- **Routing:** set `SIDEQUEST_ROUTING=off` to use straight-line estimates only.
- **Database:** `data/sidequest.db` (SQLite, created on first use, git-ignored). Override with `SIDEQUEST_DB`.

## Layout
- `server/models/` contracts; `server/planning/` scheduling, validators, overlap, export
- `server/agent/` model adapter, agent loop, skills loader; `skills/` the seven skill files in the prompt
- `server/tools/` the agent's tools; `server/providers/` Overpass, Open-Meteo, FOSSGIS routing, synthetic data
- `server/api/` FastAPI app and security middleware; `server/groups/` group trips and the SQLite store
- `server/eval/` scenario evaluation; `web/` the single-page UI (`index.html`, `app.js`, `app.css`)
- `tests/` all tests (scripted model, synthetic data, mocked HTTP); `docs/` plan, status, security, live results

## Deploy (free)

`render.yaml` is a Render Blueprint on the free plan. In Render choose New > Blueprint, pick this repo, and paste `GEMINI_API_KEY` when asked (never commit it). Notes: the free plan sleeps after idle time (first request is slow) and its disk is temporary, so saved plans, share links and owner tokens are lost on every restart. Treat the deployed copy as a demo. Gemini's free tier may use prompts to improve Google's products, so enter only non-sensitive text.
