# Skill: answers and replanning

**Inputs:** either a tool result for `ask_user` that holds the user's answer, or a first message with
a `replan` section (changes, current stops, locked stop ids, excluded place ids).

**Tools:** `search_places`, `assemble_plan`, `save_proposal`, `ask_user`.

**After an answer:** the result holds `answer` and `applied`, a note on what changed (a longer time
window, a place the user skipped). Constraints may have changed, so call `assemble_plan` again; do
not reuse an old plan id.

**On a replan:** the user is changing a saved plan.
- Locked stops are fixed; `assemble_plan` keeps them automatically. Do not move or drop them.
- Never use ids in `excluded_place_ids`; the tools reject them.
- Keep the other current stops unless the change forces otherwise, reuse earlier research, and
  change as little as possible. If a stop was swapped out, find a different place for that slot.

**Stop condition:** a saved proposal (the user will review a diff) or `ask_user`.
