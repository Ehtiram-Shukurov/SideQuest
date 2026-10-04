"""P2: real route times from the free FOSSGIS service, with cache, rate limit and a labelled fallback.
All HTTP is mocked: no network."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from server.api.app import route_notes
from server.models import ActivityBlock, TravelLeg, Plan
from server.providers.base import ProviderError
from server.providers.live import LiveWorld
from server.providers.routing import FossgisRouting, RouteCache
from server.tools.context import ToolContext
from server.tools.planning_tools import build_registry

from .helpers import at, member, trip

NOW = datetime(2026, 6, 4, 12, tzinfo=UTC)
POINTS = [("here", 44.9778, -93.2650), ("a", 44.9790, -93.2477), ("b", 44.9860, -93.2560)]
TABLE = {"code": "Ok", "durations": [[0, 1408, 1043], [1410, 0, 700], [1050, 710, 0]],
         "distances": [[0, 1760, 1304], [1762, 0, 900], [1306, 902, 0]]}


def routing(handler, **kw):
    seen: list[httpx.Request] = []
    sleeps: list[float] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    r = FossgisRouting(client=httpx.Client(transport=httpx.MockTransport(wrapped)), cache=RouteCache(),
                       sleep=sleeps.append, clock=lambda: 0.0, wall=lambda: 1000.0, **kw)
    return r, seen, sleeps


def test_one_matrix_request_uses_the_right_profile_agent_and_order_then_the_cache_and_limiter_apply():
    r, seen, sleeps = routing(lambda req: httpx.Response(200, json=TABLE))
    pairs = [("here", "a"), ("a", "b"), ("b", "here")]
    got = r.matrix("walk", POINTS, pairs)
    assert got[("here", "a")] == (1760.0, 1408.0) and len(seen) == 1
    url = str(seen[0].url)
    assert "/routed-foot/table/v1/driving/" in url and "-93.265000,44.977800;" in url  # lon,lat order, foot profile
    assert "SideQuest" in seen[0].headers["user-agent"] and "annotations=duration%2Cdistance" in url
    assert r.matrix("walk", POINTS, pairs) == got and len(seen) == 1  # served from the cache
    r.matrix("bike", POINTS, pairs)
    assert "/routed-bike/" in str(seen[1].url) and sleeps == [1.1]  # a second request waits out the 1 req/s limit
    with pytest.raises(ProviderError):
        r.matrix("teleport", POINTS, pairs)


def test_routes_become_estimates_with_a_band_and_a_source_and_failures_fall_back_visibly():
    def build(handler):
        w = LiveWorld(lat=44.9778, lon=-93.2650, tz="America/Chicago", origin_id="here", now=NOW,
                      client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
                      routing=routing(handler)[0])
        w.register_points({"a": (44.9790, -93.2477), "b": (44.9860, -93.2560)})
        return w

    pairs = [("here", "a"), ("a", "b"), ("here", "zzz")]
    ok = build(lambda req: httpx.Response(200, json=TABLE)).estimate_many(pairs, "walk", NOW)
    e = ok[("here", "a")]
    assert (e.min_s, e.max_s, e.distance_m) == (1408, round(1408 * 1.25) + 60, 1760)  # real time + uncertainty band
    assert e.source.startswith("OpenStreetMap routing") and "ev-route-osm" in e.evidence_id
    assert ok[("here", "zzz")] is None  # an unknown place has no estimate
    car = build(lambda req: httpx.Response(200, json=TABLE)).estimate_many([("here", "a")], "car", NOW)[("here", "a")]
    assert car.min_s == 1408 + 300  # the parking/access buffer still applies to cars

    down = build(lambda req: httpx.Response(503)).estimate_many(pairs[:2], "walk", NOW)
    assert down[("here", "a")].source == "straight-line estimate" and "ev-route-est" in down[("here", "a")].evidence_id

    holes = dict(TABLE, durations=[[0, None, 1], [1, 0, 1], [1, 1, 0]])
    cut = build(lambda req: httpx.Response(200, json=holes)).estimate_many(pairs[:2], "walk", NOW)
    assert cut[("here", "a")] is None and cut[("a", "b")] is not None  # unreachable is reported, not guessed


def test_the_tool_makes_one_routing_request_for_a_whole_plan_and_notes_say_where_times_came_from():
    r, seen, _ = routing(lambda req: httpx.Response(200, json=TABLE))
    t = trip([member("m1")], start=at(10), end=at(14))
    w = LiveWorld(lat=44.9778, lon=-93.2650, tz="America/Chicago", origin_id="home", now=NOW,
                  client=httpx.Client(transport=httpx.MockTransport(lambda q: httpx.Response(500))), routing=r)
    w.register_points({"a": (44.9790, -93.2477), "b": (44.9860, -93.2560)})
    ctx = ToolContext(trip=t, places=w, routes=w, weather=w, now=NOW)
    ctx.candidates.update({"a": object(), "b": object()})  # ids a provider returned
    out = build_registry(ctx).call("estimate_routes", {"place_ids": ["a", "b"], "mode": "walk"})
    assert len(seen) == 1 and out.data["legs"] and all(l["source"].startswith("OpenStreetMap") for l in out.data["legs"])

    def plan_with(*evidence):
        blk = ActivityBlock(id="b1", place_id="a", name="A", start=at(10), end=at(11), timezone="America/Chicago", attendees=("m1",))
        legs = tuple(TravelLeg(id=f"l{i}", from_id="home", to_id="a", mode="walk", depart=at(10), duration_min_s=1,
                               duration_max_s=2, attendees=("m1",), evidence_ids=(e,)) for i, e in enumerate(evidence))
        return Plan(id="p", trip_id="t", blocks=(blk,), legs=legs)

    osm = route_notes(plan_with("ev-route-osm-x"), False)[0]
    assert "OpenStreetMap routing" in osm and "© OpenStreetMap contributors" in osm and "straight-line" not in osm
    assert "straight-line estimates" in route_notes(plan_with("ev-route-est-x"), False)[0]
    mixed = route_notes(plan_with("ev-route-osm-x", "ev-route-est-y"), False)[0]
    assert "Some route times" in mixed and "© OpenStreetMap" in mixed
    assert route_notes(plan_with("ev-route-osm-x"), True) == ["DEMO DATA: venues, prices and forecasts are invented."]
