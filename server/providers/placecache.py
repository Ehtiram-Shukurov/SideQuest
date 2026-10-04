"""SQLite cache of raw Overpass answers, so a public-service outage does not stop planning near a place
that was searched before. Entries are served stale only when the live service fails, and then the
caller must say so. Lazy connect, like the route cache."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class PlaceCache:
    def __init__(self, path: str = ":memory:") -> None:
        self._path, self._conn, self._lock = path, None, threading.RLock()

    def _db(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                if self._path != ":memory:":
                    Path(self._path).parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(self._path, check_same_thread=False)
                self._conn.execute("CREATE TABLE IF NOT EXISTS place_cache(key TEXT PRIMARY KEY, body TEXT, at REAL)")
            return self._conn

    def get(self, key: str) -> tuple[list[dict], float] | None:
        """(elements, saved_at epoch seconds), or None."""
        with self._lock:
            row = self._db().execute("SELECT body, at FROM place_cache WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0]), row[1]
        except ValueError:
            return None

    def put(self, key: str, elements: list[dict], at: float) -> None:
        with self._lock:
            self._db().execute("INSERT OR REPLACE INTO place_cache(key,body,at) VALUES(?,?,?)", (key, json.dumps(elements), at))
            self._db().commit()
