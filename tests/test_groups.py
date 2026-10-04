"""Group planning: availability overlap, privacy rules, auth, questions, stale proposals.
Scripted model + demo (synthetic) data: no keys, no network."""
from __future__ import annotations

import itertools
import json
import re
import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from server.agent.model import ModelTurn, ToolCall
from server.agent.runner import trip_snapshot
from server.agent.scripted import ScriptedProvider
from server.api.app import create_app
from server.models import TimeWindow
from server.planning.assemble import assemble_plan
from server.planning.overlap import best_window, everyone_overlap
from server.providers.synthetic import SyntheticWorld
from server.tools.context import ToolContext
from server.tools.planning_tools import build_registry

from .helpers import matrix, member, money, place, trip

NOW = datetime(2026, 6, 6, 16, 0, tzinfo=UTC)  # 11:00 in Chicago
_n = itertools.count(1)


def tc(name, **args):
    return ModelTurn(tool_calls=(ToolCall(f"c{next(_n)}", name, args),))


def W(h1, h2, day=6):  # UTC hours -> TimeWindow
    return TimeWindow(start=datetime(2026, 6, day, h1, tzinfo=UTC) if h1 < 24 else datetime(2026, 6, day + 1, h1 - 24, tzinfo=UTC),
                      end=datetime(2026, 6, day, h2, tzinfo=UTC) if h2 < 24 else datetime(2026, 6, day + 1, h2 - 24, tzinfo=UTC))


# --- availability overlap ----------------------------------------------------------------

def test_overlap_picks_the_window_that_fits_the_most_people_and_names_who_is_left_out():
    wins = {"a": [W(16, 20)], "b": [W(17, 21)], "c": [W(23, 25)]}
    bw = best_window(wins)
    assert (bw.window.start.hour, bw.window.end.hour) == (17, 20)  # a and b overlap for 3 hours
    assert bw.attendees == {"a", "b"} and bw.excluded == {"c"}
    assert not everyone_overlap(wins)
    assert everyone_overlap({"a": [W(16, 20)], "b": [W(17, 21)]})


def test_overlap_ignores_slivers_merges_segments_and_caps_length():
    assert best_window({"a": [W(10, 11)], "b": [W(10, 11)]}, min_minutes=90) is None  # too short to plan
    assert best_window({}) is None
    long = best_window({"a": [W(0, 23)], "b": [W(0, 23)]})
    assert long.window.end - long.window.start == timedelta(hours=8)  # capped at 8 hours
    # same people free across a boundary of someone else's window are merged into one segment
    merged = best_window({"a": [W(10, 14)], "b": [W(12, 13)], "c": [W(10, 14)]}, min_minutes=30)
    assert merged.attendees == {"a", "b", "c"} and merged.window.start.hour == 12


# --- privacy: what the model and the tool output may contain ---------------------------------

def test_group_snapshot_never_carries_per_person_budgets_or_access_needs():
    t = trip([member("m1", cap=12345, requires_step_free=True, dietary=("vegan",), interests=("coffee",)),
              member("m2", cap=67890, interests=("hiking",))], start=NOW, end=NOW + timedelta(hours=3))
    for m in t.members:  # the helper's availability window is irrelevant here
        assert m.budget_cap_minor
    snap = json.dumps(trip_snapshot(t, group=True))
    for secret in ("12345", "67890", "requires_step_free", "budget_cap_minor"):
        assert secret not in snap
    assert "coffee" in snap and "hiking" in snap  # public interests do reach the model
    notes = json.loads(snap)["group_notes"]
    assert notes["budget_pressure"] == "relaxed ($60+)"  # the tightest cap ($123) as a band, not a number
    assert notes["someone_needs_step_free_access"] is True and notes["dietary_needs"] == ["vegan"]
    assert "12345" in json.dumps(trip_snapshot(t, group=False))  # solo snapshot is unchanged


def test_validate_tool_redacts_budget_details_in_group_mode_only():
    t = trip([member("m1", cap=100)])
    p = place("a", price=money(5000))
    plan = assemble_plan(t, [p], matrix({("home", "a"): 5})).plan
    outputs = {}
    for private in (False, True):
        w = SyntheticWorld(t, now=NOW)
        ctx = ToolContext(trip=t, places=w, routes=w, weather=w, now=NOW, private_mode=private)
        ctx.details["a"], ctx.plans["plan-1"] = p, plan
        outputs[private] = build_registry(ctx).call("validate_plan", {"plan_id": "plan-1"}).data
    open_issue = next(i for i in outputs[False]["issues"] if i["code"] == "BUDGET_PER_PERSON")
    assert "5000" in json.dumps(open_issue) and outputs[False]["per_person_cost_minor"]["m1"]["cap"] == 100
    shut = next(i for i in outputs[True]["issues"] if i["code"] == "BUDGET_PER_PERSON")
    assert shut["participants"] == [] and shut["data"] == {} and "5000" not in json.dumps(outputs[True])
    assert outputs[True]["per_person_cost_minor"] == {"redacted": True}
    assert outputs[True]["overall"] == "failed"  # code still enforces it, the model just cannot see the numbers


