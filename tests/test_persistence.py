"""P3: plans survive a restart, history and restore, share links, exports, delete.
A 'restart' is a second create_app() on the same SQLite file (fresh memory). Scripted model, demo data."""
from __future__ import annotations

import json
from datetime import timedelta

from fastapi.testclient import TestClient

from server.agent.scripted import ScriptedProvider
from server.api.app import create_app
from server.groups.store import Store
from server.models import Trip
from server.planning.changes import with_window
from server.planning.export import directions_links, ics_for_plan

from .helpers import free, place
from .test_groups import H, NOW, inputs, join, new_group, overlook_script, tc, wait_run
from .test_replan_api import BODY, wait

ORIGIN = "44.970000,-93.260000"  # the owner's start (BODY lat/lon)


def trail_script():
    return [tc("search_places", category="outdoor"), tc("get_place_details", place_ids=["lakeside-trail"]),
            tc("estimate_routes", place_ids=["lakeside-trail"], mode="walk"),
            tc("assemble_plan", place_ids=["lakeside-trail"], mode="walk"), tc("validate_plan", plan_id="plan-1"),
            tc("save_proposal", plan_id="plan-1", explanation="A trail walk.")]


def solo_app(db, scripts):
    return TestClient(create_app(lambda: ScriptedProvider(scripts.pop(0)), lambda: NOW, db_path=str(db)))


def test_solo_plan_history_and_locks_survive_a_restart_and_restore_makes_a_new_version(tmp_path):
    db = tmp_path / "sq.db"
    c1 = solo_app(db, [trail_script()])
    made = c1.post("/api/plans", json={**BODY, "minutes": 180}).json()
    tid, tok = made["trip_id"], made["owner_token"]
    c1.headers["Authorization"] = "Bearer " + tok
    assert wait(c1, made["run_id"])["result"]["proposal"]["overall"] == "checked"
    assert c1.post(f"/api/trips/{tid}/replan", json={"changes": [{"type": "lock", "block_id": "b-lakeside-trail"}]}).status_code == 200

    # ---- restart: new app, empty memory, same database ----
    swap = [tc("search_places", category="outdoor"), tc("get_place_details", place_ids=["river-overlook"]),
            tc("estimate_routes", place_ids=["river-overlook"], mode="walk"),
            tc("assemble_plan", place_ids=["river-overlook"], mode="walk"), tc("validate_plan", plan_id="plan-1"),
            tc("save_proposal", plan_id="plan-1", explanation="An overlook instead.")]
    c2 = solo_app(db, [swap])
    assert c2.get(f"/api/trips/{tid}").status_code == 401  # the token is required, also after a restart
    assert c2.get(f"/api/trips/{tid}", headers={"Authorization": "Bearer nope"}).status_code == 401
    c2.headers["Authorization"] = "Bearer " + tok
    back = c2.get(f"/api/trips/{tid}").json()["result"]["proposal"]
    assert back["blocks"][0]["name"].startswith("Lakeside") and back["blocks"][0]["locked"] is True  # lock persisted

    sw = c2.post(f"/api/trips/{tid}/replan", json={"changes": [{"type": "swap_stop", "block_id": "b-lakeside-trail"}]})
    res = wait(c2, sw.json()["run_id"])["result"]
    assert res["awaiting_decision"] and res["diff"]["added"][0]["name"].startswith("River")
    assert c2.post(f"/api/trips/{tid}/decision", json={"decision": "accept"}).status_code == 200
    hist = c2.get(f"/api/trips/{tid}/history").json()["versions"]
    assert [(v["seq"], v["status"], v["kind"]) for v in hist] == [(2, "accepted", "replan"), (1, "superseded", "plan")]
    assert hist[0]["change"] == "1 added, 1 removed"

    back_to_1 = c2.post(f"/api/trips/{tid}/restore", json={"seq": 1})
    assert back_to_1.status_code == 200 and back_to_1.json()["result"]["proposal"]["blocks"][0]["name"].startswith("Lakeside")
    hist = c2.get(f"/api/trips/{tid}/history").json()["versions"]
    assert [(v["seq"], v["kind"], v["restored_from"]) for v in hist][0] == (3, "restore", 1)  # new version, old ones intact
    assert [v["status"] for v in hist] == ["accepted", "superseded", "superseded"]

    # a version that no longer fits today's constraints is refused, never silently restored
    trip = Trip.model_validate_json(Store(str(db)).get_solo(tid)["trip"])
    Store(str(db)).update_solo_trip(tid, with_window(trip, trip.window_start + timedelta(days=3),
                                                     trip.window_end + timedelta(days=3)).model_dump_json())
    c3 = solo_app(db, [])
    c3.headers["Authorization"] = "Bearer " + tok
    refused = c3.post(f"/api/trips/{tid}/restore", json={"seq": 1})
    assert refused.status_code == 409 and refused.json()["issues"]
    assert len(c3.get(f"/api/trips/{tid}/history").json()["versions"]) == 3  # nothing was added


