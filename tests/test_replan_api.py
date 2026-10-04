"""Small API tests for the answerable-clarification flow and replan. Scripted model + demo
(synthetic) data: no keys, no network."""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from server.agent.model import ModelTurn, ToolCall
from server.agent.scripted import ScriptedProvider
from server.api.app import PlanRequest, build_trip, create_app
from server.planning.changes import extend_window, running_late

NOW = datetime(2026, 6, 6, 16, 0, tzinfo=UTC)  # 11:00 in Chicago
_n = iter(range(1, 10_000))


def tc(name, **args):
    return ModelTurn(tool_calls=(ToolCall(f"c{next(_n)}", name, args),))


def wait(client, rid):
    for _ in range(200):
        j = client.get(f"/api/runs/{rid}").json()
        if j["done"]:
            return j
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def authed(c, made):
    c.headers["Authorization"] = "Bearer " + made["owner_token"]
    return made


BODY = {"request": "a museum", "lat": 44.97, "lon": -93.26, "tz": "America/Chicago", "minutes": 30,
        "budget_dollars": 40, "mode": "walk", "data": "demo"}


def test_answering_a_clarification_applies_a_structured_change_and_resumes_the_run():
    script = [tc("search_places", category="culture"), tc("get_place_details", place_ids=["art-museum"]),
              tc("estimate_routes", place_ids=["art-museum"], mode="walk"),
              tc("assemble_plan", place_ids=["art-museum"], mode="walk"),  # 30 min cannot fit a museum
              tc("ask_user", question="That does not fit 30 minutes. What now?", options=["Pick something shorter"]),
              tc("assemble_plan", place_ids=["art-museum"], mode="walk"),  # after the answer
              tc("validate_plan", plan_id="plan-1"),
              tc("save_proposal", plan_id="plan-1", explanation="Museum with a longer window.")]
    c = TestClient(create_app(lambda: ScriptedProvider(script), lambda: NOW))
    r = authed(c, c.post("/api/plans", json=BODY).json())
    first = wait(c, r["run_id"])
    q = first["result"]["clarification"]
    assert first["result"]["status"] == "needs_clarification" and first["result"]["constraints"]["minutes"] == 30
    assert q["choices"][0]["kind"] == "extend_window" and "Extend" in q["choices"][0]["label"]
    assert any(ch["kind"] == "text" for ch in q["choices"])  # the model's own option stays selectable

    a = c.post(f"/api/runs/{r['run_id']}/answer", json={"choice": 0})
    assert a.status_code == 200 and a.json()["trip_id"] == r["trip_id"]
    second = wait(c, a.json()["run_id"])
    res = second["result"]
    assert res["status"] == "proposal_saved" and res["proposal"]["blocks"][0]["name"].startswith("Art Museum")
    assert res["constraints"]["minutes"] > 30  # the window really changed, not just chat text
    assert c.post(f"/api/runs/{r['run_id']}/answer", json={"choice": 0}).status_code == 409  # question is closed


def test_replan_swaps_a_stop_shows_a_diff_and_waits_for_accept():
    plan1 = [tc("search_places", category="outdoor"), tc("get_place_details", place_ids=["lakeside-trail"]),
             tc("estimate_routes", place_ids=["lakeside-trail"], mode="walk"),
             tc("assemble_plan", place_ids=["lakeside-trail"], mode="walk"), tc("validate_plan", plan_id="plan-1"),
             tc("save_proposal", plan_id="plan-1", explanation="A trail walk.")]
    plan2 = [tc("search_places", category="outdoor"), tc("get_place_details", place_ids=["river-overlook"]),
             tc("estimate_routes", place_ids=["river-overlook"], mode="walk"),
             tc("assemble_plan", place_ids=["river-overlook"], mode="walk"), tc("validate_plan", plan_id="plan-2"),
             tc("save_proposal", plan_id="plan-2", explanation="An overlook instead.")]
    scripts = [plan1, plan2]
    c = TestClient(create_app(lambda: ScriptedProvider(scripts.pop(0)), lambda: NOW))
    r = authed(c, c.post("/api/plans", json={**BODY, "minutes": 180}).json())
    saved = wait(c, r["run_id"])["result"]
    assert saved["proposal"]["overall"] == "checked"
    tid = r["trip_id"]

    lock = c.post(f"/api/trips/{tid}/replan", json={"changes": [{"type": "lock", "block_id": "b-lakeside-trail"}]}).json()
    assert lock["immediate"] and lock["result"]["proposal"]["blocks"][0]["locked"] is True  # no agent run

    sw = c.post(f"/api/trips/{tid}/replan", json={"changes": [{"type": "swap_stop", "block_id": "b-lakeside-trail"}]})
    res = wait(c, sw.json()["run_id"])["result"]
    assert res["awaiting_decision"] and res["diff"]["added"][0]["name"].startswith("River Overlook")
    assert res["diff"]["removed"][0]["name"].startswith("Lakeside Trail")
    # the old plan stays current until the user decides
    assert c.get(f"/api/trips/{tid}").json()["result"]["proposal"]["blocks"][0]["name"].startswith("Lakeside")
    assert c.post(f"/api/trips/{tid}/replan", json={"changes": [{"type": "set_minutes", "minutes": 60}]}).status_code == 409

    done = c.post(f"/api/trips/{tid}/decision", json={"decision": "accept"}).json()
    assert done["result"]["proposal"]["blocks"][0]["name"].startswith("River Overlook")


def test_rain_preference_reaches_the_trip_and_change_helpers_behave():
    req = PlanRequest(**{**BODY, "avoid_rain_pct": 30})
    trip = build_trip(req, NOW)
    assert trip.members[0].avoid_rain_above == 0.3  # so the validator's WEATHER check can fire
    longer = extend_window(trip, 45)
    assert longer.window_end - trip.window_end == timedelta(minutes=45)
    assert longer.constraints_version == trip.constraints_version + 1  # old plans become stale
    late = running_late(extend_window(trip, 90), NOW, 20)
    assert late.window_start == NOW + timedelta(minutes=20) and late.window_end == longer.window_end + timedelta(minutes=45)
    try:
        running_late(trip, NOW, 29)  # leaves under the 30-minute minimum
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
