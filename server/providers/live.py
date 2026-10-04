"""Live, keyless data for the MVP: OpenStreetMap (Overpass) places, Open-Meteo forecast, and
route times (real OpenStreetMap routing via FOSSGIS when a routing client is given, otherwise
straight-line ESTIMATES). Limitations are surfaced to the model and the user:

* OSM hours/prices come from community tags and may be missing or stale. Unparseable hours stay
  UNKNOWN; a missing price stays UNKNOWN (only an explicit fee=no counts as free).
* Routes are straight-line distance x a detour factor at a fixed speed, not a routing engine.
* Open-Meteo's free service is for non-commercial use; check its terms before commercial use.
"""
from __future__ import annotations

import dataclasses
import math
import re
import time as _time
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from server.http import make_client
from server.models import Cost, Evidence, Place, TimeWindow
from server.models.weather import ForecastPeriod
from server.planning.assemble import LegEstimate

from .placecache import PlaceCache
from .base import PlaceDetails, PlaceSummary, ProviderError, category_for_words
from .synthetic import SPEED_KMH, _haversine_m

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Public mirrors, tried in order only when the main server fails (a deployed copy could not reach the main one).
OVERPASS_FALLBACKS = ("https://overpass.private.coffee/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "SideQuest-MVP/0.1 (hackathon prototype)"
DETOUR = 1.3

# category -> (OSM key, accepted values)
CATEGORIES = {
    "food": ("amenity", {"cafe", "restaurant", "ice_cream", "fast_food"}),
    "outdoor": ("leisure", {"park", "garden"}),
    "scenic": ("tourism", {"viewpoint"}),
    "culture": ("tourism", {"museum", "gallery", "attraction"}),
}
# (typical, minimum) visit minutes by OSM value
PLACE_FRESH_S = 24 * 3600  # a cached answer this young is used without asking Overpass
PLACE_STALE_MAX_S = 30 * 24 * 3600  # an older one is used only when Overpass fails
PER_CATEGORY = 40  # results kept per category from Overpass
DURATIONS = {"cafe": (45, 20), "restaurant": (60, 30), "ice_cream": (20, 10), "fast_food": (30, 15),
             "park": (45, 20), "garden": (40, 15), "viewpoint": (20, 10), "museum": (90, 45),
             "gallery": (60, 30), "attraction": (45, 20)}
DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_RULE = re.compile(r"(?:(?P<days>[A-Za-z, \-]+?)\s+)?(?P<times>\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}"
                   r"(?:\s*,\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})*)")


def _days_in(spec: str | None) -> set[int] | None:
    if spec is None:
        return set(range(7))
    out: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if "-" in part:
            a, _, b = part.partition("-")
            if a not in DAYS or b not in DAYS:
                return None
            i, j = DAYS.index(a), DAYS.index(b)
            out |= set(range(i, j + 1)) if i <= j else set(range(i, 7)) | set(range(0, j + 1))
        elif part in DAYS:
            out.add(DAYS.index(part))
        else:
            return None
    return out


def parse_hours(raw: str | None, day: date, tz: ZoneInfo) -> tuple[TimeWindow, ...] | None:
    """Parse simple OSM opening_hours for `day`. None = unknown (unparseable or absent);
    () = parsed and closed that day. Anything exotic (PH, sunrise, off, week ranges) -> None."""
    if not raw:
        return None
    raw = raw.strip()

    def at(d: date, h: int, m: int) -> datetime:
        base = d + timedelta(days=1) if h == 24 else d
        return datetime.combine(base, time(0 if h == 24 else h, m), tzinfo=tz).astimezone(UTC)

    if raw == "24/7":
        return (TimeWindow(start=at(day, 0, 0), end=at(day, 24, 0)),)
    windows: list[TimeWindow] = []
    for rule in (r.strip() for r in raw.split(";") if r.strip()):
        m = _RULE.fullmatch(rule)
        if not m:
            return None
        days = _days_in(m.group("days"))
        if days is None:
            return None
        if day.weekday() not in days:
            continue
        for span in re.findall(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", m.group("times")):
            a, b = (x.strip() for x in span.split("-"))
            (h1, m1), (h2, m2) = (map(int, a.split(":")), map(int, b.split(":")))
            start = at(day, h1, m1)
            end = at(day, h2, m2)
            if end <= start:  # closes after midnight
                end = at(day + timedelta(days=1), h2, m2)
            windows.append(TimeWindow(start=start, end=end))
    return tuple(windows)


class LiveWorld:
    """Implements PlacesProvider, RoutesProvider and WeatherProvider with live public data."""

    name = "OpenStreetMap + Open-Meteo (route times are estimates)"
    synthetic = False
    modes = ("walk", "bike", "car")
    horizon_days = 7

    def __init__(self, *, lat: float, lon: float, tz: str, origin_id: str, now: datetime,
                 radius_m: int = 2000, client: httpx.Client | None = None, routing: Any = None,
                 cache: PlaceCache | None = None, wall: Any = _time.time):
        self._lat, self._lon, self._tz, self._origin_id = lat, lon, ZoneInfo(tz), origin_id
        self._now = now.astimezone(UTC)
        self._radius = radius_m
        self._client = client or make_client(timeout=25.0, headers={"User-Agent": USER_AGENT})
        self._elements: dict[str, dict] | None = None
        self._points: dict[str, tuple[float, float]] = {origin_id: (lat, lon)}
        self._cache, self._wall, self._stale_at = cache, wall, None
        self._routing = routing  # a FossgisRouting, or None for straight-line estimates only
        self.name = "OpenStreetMap + Open-Meteo" + ("" if routing else " (route times are estimates)")

    def register_points(self, points: dict[str, tuple[float, float]]) -> None:
        """Make already-known places routable without re-querying (used when a saved trip is restored)."""
        self._points.update(points)

    # --- places (Overpass) -------------------------------------------------------

    def data_notes(self) -> list[str]:
        """Said to the model and shown on the plan when the places are not fresh."""
        if self._stale_at is None:
            return []
        when = datetime.fromtimestamp(self._stale_at, UTC).astimezone(self._tz)
        return [f"The live OpenStreetMap places service was unavailable, so places come from a saved copy "
                f"from {when:%Y-%m-%d %H:%M}. Venues may have changed or closed."]

    def _fetch_elements(self, lat: float, lon: float) -> list[dict]:
        # One request, but a separate capped `out` per category: a single shared cap let a dense block of
        # cafes crowd every park out of the answer (found with a real run near a university).
        parts = []
        for i, (key, vals) in enumerate(CATEGORIES.values()):
            rx = "|".join(sorted(vals))
            parts.append(f'nwr(around:{self._radius},{lat},{lon})["{key}"~"^({rx})$"]["name"]->.c{i};.c{i} out center {PER_CATEGORY};')
        query = f"[out:json][timeout:25];{''.join(parts)}"
        resp, failure, kind = None, "", "unavailable"
        for url in (OVERPASS_URL, *OVERPASS_FALLBACKS):
            try:
                resp = self._client.post(url, data={"data": query})
            except httpx.HTTPError as exc:
                resp, failure, kind = None, f"Overpass request failed: {type(exc).__name__}", "unavailable"
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                failure = f"Overpass busy ({resp.status_code}); try again shortly"
                kind = "rate_limited" if resp.status_code in (429, 504) else "unavailable"
                resp = None
                continue
            break
        if resp is None:
            raise ProviderError(kind, failure)
        if resp.status_code != 200:
            raise ProviderError("unavailable", f"Overpass returned {resp.status_code}")
        try:
            return list(resp.json().get("elements", []))
        except (ValueError, AttributeError):
            raise ProviderError("unavailable", "Overpass returned an unreadable response") from None

    def _load(self) -> dict[str, dict]:
        if self._elements is not None:
            return self._elements
        lat, lon = round(self._lat, 3), round(self._lon, 3)  # ~100 m: the same spot reuses a saved answer
        key = f"v2:{lat:.3f},{lon:.3f},{self._radius}"
        now = self._wall()
        cached = self._cache.get(key) if self._cache else None
        if cached and now - cached[1] < PLACE_FRESH_S:
            raw = cached[0]
        else:
            try:
                raw = self._fetch_elements(lat, lon)
                if self._cache:
                    self._cache.put(key, raw, now)
            except ProviderError:
                if not cached or now - cached[1] > PLACE_STALE_MAX_S:
                    raise
                raw, self._stale_at = cached[0], cached[1]
        els: dict[str, dict] = {}
        for e in raw:
            elat = e.get("lat") or (e.get("center") or {}).get("lat")
            elon = e.get("lon") or (e.get("center") or {}).get("lon")
            if elat is None or elon is None:
                continue
            pid = f"osm-{e['type']}-{e['id']}"
            els[pid] = {"tags": e.get("tags", {}), "lat": elat, "lon": elon, "type": e["type"], "osm_id": e["id"]}
            self._points[pid] = (elat, elon)
        self._elements = els
        return els

    @staticmethod
    def _kind(tags: dict) -> tuple[str, str] | None:
        for cat, (key, vals) in CATEGORIES.items():
            if tags.get(key) in vals:
                return cat, tags[key]
        return None

    def _filter(self, cat: str, q: str) -> list[tuple[float, PlaceSummary]]:
        out = []
        for pid, e in self._load().items():
            kind = self._kind(e["tags"])
            if kind is None or (cat and kind[0] != cat):
                continue
            name = e["tags"].get("name", "")
            if q and q not in name.lower() and q not in kind[1]:
                continue
            d = _haversine_m((self._lat, self._lon), (e["lat"], e["lon"]))
            out.append((d, PlaceSummary(pid, name, (kind[0], kind[1]), e["lat"], e["lon"], "OpenStreetMap")))
        out.sort(key=lambda t: t[0])
        return out

    def search(self, *, category: str, query: str, limit: int) -> list[PlaceSummary]:
        cat, q = category.strip().lower(), query.strip().lower()
        if cat and cat not in CATEGORIES:  # e.g. category="coffee": treat it as everyday words
            q, cat = q or cat, category_for_words(cat) or ""
        found = self._filter(cat, q)
        if not found and q:  # a literal name/tag match found nothing: try what the words mean ("coffee" -> food)
            mapped = category_for_words(q)
            if mapped:
                found = self._filter(cat or mapped, "")
        return [p for _, p in found[:limit]]

    def category_counts(self) -> dict[str, int]:
        """How many nearby places each category has, so an empty search can say what IS available."""
        counts: dict[str, int] = {}
        for e in self._load().values():
            kind = self._kind(e["tags"])
            if kind:
                counts[kind[0]] = counts.get(kind[0], 0) + 1
        return counts

    def details(self, place_id: str, visit_day: date) -> PlaceDetails | None:
        e = self._load().get(place_id)
        if e is None:
            return None
        tags = e["tags"]
        cat, value = self._kind(tags) or ("", "")
        typical, minimum = DURATIONS.get(value, (45, 20))
        raw_hours = tags.get("opening_hours")
        windows = parse_hours(raw_hours, visit_day, self._tz)
        fee = tags.get("fee")
        price = Cost(currency="USD", min_minor=0, max_minor=0, basis="per_person",
                     quoted_at=self._now, evidence_id=f"ev-{place_id}-price") if fee == "no" else None
        wc = tags.get("wheelchair")
        step_free = True if wc == "yes" else False if wc == "no" else None
        outdoor = True if cat in ("outdoor", "scenic") else False if cat in ("food", "culture") and value != "garden" else None
        url = f"https://www.openstreetmap.org/{e['type']}/{e['osm_id']}"
        noon = datetime.combine(visit_day, time(12), tzinfo=self._tz).astimezone(UTC)

        def ev(field, value, known, eid):
            return Evidence(id=eid, field=field, value=value, source="OpenStreetMap", url=url,
                            retrieved_at=self._now, applies_at=noon,
                            status="unverified" if known else "unknown")

        evidence = (ev("opening_hours", raw_hours, windows is not None, f"ev-{place_id}-hours"),
                    ev("price", fee, price is not None, f"ev-{place_id}-price"),
                    ev("step_free", wc, step_free is not None, f"ev-{place_id}-access"))
        place = Place(id=place_id, name=tags.get("name", place_id), lat=e["lat"], lon=e["lon"],
                      timezone=str(self._tz), categories=(cat, value), opening_windows=windows, price=price,
                      min_visit_minutes=minimum, typical_visit_minutes=typical, optional_shrink=(cat == "food"),
                      step_free=step_free, outdoor=outdoor, evidence_ids=tuple(x.id for x in evidence))
        return PlaceDetails(place, evidence, (tags.get("description") or "")[:200])

    # --- routes (straight-line estimates) ---------------------------------------

    def estimate(self, origin_id: str, dest_id: str, mode: str, depart: datetime) -> LegEstimate | None:
        if mode not in SPEED_KMH:
            raise ProviderError("unsupported", f"mode {mode!r} not supported; use one of {self.modes}")
        a, b = self._points.get(origin_id), self._points.get(dest_id)
        if a is None or b is None:
            return None
        dist = _haversine_m(a, b) * DETOUR
        base = dist / 1000 / SPEED_KMH[mode] * 3600
        extra = 300 if mode == "car" else 0
        return LegEstimate(mode=mode, min_s=int(round(base)) + extra, max_s=int(round(base * 1.3)) + extra + 60,
                           distance_m=int(round(dist)), evidence_id=f"ev-route-{origin_id}-{dest_id}-{mode}",
                           source="straight-line estimate")

    def estimate_many(self, pairs, mode: str, depart: datetime) -> dict[tuple[str, str], LegEstimate | None]:
        """All legs of a plan at once: ONE routing request, cached. A leg the service cannot route is None;
        if the service itself fails, every leg falls back to a straight-line estimate labelled as such."""
        if mode not in SPEED_KMH:
            raise ProviderError("unsupported", f"mode {mode!r} not supported; use one of {self.modes}")
        pts = {x: self._points[x] for a, b in pairs for x in (a, b) if x in self._points}
        known = [(a, b) for a, b in pairs if a in pts and b in pts]
        routed: dict = {}
        if self._routing is not None and known:
            try:
                routed = self._routing.matrix(mode, [(i, *c) for i, c in pts.items()], known)
            except ProviderError:
                routed = {}  # fall back below
        out: dict[tuple[str, str], LegEstimate | None] = {}
        for a, b in pairs:
            if (a, b) not in known:
                out[(a, b)] = None
            elif (a, b) in routed:
                r = routed[(a, b)]
                if r is None:  # genuinely unreachable: do not guess
                    out[(a, b)] = None
                    continue
                dist, dur = r
                extra = 300 if mode == "car" else 0  # parking / access buffer
                out[(a, b)] = LegEstimate(mode=mode, min_s=int(round(dur)) + extra,
                                          max_s=int(round(dur * 1.25)) + extra + 60, distance_m=int(round(dist)),
                                          evidence_id=f"ev-route-osm-{a}-{b}-{mode}", source="OpenStreetMap routing (FOSSGIS)")
            else:
                est = self.estimate(a, b, mode, depart)
                out[(a, b)] = None if est is None else dataclasses.replace(
                    est, evidence_id=(est.evidence_id or "").replace("ev-route-", "ev-route-est-", 1))
        return out

    # --- weather (Open-Meteo) ----------------------------------------------------

    def forecast(self, lat: float, lon: float, start: datetime, end: datetime) -> list[ForecastPeriod]:
        if start > self._now + timedelta(days=self.horizon_days):
            return []
        try:
            resp = self._client.get(OPEN_METEO_URL, params={
                "latitude": lat, "longitude": lon, "hourly": "precipitation_probability",
                "timezone": "GMT", "forecast_days": 7})
        except httpx.HTTPError as exc:
            raise ProviderError("unavailable", f"Open-Meteo request failed: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise ProviderError("unavailable", f"Open-Meteo returned {resp.status_code}")
        try:
            h = resp.json().get("hourly", {})
        except ValueError:
            raise ProviderError("unavailable", "Open-Meteo returned an unreadable response") from None
        out = []
        for t, p in zip(h.get("time", []), h.get("precipitation_probability", [])):
            if p is None:
                continue
            s = datetime.fromisoformat(t).replace(tzinfo=UTC)
            if s < end and s + timedelta(hours=1) > start:
                out.append(ForecastPeriod(start=s, end=s + timedelta(hours=1),
                                          precip_probability=p / 100, evidence_id="ev-open-meteo"))
        return out