# --- API: auth, roles, privacy of what viewers see -------------------------------------------

H = lambda tok: {"Authorization": f"Bearer {tok}"}  # noqa: E731


def iso(h, m=0):  # Chicago local time on 2026-06-06 -> ISO with offset
    return f"2026-06-06T{h:02d}:{m:02d}:00-05:00"


def inputs(h1, h2, **kw):
    return {"windows": [{"start": iso(h1), "end": iso(h2)}], "transport": ["walk"], **kw}


def new_group(c, name="Ann"):
    return c.post("/api/groups", json={"title": "Saturday out", "request": "something scenic outdoors", "lat": 44.97,
                                       "lon": -93.26, "tz": "America/Chicago", "data": "demo", "display_name": name}).json()


def join(c, g, name):
    return c.post(f"/api/invites/{g['invite_token']}/join", json={"name": name}).json()


def test_group_access_roles_and_private_fields():
    c = TestClient(create_app(lambda: ScriptedProvider([]), lambda: NOW))
    g = new_group(c)
    ann, gid = g["member_token"], g["group_id"]
    assert c.get(f"/api/invites/{g['invite_token']}").json()["title"] == "Saturday out"
    bob = join(c, g, "Bob")
    assert c.get(f"/api/groups/{gid}").status_code == 401  # no token
    assert c.get(f"/api/groups/{gid}", headers=H("not-a-token")).status_code == 401
    assert c.put(f"/api/groups/{gid}/me/inputs", headers=H(ann["member_token"] if isinstance(ann, dict) else ann),
                 json=inputs(11, 15, budget_dollars=50, dietary=["nut allergy"])).status_code == 200
    assert c.put(f"/api/groups/{gid}/me/inputs", headers=H(bob["member_token"]),
                 json=inputs(12, 16, budget_dollars=37.5, step_free=True, interests=["views"])).status_code == 200

    ann_view = c.get(f"/api/groups/{gid}", headers=H(ann)).json()
    blob = json.dumps(ann_view)
    assert "37.5" not in blob and "3750" not in blob and "step_free" not in json.dumps(ann_view["members"])
    bob_entry = next(m for m in ann_view["members"] if m["name"] == "Bob")
    assert set(bob_entry) == {"id", "name", "role", "is_me", "submitted", "windows", "transport", "interests", "budget_status"}
    bob_view = c.get(f"/api/groups/{gid}", headers=H(bob["member_token"])).json()
    assert bob_view["me"]["inputs"]["budget_dollars"] == 37.5 and bob_view["me"]["inputs"]["step_free"] is True  # own data
    assert "nut allergy" not in json.dumps(bob_view) and bob_view["group"]["invite_token"] is None  # organizer-only

    for call in (lambda: c.post(f"/api/groups/{gid}/plan", headers=H(bob["member_token"])),
                 lambda: c.delete(f"/api/groups/{gid}/members/{bob['member_id']}", headers=H(bob["member_token"])),
                 lambda: c.post(f"/api/groups/{gid}/invite", headers=H(bob["member_token"]), json={"enabled": False}),
                 lambda: c.post(f"/api/groups/{gid}/decision", headers=H(bob["member_token"]), json={"decision": "accept"})):
        assert call().status_code == 403  # roles come from the server, not the client

    assert c.post(f"/api/groups/{gid}/invite", headers=H(ann), json={"enabled": False}).json()["invite_token"] is None
    assert c.get(f"/api/invites/{g['invite_token']}").status_code == 404  # revoked
    assert c.delete(f"/api/groups/{gid}/members/{bob['member_id']}", headers=H(ann)).status_code == 200
    assert c.get(f"/api/groups/{gid}", headers=H(bob["member_token"])).status_code == 401  # access ends immediately


# --- API: planning, the question to the person left out, stale proposals -----------------------

def overlook_script():
    return [tc("search_places", category="outdoor"), tc("get_place_details", place_ids=["river-overlook"]),
            tc("estimate_routes", place_ids=["river-overlook"], mode="walk"),
            tc("assemble_plan", place_ids=["river-overlook"], mode="walk"), tc("validate_plan", plan_id="plan-1"),
            tc("save_proposal", plan_id="plan-1", explanation="A short outdoor stop that suits the group.")]


