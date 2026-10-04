# Skill: research, assemble and validate (the fast path)

**Inputs:** the request and the trip snapshot.

**Tools:** `search_places`, `assemble_plan`, `save_proposal` (and `get_weather`,
`estimate_routes`, `get_place_details`, `validate_plan` only if you need them).

**Procedure (aim for three tool calls)**
1. `search_places` once per category you need, using the `category` argument: `food` (cafes,
   restaurants), `outdoor` (parks, walks), `scenic` (viewpoints), `culture` (museums, galleries). Hours,
   prices and access facts come back inline. Pick stops from those results. If a search returns
   nothing, read `available_categories` and use one of those; never retry with a different free-text
   query.
2. `assemble_plan` with the chosen ids and one travel mode. Code estimates any missing routes,
   fetches the forecast if a rain limit applies, orders the stops, and validates the draft. Read the
   `validation` field in the result.
3. If `validation.overall` is `checked` or `provisional`, call `save_proposal` with a short
   explanation. Do not call `validate_plan` again unless you changed something.

**Output:** a saved proposal whose explanation cites only what tools returned.

**Uncertainty rules:** a `provisional` result means some facts are unknown; say which. Do not use car
estimates for walking; pick the mode the traveller has.

**Stop condition:** `save_proposal` succeeds. If `assemble_plan` says `feasible: false`, go to the
repair skill; do not keep searching. `blockers` names the stop that does not fit (for example one that is
not open long enough); swap or drop that stop rather than retrying the same set.
