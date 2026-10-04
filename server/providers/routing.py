"""Real route times from the free FOSSGIS OSRM service (routing.openstreetmap.de).

Verified with live calls (2026-10-03): foot, bike and car profiles, and the matrix ("table")
endpoint, all without a key. Terms (https://routing.openstreetmap.de/about.html): at most one
request per second, a valid User-Agent, show attribution, no heavy use or scraping. So this client:
one matrix request per plan, a SQLite cache (their data refreshes about every two days), a request
limiter, and it raises ProviderError on any failure so the caller can fall back to a LABELED
straight-line estimate. Durations are typical speeds without traffic; callers add an uncertainty band.

NOTE: the public OSRM demo server (router.project-osrm.org) ignores the profile and returns car
times for "foot", so it must not be used for walking. Do not point this client at it."""
from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .base import ProviderError

BASE_URL = "https://routing.openstreetmap.de"
PROFILE = {"walk": "routed-foot", "bike": "routed-bike", "car": "routed-car"}
USER_AGENT = "SideQuest-MVP/0.1 (hackathon prototype)"
CACHE_TTL_S = 24 * 3600
MAX_POINTS = 25


class RouteCache:
    """SQLite cache of (profile, rounded endpoints) -> (distance m, duration s). Lazy connect."""

    def __init__(self, path: str = ":memory:") -> None:
        self._path, self._conn, self._lock = path, None, threading.RLock()

    def _db(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                if self._path != ":memory:":
                    Path(self._path).parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(self._path, check_same_thread=False)
                self._conn.execute("CREATE TABLE IF NOT EXISTS route_cache(key TEXT PRIMARY KEY, dist REAL, dur REAL, at REAL)")
            return self._conn

    @staticmethod
    def key(profile: str, a: tuple[float, float], b: tuple[float, float]) -> str:
        return f"{profile}:{a[0]:.4f},{a[1]:.4f}>{b[0]:.4f},{b[1]:.4f}"  # ~11 m precision

    def get(self, key: str, now: float) -> tuple[float, float] | None:
        with self._lock:
            row = self._db().execute("SELECT dist, dur, at FROM route_cache WHERE key = ?", (key,)).fetchone()
        return (row[0], row[1]) if row and now - row[2] < CACHE_TTL_S else None

    def put(self, key: str, dist: float, dur: float, now: float) -> None:
        with self._lock, self._db():
            self._db().execute("INSERT OR REPLACE INTO route_cache(key,dist,dur,at) VALUES(?,?,?,?)", (key, dist, dur, now))


class FossgisRouting:
    def __init__(self, *, client: httpx.Client | None = None, cache: RouteCache | None = None,
                 min_interval_s: float = 1.1, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 base_url: str = BASE_URL, timeout_s: float = 12.0) -> None:
        self._client = client or httpx.Client(timeout=timeout_s, headers={"User-Agent": USER_AGENT})
        self._cache = cache or RouteCache()
        self._min, self._sleep, self._clock, self._wall = min_interval_s, sleep, clock, wall
        self._base, self._last, self._gate = base_url.rstrip("/"), None, threading.Lock()

    def matrix(self, mode: str, points: Sequence[tuple[str, float, float]],
               wanted: Sequence[tuple[str, str]]) -> dict[tuple[str, str], tuple[float, float] | None]:
        """(distance m, duration s) for each wanted (from_id, to_id); None = no route. One request at most."""
        if mode not in PROFILE:
            raise ProviderError("unsupported", f"mode {mode!r} is not routable here")
        if len(points) > MAX_POINTS:
            raise ProviderError("unsupported", f"at most {MAX_POINTS} points per matrix")
        coords = {pid: (lat, lon) for pid, lat, lon in points}
        profile, now = PROFILE[mode], self._wall()
        out: dict[tuple[str, str], tuple[float, float] | None] = {}
        missing: list[tuple[str, str]] = []
        for a, b in wanted:
            hit = self._cache.get(RouteCache.key(profile, coords[a], coords[b]), now)
            if hit is not None:
                out[(a, b)] = hit
            else:
                missing.append((a, b))
        if not missing:
            return out
        ids = list(coords)
        path = ";".join(f"{coords[i][1]:.6f},{coords[i][0]:.6f}" for i in ids)  # OSRM wants lon,lat
        data = self._get(f"/{profile}/table/v1/driving/{path}", {"annotations": "duration,distance"})
        dur, dist = data.get("durations"), data.get("distances")
        if data.get("code") != "Ok" or not dur or not dist:
            raise ProviderError("unavailable", f"routing service said {data.get('code', 'no data')}")
        index = {pid: i for i, pid in enumerate(ids)}
        for a, b in missing:
            d, t = dist[index[a]][index[b]], dur[index[a]][index[b]]
            if d is None or t is None:
                out[(a, b)] = None  # genuinely unreachable: do not guess
                continue
            self._cache.put(RouteCache.key(profile, coords[a], coords[b]), float(d), float(t), now)
            out[(a, b)] = (float(d), float(t))
        return out

    def _get(self, path: str, params: dict[str, str]) -> dict:
        with self._gate:  # one request in flight, spaced by the service's rate limit
            if self._last is not None:
                wait = self._last + self._min - self._clock()
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()
            try:
                resp = self._client.get(self._base + path, params=params, headers={"User-Agent": USER_AGENT})
            except httpx.HTTPError as exc:
                raise ProviderError("unavailable", f"routing request failed: {type(exc).__name__}") from None
        if resp.status_code == 429:
            raise ProviderError("rate_limited", "routing service asked us to slow down")
        if resp.status_code != 200:
            raise ProviderError("unavailable", f"routing service returned {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise ProviderError("unavailable", "routing service returned invalid data") from None


_ = datetime, UTC  # (kept for callers that format cache timestamps)