def wait_run(c, gid, token, until=None):
    """Wait for a finished run. Run ids come from the (fixed) test clock, so a replan cannot be told apart
    from the first run by id; `until` says what the new result must look like."""
    for _ in range(300):
        v = c.get(f"/api/groups/{gid}", headers=H(token)).json()
        if v["run"] and v["run"]["done"] and (until is None or until(v)):
            return v
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_plan_for_everyone_asks_the_person_left_out_and_blocks_stale_acceptance():
    scripts = [overlook_script(), overlook_script(), overlook_script()]
    c = TestClient(create_app(lambda: ScriptedProvider(scripts.pop(0)), lambda: NOW))
    g = new_group(c)
    gid, ann = g["group_id"], g["member_token"]
    bob, cara = join(c, g, "Bob"), join(c, g, "Cara")
    c.put(f"/api/groups/{gid}/me/inputs", headers=H(ann), json=inputs(11, 15, budget_dollars=50))
    c.put(f"/api/groups/{gid}/me/inputs", headers=H(bob["member_token"]), json=inputs(12, 16, budget_dollars=37.5, step_free=True))
    c.put(f"/api/groups/{gid}/me/inputs", headers=H(cara["member_token"]), json=inputs(18, 20, budget_dollars=20))

    assert c.post(f"/api/groups/{gid}/plan", headers=H(ann)).status_code == 200
    v = wait_run(c, gid, ann)
    cand = v["candidate"]
    assert v["run"]["status"] == "proposal_saved" and cand["window"] == "Sat 12:00 PM-3:00 PM"
    assert [e["name"] for e in cand["excluded"]] == ["Cara"] and cand["excluded"][0]["question"] == "pending"
    assert {p["name"] for p in cand["people"]} == {"Ann", "Bob"} and cand["stale"] is False

    # per-person cost: you see your own amounts; everyone else sees only a status
    bob_plan = c.get(f"/api/groups/{gid}", headers=H(bob["member_token"])).json()["candidate"]
    rows = {p["name"]: p for p in bob_plan["people"]}
    assert rows["Bob"]["my_cost"]["cap"] == 37.5 and "my_cost" not in rows["Ann"]
    assert rows["Ann"]["budget_status"] == "within budget" and not re.search(r"\b50(\.0)?\b", json.dumps(rows["Ann"]))  # random ids may contain 50; a standalone 50 is her budget

    # Cara is asked, and only Cara (and the organizer) can see the question
    cara_view = c.get(f"/api/groups/{gid}", headers=H(cara["member_token"])).json()
    q = next(x for x in cara_view["questions"] if x["mine"])
    assert q["text"] == "Could you make Sat 12:00 PM-3:00 PM?" and q["status"] == "pending"
    assert c.get(f"/api/groups/{gid}", headers=H(bob["member_token"])).json()["questions"] == []  # not Bob's question
    assert c.post(f"/api/groups/{gid}/questions/{q['id']}/answer", headers=H(bob["member_token"]), json={"answer": "yes"}).status_code == 403

    # an input change after the plan makes it stale: it cannot be accepted
    c.put(f"/api/groups/{gid}/me/inputs", headers=H(bob["member_token"]), json=inputs(12, 16, budget_dollars=40, step_free=True))
    assert c.get(f"/api/groups/{gid}", headers=H(ann)).json()["candidate"]["stale"] is True
    assert c.post(f"/api/groups/{gid}/decision", headers=H(ann), json={"decision": "accept"}).status_code == 409

    # Cara suggests another time -> availability updates and a new proposal is made for the organizer
    alt = c.post(f"/api/groups/{gid}/questions/{q['id']}/answer", headers=H(cara["member_token"]),
                 json={"answer": "alt", "alt_start": iso(12, 30), "alt_end": iso(14, 30)})
    assert alt.status_code == 200 and alt.json()["replanning"] is True
    v2 = wait_run(c, gid, ann, until=lambda x: x["candidate"] and x["candidate"]["excluded"] == [])
    assert {p["name"] for p in v2["candidate"]["people"]} == {"Ann", "Bob", "Cara"} and v2["candidate"]["excluded"] == []
    assert v2["candidate"]["stale"] is False
    assert c.post(f"/api/groups/{gid}/decision", headers=H(ann), json={"decision": "accept"}).status_code == 200
    assert c.get(f"/api/groups/{gid}", headers=H(cara["member_token"])).json()["accepted"]["window"] == "Sat 12:30 PM-2:30 PM"