def test_share_link_is_read_only_private_and_revocable_and_exports_are_well_formed(tmp_path):
    c = solo_app(tmp_path / "sq.db", [trail_script()])
    made = c.post("/api/plans", json={**BODY, "minutes": 180}).json()
    tid = made["trip_id"]
    c.headers["Authorization"] = "Bearer " + made["owner_token"]
    j = wait(c, made["run_id"])
    own = j["result"]["proposal"]
    assert any(ORIGIN in d["url"].replace("%2C", ",") for d in own["directions"])  # the owner's own links start at home

    token = c.post(f"/api/trips/{tid}/share", json={"enabled": True}).json()["share_token"]
    anon = TestClient(c.app)  # no Authorization header: a stranger with the link
    pub = anon.get(f"/api/shared/{token}")
    assert pub.status_code == 200
    text = json.dumps({k: v for k, v in pub.json().items() if k != "shared_note"}).lower()  # the fixed disclaimer names what is excluded
    assert set(pub.json()) == {"title", "date", "synthetic", "window", "blocks", "directions", "confidence", "verify",
                               "notes", "shared_note"}
    for private in ("budget", "cap", "totals", "issues", "origin", "explanation", "owner_token", "-93.26,", "44.97,"):
        assert private not in text
    assert all(ORIGIN not in d["url"].replace("%2C", ",") for d in pub.json()["directions"])  # no leg touches home
    assert anon.get(f"/api/trips/{tid}").status_code == 401  # a share link is not an owner token

    ics = anon.get(f"/api/shared/{token}/export.ics")
    assert ics.headers["content-type"].startswith("text/calendar") and ics.text.startswith("BEGIN:VCALENDAR\r\n")
    assert "DTSTART:2026" in ics.text and "not a booking" in ics.text and ics.text.endswith("END:VCALENDAR\r\n")
    assert c.get(f"/api/trips/{tid}/export.ics").status_code == 200

    assert c.post(f"/api/trips/{tid}/share", json={"enabled": False}).json()["share_token"] is None
    assert anon.get(f"/api/shared/{token}").status_code == 404  # revoked
    assert c.delete(f"/api/trips/{tid}").status_code == 200 and c.get(f"/api/trips/{tid}").status_code == 404


