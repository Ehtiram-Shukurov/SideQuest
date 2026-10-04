# Skill: confidence and the explanation

**Inputs:** the `validation` result: `overall`, `confidence`, `issues`, `verify_before_going`.

**Tools:** `save_proposal` (the explanation field).

**Rules**
- `confidence` is `verified`, `community_data` or `mixed`. Community map data is not verified.
- If confidence is `community_data` or `mixed`, say so in the explanation and name the stops whose
  hours or prices the user should confirm. Never describe community data as verified.
- If `overall` is `provisional`, state what is unknown (for example "price unknown at X").
- Keep the explanation to two or three sentences and cite only what the tools returned: stop names,
  times, costs and why each stop fits the request.

**Uncertainty rules:** do not state anything about hours, prices or accessibility that a tool did not
return.
