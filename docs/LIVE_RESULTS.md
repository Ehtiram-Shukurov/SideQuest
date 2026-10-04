# Live results

What happened when the real server was driven over HTTP with a real model and live data. Nothing here
comes from the scripted model or the synthetic world.

**Setup:** Gemini free tier, default `gemini-3.5-flash-lite` (fallbacks enabled); places from
OpenStreetMap (Overpass); routes from FOSSGIS OSRM; weather from Open-Meteo. Start point near the
University of Minnesota, Minneapolis. The runs were in the evening (about 20:00 local), so a window of
240 minutes ended near midnight. That matters: many cafes were closed or had no hours in the data.

**Scope of this evidence:** one person, one location, one evening, one model. These are single runs, not
a benchmark. Latency includes the free-tier model's response time, which varies. Only
`gemini-3.5-flash-lite` was measured; the other models in the fallback list were not compared live.
`gemini-3.8-flash` was observed to return a daily quota error (20 requests/day on the free tier), which
is why it is not the default.

## Flows

| Flow | Result | Time | Tool calls | Tokens |
|---|---|---|---|---|
| (a) Coffee, short walk outside, lunch under $20, 45 min each, walks under 15 min, back to start | Saved provisional proposal, 3 stops | 11.1 s | 6 | 10,729 |
| (b) "A proper museum visit and a long walk by the river" in a 60 minute window | Code found the window too short (`WINDOW_TOO_SHORT`, needs 198 min); the model asked a structured question | 9.1 s | 5 | 10,424 |
| (b) answered with the "extend the window" choice | Saved provisional proposal, 2 stops | 5.0 s | 2 | 5,134 |
| (c) Replan: swap the first stop of (a)'s earlier plan | Saved replan proposal, diff 1 added / 1 removed / 1 moved; accepted; history shows v2 accepted and v1 superseded | 5.0 s | 3 | 8,117 |

Not run live: group planning (out of scope for this pass), share links and ICS export (covered by automated
tests with the scripted model only).

## What the runs showed about honesty

- Every proposal was **provisional**, never "checked". Opening hours and prices were unknown for most OSM
  venues, and the validator reported them as `unknown` instead of passing them.
- The confidence label for flow (a) is `unverified` ("Facts unknown"). An earlier run of the same flow
  produced `community_data` when a place did have OSM opening hours.
- Route notes say the times come from OpenStreetMap routing without live traffic, with attribution.
- The model's explanations name the unknown facts. One replan explanation called a viewpoint a "lunch
  spot"; that wording is the model's, and the validator and stop data were unaffected. This is a model
  wording weakness, not caught by code.

## Bugs found only by running a real model, and fixed

1. **Search loop.** The model searched for everyday words ("coffee", "park") that match no OSM name or
   tag, got zero results, and repeated until the turn limit. Words now map to categories, and an empty
   search returns `available_categories` plus a hint. (`tests/test_search_words.py`)
2. **Categories crowded out.** All Overpass categories shared one 60 result cap, so a dense block of
   cafes returned **zero** parks near the university. Each category now has its own cap (live check
   after the fix: food 40, outdoor 40, scenic 1, culture 15). (`tests/test_live_provider.py`)
3. **Vacuous "verified".** A plan whose only external facts were unknown had no passing check with a
   source, so the confidence label read "verified". Unknown facts now count, and an all-unknown plan is
   labelled `unverified`. (`tests/test_confidence.py`)
4. **Unexplained infeasibility.** `assemble_plan` returned `NO_FEASIBLE_ORDER` with no reason, so the
   model retried blindly. It now also returns `blockers` naming the stop that does not fit.
   (`tests/test_search_words.py`)

## Known limits seen live

- Flow (a) still hit `NO_FEASIBLE_ORDER` once before the model changed its selection. `blockers` helps,
  but it relies on the model reading it.
- Stop order is chosen by code (shortest travel), so a request that lists "coffee, walk, lunch" can come
  back as coffee, lunch, walk.
- OSM data gives no prices, and few opening hours. Plans will usually be provisional until a source with
  real hours is added.
- A single free-tier key is shared by every request; heavy use will hit the daily quota.
