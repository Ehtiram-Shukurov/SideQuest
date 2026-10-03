# SideQuest

Turns available time, preferences, and practical constraints into an itinerary users can inspect, change, and follow. Solo and group trips share one engine.

Status: planning. The full specification is in [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md). Nothing here is implemented yet.

## Team
TODO

## Summary
TODO

## How to Run
TODO

## Layout
- `web/` React + TypeScript interface
- `server/` FastAPI backend (`agent/`, `tools/`, `planning/`, `providers/`, `models/`)
- `db/migrations/` PostgreSQL schema
- `skills/` agent skill files
- `fixtures/` recorded provider responses and scenarios
- `tests/`
- `docs/`
