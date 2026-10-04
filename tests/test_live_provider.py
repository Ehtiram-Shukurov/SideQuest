"""LiveWorld unit tests. No network is used: the HTTP client is mocked or _load is bypassed."""
from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from server.providers.base import ProviderError
from server.providers.live import LiveWorld


def _world(client=None) -> LiveWorld:
    return LiveWorld(lat=44.97, lon=-93.26, tz="America/Chicago", origin_id="here",
                     now=datetime.now(UTC), client=client)


def _element(**tags):
    return {"tags": tags, "lat": 44.971, "lon": -93.261, "type": "node", "osm_id": 1}


def test_construction_survives_unparseable_proxy_env(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost,[::1]")
    monkeypatch.setenv("no_proxy", "localhost,[::1]")
    assert _world().name  # must not raise httpx.InvalidURL


def test_cafe_allows_a_shortened_visit_but_a_park_does_not():
    w = _world()
    w._elements = {"osm-node-1": _element(amenity="cafe", name="Cafe"),
                   "osm-node-2": _element(leisure="park", name="Park")}
    day = date(2026, 10, 3)
    assert w.details("osm-node-1", day).place.optional_shrink is True
    assert w.details("osm-node-2", day).place.optional_shrink is False


def _garbage_client() -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_garbled_overpass_body_is_a_labeled_provider_error():
    w = _world(_garbage_client())
    with pytest.raises(ProviderError) as exc:
        w.search(category="", query="", limit=5)
    assert exc.value.kind == "unavailable"


def test_garbled_open_meteo_body_is_a_labeled_provider_error():
    w = _world(_garbage_client())
    now = datetime.now(UTC)
    with pytest.raises(ProviderError) as exc:
        w.forecast(44.97, -93.26, now, now)
    assert exc.value.kind == "unavailable"


def test_overpass_query_caps_each_category_separately():
    """A real run near a university got 0 parks: one shared cap let 60 cafes crowd every other category out."""
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["q"] = req.content.decode()
        return httpx.Response(200, json={"elements": []})

    _world(httpx.Client(transport=httpx.MockTransport(handler))).search(category="", query="", limit=5)
    q = httpx.QueryParams(seen["q"])["data"]
    assert q.count(" out center ") == 4 and "out center 60" not in q  # one capped output per category


def test_overpass_falls_back_to_a_mirror_when_the_main_server_is_unreachable():
    hosts = []

    def handler(req: httpx.Request) -> httpx.Response:
        hosts.append(req.url.host)
        if req.url.host == "overpass-api.de":
            raise httpx.ConnectError("blocked")
        return httpx.Response(200, json={"elements": [{"type": "node", "id": 7, "lat": 44.971, "lon": -93.261,
                                                       "tags": {"amenity": "cafe", "name": "Mirror Cafe"}}]})

    found = _world(httpx.Client(transport=httpx.MockTransport(handler))).search(category="food", query="", limit=5)
    assert [p.name for p in found] == ["Mirror Cafe"] and hosts[0] == "overpass-api.de" and len(hosts) == 2


def test_every_overpass_server_failing_is_still_a_labeled_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked")

    with pytest.raises(ProviderError) as exc:
        _world(httpx.Client(transport=httpx.MockTransport(handler))).search(category="", query="", limit=5)
    assert exc.value.kind == "unavailable"


# --- saved copy of places (survives an Overpass outage) -------------------------------------

from server.providers.placecache import PlaceCache  # noqa: E402

_CAFE = {"type": "node", "id": 7, "lat": 44.971, "lon": -93.261, "tags": {"amenity": "cafe", "name": "Saved Cafe"}}


def _cached_world(handler, cache, wall):
    return LiveWorld(lat=44.97, lon=-93.26, tz="America/Chicago", origin_id="here", now=datetime.now(UTC),
                     client=httpx.Client(transport=httpx.MockTransport(handler)), cache=cache, wall=wall)


def test_a_fresh_saved_copy_is_used_without_calling_overpass():
    cache, calls = PlaceCache(), []

    def ok(req):
        calls.append(1)
        return httpx.Response(200, json={"elements": [_CAFE]})

    _cached_world(ok, cache, lambda: 1000.0).search(category="food", query="", limit=5)
    w2 = _cached_world(lambda r: (_ for _ in ()).throw(AssertionError("must not call")), cache, lambda: 1000.0 + 3600)
    assert [p.name for p in w2.search(category="food", query="", limit=5)] == ["Saved Cafe"]
    assert len(calls) == 1 and w2.data_notes() == []  # fresh: no staleness warning


def test_an_old_saved_copy_is_served_when_overpass_fails_and_it_says_so():
    cache = PlaceCache()
    _cached_world(lambda r: httpx.Response(200, json={"elements": [_CAFE]}), cache, lambda: 1000.0).search(category="", query="", limit=5)

    def down(req):
        raise httpx.ConnectError("blocked")

    later = 1000.0 + 3 * 24 * 3600  # older than the freshness window, younger than the stale limit
    w = _cached_world(down, cache, lambda: later)
    assert [p.name for p in w.search(category="food", query="", limit=5)] == ["Saved Cafe"]
    assert w.data_notes() and "saved copy" in w.data_notes()[0]


def test_without_any_saved_copy_an_outage_is_still_an_error():
    def down(req):
        raise httpx.ConnectError("blocked")

    with pytest.raises(ProviderError):
        _cached_world(down, PlaceCache(), lambda: 5.0).search(category="", query="", limit=5)
    cache = PlaceCache()
    cache.put("v2:44.970,-93.260,2000", [_CAFE], 0.0)  # far too old to trust
    with pytest.raises(ProviderError):
        _cached_world(down, cache, lambda: 60 * 24 * 3600.0).search(category="", query="", limit=5)
