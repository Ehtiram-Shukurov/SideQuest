# Skill: interpret constraints

**Inputs:** the trip snapshot, especially `known_gaps` and each member's budget, transport,
availability and rain limit.

**Tools:** `ask_user` only (before any research).

**Procedure**
1. Read the request for categories of stops, pace, budget limits and walking limits.
2. If `known_gaps` lists something that changes feasibility (budget cap, transport, availability),
   call `ask_user` with one specific question and concrete options before researching.
3. Otherwise do not ask. Pick sensible categories from the request and continue.

**Uncertainty rules:** never assume a missing budget, transport mode or availability. A request
that is vague about categories is not a gap; choose reasonable ones.

**Stop condition:** after `ask_user`, the run pauses until the user answers.
