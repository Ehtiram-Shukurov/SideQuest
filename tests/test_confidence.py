"""P1: validation is honest about what it rests on. Labelling only: verdicts never change."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from server.agent.scripted import ScriptedProvider
from server.api.app import create_app
from server.models import Cost, Evidence
from server.planning.assemble import assemble_plan
from server.planning.validators import validate_plan

from .helpers import matrix, member, place, trip
from .test_api import BODY, script, wait

NOW = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
URL = "https://www.openstreetmap.org/node/1"


def ev(eid: str, field: str, status: str, url: str | None = None) -> Evidence:
    return Evidence(id=eid, field=field, source="OpenStreetMap", url=url, retrieved_at=NOW, status=status)


def setup(status: str):
    """A one-stop plan whose hours and price evidence have the given status."""
    t = trip([member("m1", cap=2000, requires_step_free=True)])
    price = Cost(currency="USD", min_minor=0, max_minor=0, basis="per_person", evidence_id="ev-price")
    p = place("a", price=price, step_free=True).model_copy(update={"evidence_ids": ("ev-hours", "ev-price", "ev-access")})
    plan = assemble_plan(t, [p], matrix({("home", "a"): 5})).plan
    evid = {"ev-hours": ev("ev-hours", "opening_hours", status, URL), "ev-price": ev("ev-price", "price", status, URL),
            "ev-access": ev("ev-access", "step_free", status, URL)}
    return t, p, plan, evid


def test_community_data_is_labelled_and_listed_but_verdicts_do_not_change():
    t, p, plan, evid = setup("unverified")
    plain = validate_plan(t, plan, {"a": p})  # no evidence supplied
    rep = validate_plan(t, plan, {"a": p}, evidence=evid)
    assert [c.status for c in rep.checks] == [c.status for c in plain.checks]  # hard outcomes identical
    assert rep.overall == plain.overall == "checked"
    assert rep.confidence == "community_data" and plain.confidence == "unassessed"
    assert all(c.basis is None for c in plain.checks)
    basis = {c.code: c.basis for c in rep.checks if c.basis}
    assert basis == {"OPEN_HOURS": "community", "ACCESSIBILITY": "community", "BUDGET_PER_PERSON": "community"}
    assert {v.field for v in rep.verify} == {"opening_hours", "price", "step_free"}
    assert all(v.url == URL and v.name for v in rep.verify)  # each item links to its source


def test_verified_evidence_gives_verified_confidence_and_an_empty_verify_list():
    t, p, plan, evid = setup("verified")
    rep = validate_plan(t, plan, {"a": p}, evidence=evid)
    assert rep.confidence == "verified" and rep.verify == ()
    assert {c.basis for c in rep.checks if c.basis} == {"verified"}


def test_missing_evidence_counts_as_not_verified_and_mixed_is_reported():
    t, p, plan, evid = setup("verified")
    rep = validate_plan(t, plan, {"a": p}, evidence={k: v for k, v in evid.items() if k != "ev-hours"})
    assert rep.overall == "checked"  # the verdict is unchanged
    assert next(c for c in rep.checks if c.code == "OPEN_HOURS").basis == "unknown"
    assert rep.confidence == "mixed" and [v.field for v in rep.verify] == ["opening_hours"]
    assert rep.verify[0].source == "unknown source" and rep.verify[0].url is None


def test_api_exposes_confidence_and_source_links():
    c = TestClient(create_app(lambda: ScriptedProvider(script()), lambda: NOW))
    made = c.post("/api/plans", json=BODY).json()
    c.headers["Authorization"] = "Bearer " + made["owner_token"]
    j = wait(c, made["run_id"])
    p = j["result"]["proposal"]
    assert p["confidence"] == "verified" and p["verify"] == []  # demo data is flagged DEMO elsewhere
    assert all("url" in f for b in p["blocks"] for f in b["facts"])


def test_a_plan_whose_facts_are_all_unknown_is_never_labelled_verified():
    """Found by a live run: unknown hours and price left no passing check with a basis, so the plan read 'verified'."""
    t, p, plan, evid = setup("unknown")
    rep = validate_plan(t, plan, {"a": p}, evidence=evid)
    assert rep.confidence != "verified"
    assert {c.basis for c in rep.checks if c.basis} == {"unknown"}
    assert rep.confidence == "unverified"
