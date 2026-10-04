# Skill: repair a failing plan, or explain a conflict

**Inputs:** the `validation.issues` from `assemble_plan` / `validate_plan`, or a `conflict` with
`relaxations` when no schedule fits.

**Tools:** `search_places`, `assemble_plan`, `ask_user`.

**Procedure**
1. If `validation.overall` is `failed`, read the failed issue codes and change ONE thing: drop an
   optional stop, swap a stop for another candidate from tool results, or choose a shorter or cheaper
   option. Call `assemble_plan` again with the new ids; its `validation` is the new verdict.
2. Never try to save a failed plan: `save_proposal` refuses it.
3. If `assemble_plan` returns `feasible: false`, do not keep searching. Call `ask_user` with the
   conflict in plain words and concrete options taken from its `relaxations` (extend the time
   window, skip a named stop).
4. After two failed repairs, stop repairing and ask the user.

**Uncertainty rules:** never relax a constraint yourself; only the user can.

**Stop condition:** a saved proposal, or `ask_user`.