def test_ics_escaping_folding_and_directions_rules():
    from datetime import UTC, datetime

    from server.models import ActivityBlock, Plan

    long_name = "Café, bar; grill — " + "very long name " * 8
    p = place("a", price=free()).model_copy(update={"name": long_name})
    start = datetime(2026, 6, 6, 17, tzinfo=UTC)
    blk = ActivityBlock(id="b1", place_id="a", name=long_name, start=start, end=start + timedelta(hours=1),
                        timezone="America/Chicago", attendees=("m1",), costs=(free(),))
    text = ics_for_plan(Plan(id="p", trip_id="t", blocks=(blk,)), {"a": p}, "Trip", start)
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")  # CRLF line endings throughout
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))  # RFC 5545 folding
    unfolded = text.replace("\r\n ", "")
    assert "SUMMARY:Café\\, bar\\; grill" in unfolded and "DTSTART:20260606T170000Z" in unfolded

    blocks = [{"place_id": "x", "name": "X", "lat": 1.0, "lon": 2.0}, {"place_id": "y", "name": "Y", "lat": 1.1, "lon": 2.1}]
    legs = [{"from": "here", "to": "x", "mode": "walk"}, {"from": "x", "to": "y", "mode": "bike"}, {"from": "y", "to": "here", "mode": "walk"}]
    assert [d["label"] for d in directions_links(blocks, legs)] == ["X → Y"]  # legs touching the start are dropped
    assert len(directions_links(blocks, legs, {"lat": 0.5, "lon": 0.5})) == 3
    assert "travelmode=bicycling" in directions_links(blocks, legs)[0]["url"]


def test_group_plan_history_share_and_delete_survive_a_restart(tmp_path):
    db = tmp_path / "g.db"
    c1 = TestClient(create_app(lambda: ScriptedProvider(overlook_script()), lambda: NOW, db_path=str(db)))
    g = new_group(c1)
    gid, ann = g["group_id"], g["member_token"]
    bob = join(c1, g, "Bob")
    c1.put(f"/api/groups/{gid}/me/inputs", headers=H(ann), json=inputs(11, 15, budget_dollars=50))
    c1.put(f"/api/groups/{gid}/me/inputs", headers=H(bob["member_token"]), json=inputs(12, 16, budget_dollars=37.5, step_free=True))
    assert c1.post(f"/api/groups/{gid}/plan", headers=H(ann)).status_code == 200
    v = wait_run(c1, gid, ann)
    assert v["candidate"] is not None and v["accepted"] is None
    assert c1.post(f"/api/groups/{gid}/decision", headers=H(ann), json={"decision": "accept"}).status_code == 200

    c2 = TestClient(create_app(lambda: ScriptedProvider([]), lambda: NOW, db_path=str(db)))  # restart
    after = c2.get(f"/api/groups/{gid}", headers=H(ann)).json()
    assert after["accepted"]["window"] == "Sat 12:00 PM-3:00 PM" and after["accepted"]["stale"] is False
    bob_view = c2.get(f"/api/groups/{gid}", headers=H(bob["member_token"])).json()
    rows = {p["name"]: p for p in bob_view["accepted"]["people"]}
    assert rows["Bob"]["my_cost"]["cap"] == 37.5 and "my_cost" not in rows["Ann"]  # privacy holds after a reload
    assert "50" not in json.dumps(bob_view["accepted"]["people"])
    assert [(h["status"], h["kind"]) for h in c2.get(f"/api/groups/{gid}/history", headers=H(bob["member_token"])).json()["versions"]] == [("accepted", "proposal")]

    assert c2.post(f"/api/groups/{gid}/share", headers=H(bob["member_token"]), json={"enabled": True}).status_code == 403
    token = c2.post(f"/api/groups/{gid}/share", headers=H(ann), json={"enabled": True}).json()["share_token"]
    pub = TestClient(c2.app).get(f"/api/shared/{token}").json()
    assert pub["title"] == "Saturday out" and "Bob" not in json.dumps(pub) and "people" not in pub
    assert c2.get(f"/api/groups/{gid}/export.ics", headers=H(bob["member_token"])).status_code == 200

    assert c2.delete(f"/api/groups/{gid}", headers=H(bob["member_token"])).status_code == 403
    assert c2.delete(f"/api/groups/{gid}", headers=H(ann)).status_code == 200
    assert c2.get(f"/api/groups/{gid}", headers=H(ann)).status_code == 404
    assert TestClient(c2.app).get(f"/api/shared/{token}").status_code == 404
    st = Store(str(db))
    assert st.list_versions("group", gid) == [] and st.list_members(gid) == [] and st.get_group(gid) is None
