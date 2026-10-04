# SideQuest: improvement plan

Written 2026-10-03 against `main` at `19339fa`. It covers what to improve, in what order, how to
know each piece is done, and what could go wrong. Effort uses S / M / L (small, a focused session /
medium / large, best split across sessions), not hours.

## 1. Where we are (honest baseline)

**Works and is tested with a scripted model and synthetic data (72 tests):** the deterministic
planner and 14-check validator, the tool-calling agent loop, solo planning with live OpenStreetMap
places and Open-Meteo weather, answerable clarifications, replan with diff and accept/reject, rain
preference, and group planning (invites, per-member inputs, overlap, questions, privacy, stale
proposals). A labeled replay mode works with no keys.

**Not proven:** anything past the first solo flow on real Gemini; clarification resume, replan and
groups have only run against a scripted model. Free-tier Gemini is 20 requests/day on the stronger
models and slow.

**Known weaknesses (the work below fixes these):**

| # | Weakness | Evidence in code |
|---|---|---|
| W1 | "Checked" overstates confidence: unverified map data is treated as known | `validators.py` ignores `Evidence.status`; `live.py` marks OSM facts `unverified` |
| W2 | Routes are straight-line x 1.3 estimates | `LiveWorld.estimate` |
| W3 | Many places have no parsed hours or price, so plans are often provisional | hours parsed for roughly half the places tested |
| W4 | Plans and sessions live in memory; a restart forgets them | `sessions` / `Runtime` dicts |
| W5 | One skill file, not a set; runs are slow (8-10 model calls) | `skills/`, runner |
| W6 | Group: no map, no per-person start, planner questions cannot be answered, polling, explanation text is not leak-checked, no split activities | `server/groups/routes.py` |
| W7 | No automated browser tests, no live-model evaluation | `tests/` |
| W8 | Local only: no deployment, rate limiting or hardening | `server/api/app.py` |
| W9 | Docs out of date (`docs/CHECKLIST.md` says group is "not started") | docs |

## 2. Principles (apply to every phase)

1. **The model proposes, code decides.** Never let a model output mark a plan valid, and never relax
   a hard check to make a demo look better.
2. **Unknown stays unknown.** New data must carry its source and status.
3. **Label what is real.** Live, demo, replay and recorded data stay visibly distinct.
4. **Privacy by construction.** New group features must keep per-person budget, access and dietary
   values out of other viewers' payloads and out of the model's view.
5. **Small verified steps.** Commit per step; run the whole test suite once per phase; add a few
   targeted tests, not a large suite, unless the phase says otherwise.
6. **Say what is unfinished.** Every phase ends with a short "not done / unverified" list.

## 3. Phases

Dependencies: P0 first. P1 and P2 are independent. P3 needs P0. P4 benefits from P0 and P2.
P5 needs P3. P6 is optional/stretch. P7 is last.

### Phase 0: Live verification and billing (S, needs the user)
**Why:** the largest unknown. Everything after the first solo flow was tested with a scripted model.

**Do**
- With billing enabled on the Gemini key, run live: (a) solo plan, (b) a plan that triggers a
  question, answer it by clicking a structured option, (c) a replan (swap and lock), (d) a 3-person
  group with one person left out and a "yes" answer.
- Fix whatever breaks (prompt wording, schema issues, Interactions API behaviour when resuming
  after `ask_user`, retries, token use).
- Choose and document the default model by measured latency, cost and plan quality
  (`GEMINI_MODEL`); record per-run tokens and seconds.
- Record one **real** run as the new replay fixture (replace the old recording; keep the label
  "recorded run, not live").

**Done when:** each of (a)-(d) ends with a validated proposal on real Gemini, a short results table
(latency, tokens, tool calls, outcome) is in `docs/`, and failures found are fixed or listed.

**Risks:** free-tier limits (use paid tier), model flakiness (add one retry/fallback model).

### Phase 1: Honest confidence labels (S-M)
**Why:** W1. A plan should say *how* it was checked.

**Do**
- Pass `ctx.evidence` into `validate_plan`. Each fact-based check (opening hours, price, step-free,
  weather) records its `basis`: `verified`, `community` (unverified) or `unknown`.
- Add `ValidationReport.confidence`: `verified`, `community_data` or `mixed`, plus a short
  "verify before you go" list (stops whose hours or prices rest on community data, with the source
  link already stored in `Evidence.url`).
- UI: replace the single "Checked" badge text with "Checked against community map data" when
  applicable; show a "verify" chip on affected stop cards; keep `provisional` for unknowns.
- Keep behaviour of hard checks unchanged (pass/fail/unknown); this is labelling, not new
  failures.

**Done when:** a plan whose hours come from OSM tags shows the community-data label, a plan with only
verified/synthetic-verified facts does not, tests cover both, and replay still renders.

### Phase 2: Better data (M-L, needs provider keys)
**Why:** W2, W3. This improves plan quality more than any other change.

**Do**
- **Routes:** add a `RoutesProvider` using a real routing service (OSRM, openrouteservice or
  Google Routes; pick by terms, quota, mode support). Keep straight-line as a clearly labelled
  fallback. Report provider, mode and uncertainty in each leg.
- **Places:** add an optional commercial places provider (hours, price level, accessibility) behind
  the same `PlacesProvider` interface; merge with OSM by place id/name+distance; keep per-field
  provenance.
