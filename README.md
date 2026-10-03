# SideQuest

Turns available time, preferences, and practical constraints into an itinerary users can inspect, change, and follow. Solo and group trips share one engine.

**Status: MVP with a replayable demo.** Implemented and tested (57 tests): shared contracts, the deterministic planner/validator, the tool-calling agent loop (Gemini, with a scripted model for tests), live place and weather data (OpenStreetMap, Open-Meteo), and a single-page web app that requires the device location and tracks it during the quest. **New:** `fixtures/demo/replay-run.json` is a labeled recording of one full agent run — open `?replay=1` for a bulletproof offline demo that needs no API keys (see [docs/DEMO.md](docs/DEMO.md)). **Not yet verified:** a complete live run (real Gemini + live data) ending in a saved plan in the browser. Free-tier Gemini limits (for example `gemini-3.8-flash` allows only 20 requests/day) can stop live runs; the default model is `gemini-3.5-flash-lite`. See [docs/CHECKLIST.md](docs/CHECKLIST.md) and [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md).

## Demo (no keys needed)

```bash
uv sync
uv run uvicorn server.api.app:app --port 8000   # open http://localhost:8000/?replay=1
```

`?replay=1` plays back `fixtures/demo/replay-run.json`: a recorded Saturday-afternoon
run in downtown Minneapolis (Mill Ruins Park → Stone Arch Bridge → Father Hennepin
Bluff Park → Aster Cafe), with the 12 real agent events, a validated provisional plan
(13/14 checks pass; the café's unknown price is marked unknown, not assumed free), and
a visible REPLAY badge. It proves the shape of the system — agent proposes, code
validates, unknowns stay unknown — without depending on any live model. It is a
recording, labeled as such; it does not prove a live run. Full script and fallbacks:
[docs/DEMO.md](docs/DEMO.md).

## Team
TODO

## Summary
SideQuest's model proposes; deterministic code checks. Every plan is validated pass / fail / unknown against hard constraints (time, per-person budget, opening hours, travel, accessibility, locked commitments). Missing data stays unknown rather than being assumed fine.

## How to Run
Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
cp .env.example .env   # then set GEMINI_API_KEY (never commit .env)
uv run uvicorn server.api.app:app --port 8000   # open http://localhost:8000
```

Tests need no keys. The app needs `GEMINI_API_KEY`; "Demo" data mode uses invented venues, "Live" uses OpenStreetMap near your location.

## Layout
- `server/models/` Pydantic contracts (trip, member, place, plan, evidence, validation)
- `server/planning/` money allocation, schedule assembly, validators
- `server/agent/` model adapter (Gemini), scripted test model, agent loop, demo and smoke scripts
- `server/tools/` the agent's typed tools; `server/providers/` live (OpenStreetMap, Open-Meteo) and synthetic data
- `server/api/` FastAPI app; `web/` single-page UI (location required)
- `skills/` instructions loaded into the agent's system prompt
- `tests/` 57 tests (scripted model, synthetic data; no keys needed)
- `docs/` build plan and checklist (the checklist predates the MVP and is partly out of date)
- `db/migrations/`, `fixtures/` empty placeholders
