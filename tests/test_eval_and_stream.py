"""Evaluation plumbing (scripted model) and the live-progress stream."""
from __future__ import annotations

from fastapi.testclient import TestClient

from server.agent.model import ModelTurn
from server.agent.scripted import ScriptedProvider
from server.api.app import create_app
from server.eval.run import judge, run_scenario, table
from server.eval.scenarios import SCENARIOS, build_ctx

from .test_agent_loop import tc
from .test_api import BODY, NOW, script


def by_id(i):
    return next(s for s in SCENARIOS if s.id == i)


def test_eval_counts_a_clean_plan_and_a_correct_question_with_zero_violations():
    ok = run_scenario(ScriptedProvider([tc("search_places", category="outdoor"),
                                        tc("assemble_plan", place_ids=["river-overlook"], mode="walk"),
                                        tc("save_proposal", plan_id="plan-1", explanation="A short walk.")]),
                      by_id("short_outing"))
    assert ok["status"] == "proposal_saved" and ok["ok"] and ok["tool_calls"] == 3
    assert ok["violations"] == 0 and ok["state"] == "ready" and ok["confidence"] == "verified"

    ask = run_scenario(ScriptedProvider([tc("search_places", category="culture"),
                                         tc("assemble_plan", place_ids=["art-museum"], mode="walk"),
                                         tc("ask_user", question="That does not fit 45 minutes. Extend?")]),
                       by_id("too_short"))
    assert ask["status"] == "needs_clarification" and ask["ok"] and ask["violations"] == 0

    wrong = run_scenario(ScriptedProvider([ModelTurn(text="I give up.")]), by_id("short_outing"))
    assert wrong["status"] == "no_proposal" and not wrong["ok"]  # a non-answer is reported as a miss
    assert "expected outcome met: 2/3" in table([ok, ask, wrong])
    assert judge(by_id("tight_budget"), "needs_clarification") and not judge(by_id("short_outing"), "needs_clarification")


def test_every_scenario_builds_a_valid_world_and_the_unknown_price_one_stays_provisional():
    for s in SCENARIOS:
        ctx = build_ctx(s)
        assert ctx.trip.window_end > ctx.trip.window_start and ctx.places.synthetic
    r = run_scenario(ScriptedProvider([tc("search_places", category="food"),
                                       tc("assemble_plan", place_ids=["night-market"], mode="walk"),
                                       tc("save_proposal", plan_id="plan-1", explanation="Dinner at the market.")]),
                     by_id("evening_unknown_price"))
    assert r["state"] == "provisional" and r["unknown_checks"] >= 1 and r["violations"] == 0


def test_run_stream_sends_progress_then_done_and_needs_the_owner_token():
    c = TestClient(create_app(lambda: ScriptedProvider(script()), lambda: NOW))
    made = c.post("/api/plans", json=BODY).json()
    rid = made["run_id"]
    assert c.get(f"/api/runs/{rid}/stream").status_code == 401  # no owner token
    with c.stream("GET", f"/api/runs/{rid}/stream", headers={"Authorization": "Bearer " + made["owner_token"]}) as r:
        text = "".join(r.iter_text())
    assert r.headers["content-type"].startswith("text/event-stream")
    assert text.count("event: progress") >= 6 and text.rstrip().endswith('"error": null}')
    assert text.index("event: progress") < text.index("event: done")