- **Caching:** cache Overpass and routing responses (SQLite, short TTL) to cut latency and be a
  polite client; never cache beyond the provider's terms.
- Verify access with real test calls and record the integration status honestly in docs.

**Done when:** a walking plan in a real city uses real route times, the leg evidence says so, and
turning the keys off falls back visibly to estimates. Provider terms are noted in `docs/`.

**Risks:** cost and terms (check before relying), mixed-source duplicates.

### Phase 3: Persistence, history and sharing (M)
**Why:** W4.

**Do**
- Persist trips, plan versions (immutable), the accepted pointer, proposals and diffs in SQLite
  (reuse the group store pattern); restore sessions after a restart.
- History screen: list accepted versions; "restore" creates a **new** validated version rather than
  rewriting history.
- Shareable read-only plan link (revocable token) and export: per-leg Google Maps directions links
  and a `.ics` calendar file. A saved plan is a suggestion, not a booking; keep that wording.
- Data retention: organizer can delete a group and its data.

**Done when:** restarting the server keeps accepted plans and history, a stale restore is rejected,
and a share link shows the plan without exposing private fields.

### Phase 4: Agent robustness and speed (M)
**Why:** W5, W7.

**Do**
- Split `skills/plan-and-validate.md` into focused skills (interpret constraints; research and
  sources; assemble and validate; repair; conflict and clarify; replan; group rules). The runner
  loads a list; a test asserts every skill is in the system prompt.
- Cut model calls: allow batching in tools where safe, reuse research across replans, short
  tool-output summaries.
- Stream progress with server-sent events (keep polling as fallback).
- Fallback model on daily-quota or overload errors, with a clear event in the feed.
- Evaluation: a small held-out scenario set (short outing, tight budget, closing time, locked stop,
  late arrival, group with a left-out person) and an opt-in live script that reports hard-constraint
  violations, unknown rate and cost. Code checks arithmetic; humans judge explanation quality.

**Done when:** typical solo runs use fewer calls than today (measure before/after), skills are
modular, and the eval script produces a table.

### Phase 5: Group v2 (L, split across sessions)
**Why:** W6.

**Do (in this order)**
1. **Answerable planner questions in group mode** (reuse the paused-session machinery): the
   organizer answers, the run resumes.
2. **Explanation leak guard:** after the model writes an explanation, check it against attendees'
   private values (amounts, dietary and access terms, name+budget phrasing); if it fails, replace it
   with a templated explanation and log the event. Add tests.
3. **Group map** in the group view with the shared meeting point and stops.
4. **Per-person start locations (optional).** Define the meeting rule first (everyone meets at the
   first stop). Each person gets a "can reach the first stop in time" check using their own route
   estimate; the validator reports it per person. Keep the schedule shared.
5. **Live updates** over SSE instead of 3-second polling.
6. **Split activities (stretch, own design review):** parallel blocks with independent legs and a
   validated reunion. Do not claim it until the validator proves it.

**Done when:** each item has a test that a privacy or authorization rule still holds; no other
member's budget, access or dietary values appear in any payload or in the model input.

### Phase 6: Longer trips (L, stretch)
Hierarchical planning (destinations, lodging bases, daily plans), whole-trip budgets with explicit
included categories, forecast-horizon behaviour, lodging and intercity providers, and per-day
replanning. Only start after P0-P4 are solid; it needs new providers and a larger validator.

### Phase 7: Production readiness and polish (M)
- **Security:** per-IP rate limits, request size limits, CORS policy, HTTPS, secure token handling
  (member tokens are in `localStorage`: keep the strict no-`innerHTML`-with-data rule and add a
  content security policy), dependency audit, structured logs without personal data.
- **Deploy:** pick a host, set the key as a server secret, run the validator and smoke tests before
  going live. Never enable anything public without the owner's explicit go-ahead.
- **Quality:** a handful of Playwright smoke tests (solo plan, answer a question, replan, group
  join), accessibility pass (focus order, contrast, labels), mobile QA of the group view.
- **Docs:** refresh `docs/CHECKLIST.md`, README, demo script and the hackathon notes.

## 4. Suggested order and size

| Order | Phase | Size | Needs from the user |
|---|---|---|---|
| 1 | P0 live verification | S | Billing on the Gemini key |
| 2 | P1 honest confidence labels | S-M | none |
| 3 | P3 persistence and history | M | none |
| 4 | P2 better data | M-L | Routing/places provider choice and keys |
| 5 | P4 agent robustness and speed | M | none |
| 6 | P5 group v2 | L | none |
| 7 | P7 production readiness | M | Hosting choice, go-ahead to deploy |
| 8 | P6 longer trips | L | decision to pursue |

If time is short, do P0, P1 and the first two items of P5 (planner questions, leak guard); they have
the best effect on trust.

## 5. Working rules for whoever executes this
- One phase per session where possible; stop and report after each phase.
- Commit after each step; do not push unless asked. Never commit `.env` or `data/`.
- Run the full test suite once at the end of each phase; add only the tests the phase calls for.
- Use demo data and the scripted model for development to save model quota; use the live model only
  for the verification steps.
- End every phase with: what changed, what was verified (and how), what is unfinished.
