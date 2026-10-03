# SideQuest

Turns available time, preferences, and practical constraints into an itinerary users can inspect, change, and follow. Solo and group trips share one engine.

**Status: early foundation.** The shared contracts and the deterministic planning/validation core are implemented and tested. There is no agent, no provider integration, no API server and no UI yet. See [docs/CHECKLIST.md](docs/CHECKLIST.md) for exactly what exists, and [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) for the full specification.

## Team
TODO

## Summary
SideQuest's model proposes; deterministic code checks. Every plan is validated pass / fail / unknown against hard constraints (time, per-person budget, opening hours, travel, accessibility, locked commitments). Missing data stays unknown rather than being assumed fine.

## How to Run
Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
```

No API keys are needed for the current code. `.env.example` lists the keys later work packages will need.

## Layout
- `server/models/` Pydantic contracts (trip, member, place, plan, evidence, validation)
- `server/planning/` money allocation, schedule assembly, validators
- `server/agent/`, `server/tools/`, `server/providers/` placeholders for later work packages
- `web/` placeholder for the React + TypeScript interface
- `db/migrations/` placeholder for the PostgreSQL schema
- `skills/` placeholder for agent skill files
- `fixtures/` placeholder for recorded provider responses (current test data is synthetic, in `tests/helpers.py`)
- `tests/`
- `docs/`
