"""SQLite persistence for group trips: groups, members, per-member inputs, questions.

Member tokens are stored only as SHA-256 hashes (they grant access to the member's data); the
invite token is stored in plain text so the organizer can re-copy it, and is revocable. Every
change that affects planning bumps `groups.version`, which is how stale proposals are detected.
Plans themselves are not stored here (in memory only)."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_MEMBERS = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, request TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
  tz TEXT NOT NULL, data_mode TEXT NOT NULL, invite_token TEXT, version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members(
  id TEXT PRIMARY KEY, group_id TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, joined_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS members_group ON members(group_id);
CREATE TABLE IF NOT EXISTS inputs(member_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions(
  id TEXT PRIMARY KEY, group_id TEXT NOT NULL, member_id TEXT NOT NULL, win_start TEXT NOT NULL,
  win_end TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  alt_start TEXT, alt_end TEXT, created_at TEXT NOT NULL, answered_at TEXT,
  UNIQUE(member_id, win_start, win_end));
CREATE TABLE IF NOT EXISTS solo_trips(
  id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, req TEXT NOT NULL, trip TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plan_versions(
  id TEXT PRIMARY KEY, owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
  status TEXT NOT NULL, snapshot TEXT NOT NULL, plan TEXT, places TEXT, evidence TEXT, diff TEXT,
  explanation TEXT NOT NULL DEFAULT '', restored_from INTEGER, created_at TEXT NOT NULL,
  UNIQUE(owner_kind, owner_id, seq));
CREATE TABLE IF NOT EXISTS shares(
  token TEXT PRIMARY KEY, owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(24)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        # Connect lazily: importing the app must not create a database file as a side effect.
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    @property
    def _db(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                if self._path != ":memory:":
                    Path(self._path).parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self._path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.executescript(SCHEMA)
                self._conn = conn
            return self._conn

    def _one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _bump(self, gid: str) -> None:
        self._db.execute("UPDATE groups SET version = version + 1 WHERE id = ?", (gid,))

    # --- groups --------------------------------------------------------------------

    def create_group(self, *, title: str, request: str, lat: float, lon: float, tz: str, data_mode: str,
                     organizer_name: str) -> dict[str, str]:
        gid, mid, mtoken, invite = _new_id(), _new_id(), new_token(), new_token()
        with self._lock, self._db:
            self._db.execute("INSERT INTO groups(id,title,request,lat,lon,tz,data_mode,invite_token,created_at)"
                             " VALUES(?,?,?,?,?,?,?,?,?)", (gid, title, request, lat, lon, tz, data_mode, invite, _now()))
            self._db.execute("INSERT INTO members(id,group_id,name,role,token_hash,joined_at) VALUES(?,?,?,?,?,?)",
                             (mid, gid, organizer_name, "organizer", hash_token(mtoken), _now()))
        return {"group_id": gid, "member_id": mid, "member_token": mtoken, "invite_token": invite}

    def get_group(self, gid: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM groups WHERE id = ?", (gid,))

    def group_by_invite(self, token: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM groups WHERE invite_token = ? AND invite_token IS NOT NULL", (token,))

    def update_group(self, gid: str, *, title: str | None = None, request: str | None = None) -> None:
        with self._lock, self._db:
            if title is not None:
                self._db.execute("UPDATE groups SET title = ? WHERE id = ?", (title, gid))
            if request is not None:
                self._db.execute("UPDATE groups SET request = ? WHERE id = ?", (request, gid))
            self._bump(gid)

    def set_invite(self, gid: str, *, enabled: bool) -> str | None:
        token = new_token() if enabled else None
        with self._lock, self._db:
            self._db.execute("UPDATE groups SET invite_token = ? WHERE id = ?", (token, gid))
        return token

    # --- members -------------------------------------------------------------------

    def add_member(self, gid: str, name: str) -> dict[str, str] | None:
        with self._lock, self._db:
            n = self._db.execute("SELECT COUNT(*) FROM members WHERE group_id = ?", (gid,)).fetchone()[0]
            if n >= MAX_MEMBERS:
                return None
            mid, token = _new_id(), new_token()
            self._db.execute("INSERT INTO members(id,group_id,name,role,token_hash,joined_at) VALUES(?,?,?,?,?,?)",
                             (mid, gid, name, "participant", hash_token(token), _now()))
            self._bump(gid)
        return {"member_id": mid, "member_token": token}

    def member_by_token(self, gid: str, token: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM members WHERE group_id = ? AND token_hash = ?", (gid, hash_token(token)))

    def list_members(self, gid: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM members WHERE group_id = ? ORDER BY joined_at, id", (gid,))

    def remove_member(self, gid: str, mid: str) -> bool:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM members WHERE id = ? AND group_id = ? AND role != 'organizer'", (mid, gid))
            if cur.rowcount:
                self._db.execute("DELETE FROM inputs WHERE member_id = ?", (mid,))
                self._db.execute("DELETE FROM questions WHERE member_id = ?", (mid,))
                self._bump(gid)
        return bool(cur.rowcount)

    # --- inputs --------------------------------------------------------------------

    def save_inputs(self, gid: str, mid: str, data: dict[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO inputs(member_id,data,updated_at) VALUES(?,?,?) "
                             "ON CONFLICT(member_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                             (mid, json.dumps(data), _now()))
            self._bump(gid)

    def get_inputs(self, mid: str) -> dict[str, Any] | None:
        row = self._one("SELECT data FROM inputs WHERE member_id = ?", (mid,))
        return json.loads(row["data"]) if row else None

    def all_inputs(self, gid: str) -> dict[str, dict[str, Any]]:
        rows = self._all("SELECT i.member_id, i.data FROM inputs i JOIN members m ON m.id = i.member_id "
                         "WHERE m.group_id = ?", (gid,))
        return {r["member_id"]: json.loads(r["data"]) for r in rows}

    # --- questions -----------------------------------------------------------------

    def add_question(self, gid: str, mid: str, win_start: str, win_end: str, text: str) -> str | None:
        """None if this member was already asked about this exact window."""
        qid = _new_id()
        with self._lock, self._db:
            try:
                self._db.execute("INSERT INTO questions(id,group_id,member_id,win_start,win_end,text,created_at)"
                                 " VALUES(?,?,?,?,?,?,?)", (qid, gid, mid, win_start, win_end, text, _now()))
            except sqlite3.IntegrityError:
                return None
        return qid

    def supersede_pending(self, gid: str, mid: str, keep: tuple[str, str] | None) -> None:
        """Expire this member's pending questions, except the one for window `keep` (if any)."""
        with self._lock, self._db:
            if keep is None:
                self._db.execute("UPDATE questions SET status='expired' WHERE group_id=? AND member_id=? AND status='pending'",
                                 (gid, mid))
            else:
                self._db.execute("UPDATE questions SET status='expired' WHERE group_id=? AND member_id=? AND status='pending' "
                                 "AND NOT (win_start=? AND win_end=?)", (gid, mid, keep[0], keep[1]))

    def list_questions(self, gid: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM questions WHERE group_id = ? ORDER BY created_at, id", (gid,))

    def get_question(self, gid: str, qid: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM questions WHERE id = ? AND group_id = ?", (qid, gid))

    def answer_question(self, gid: str, qid: str, status: str, alt: tuple[str, str] | None = None) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE questions SET status = ?, alt_start = ?, alt_end = ?, answered_at = ? "
                             "WHERE id = ? AND group_id = ?", (status, alt[0] if alt else None, alt[1] if alt else None,
                                                               _now(), qid, gid))

    # --- solo trips ------------------------------------------------------------------

    def create_solo(self, trip_id: str, req_json: str, trip_json: str) -> str:
        token = new_token()
        with self._lock, self._db:
            self._db.execute("INSERT INTO solo_trips(id,token_hash,req,trip,created_at) VALUES(?,?,?,?,?)",
                             (trip_id, hash_token(token), req_json, trip_json, _now()))
        return token

    def get_solo(self, trip_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM solo_trips WHERE id = ?", (trip_id,))

    def solo_by_token(self, trip_id: str, token: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM solo_trips WHERE id = ? AND token_hash = ?", (trip_id, hash_token(token)))

    def update_solo_trip(self, trip_id: str, trip_json: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE solo_trips SET trip = ? WHERE id = ?", (trip_json, trip_id))

    def delete_solo(self, trip_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM plan_versions WHERE owner_kind='solo' AND owner_id = ?", (trip_id,))
            self._db.execute("DELETE FROM shares WHERE owner_kind='solo' AND owner_id = ?", (trip_id,))
            self._db.execute("DELETE FROM solo_trips WHERE id = ?", (trip_id,))

    def delete_group(self, gid: str) -> None:
        """Remove a group and everything attached to it."""
        with self._lock, self._db:
            mids = [r[0] for r in self._db.execute("SELECT id FROM members WHERE group_id = ?", (gid,)).fetchall()]
            for mid in mids:
                self._db.execute("DELETE FROM inputs WHERE member_id = ?", (mid,))
            for table in ("questions", "members"):
                self._db.execute(f"DELETE FROM {table} WHERE group_id = ?", (gid,))
            self._db.execute("DELETE FROM plan_versions WHERE owner_kind='group' AND owner_id = ?", (gid,))
            self._db.execute("DELETE FROM shares WHERE owner_kind='group' AND owner_id = ?", (gid,))
            self._db.execute("DELETE FROM groups WHERE id = ?", (gid,))

    # --- plan versions (immutable snapshots; only the status and lock flags change) --

    @staticmethod
    def _ver(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        for k in ("snapshot", "places", "evidence", "diff"):
            row[k] = json.loads(row[k]) if row.get(k) else None
        return row

    def add_version(self, owner_kind: str, owner_id: str, kind: str, status: str, snapshot: dict[str, Any], *,
                    plan: str | None = None, places: dict | None = None, evidence: dict | None = None,
                    diff: dict | None = None, explanation: str = "", restored_from: int | None = None) -> int:
        with self._lock, self._db:
            seq = self._db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM plan_versions WHERE owner_kind=? AND owner_id=?",
                                   (owner_kind, owner_id)).fetchone()[0]
            if status == "accepted":
                self._db.execute("UPDATE plan_versions SET status='superseded' WHERE owner_kind=? AND owner_id=? "
                                 "AND status='accepted'", (owner_kind, owner_id))
            if status == "proposal":
                self._db.execute("UPDATE plan_versions SET status='superseded' WHERE owner_kind=? AND owner_id=? "
                                 "AND status='proposal'", (owner_kind, owner_id))
            self._db.execute(
                "INSERT INTO plan_versions(id,owner_kind,owner_id,seq,kind,status,snapshot,plan,places,evidence,diff,"
                "explanation,restored_from,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_new_id(), owner_kind, owner_id, seq, kind, status, json.dumps(snapshot), plan,
                 json.dumps(places) if places is not None else None, json.dumps(evidence) if evidence is not None else None,
                 json.dumps(diff) if diff is not None else None, explanation, restored_from, _now()))
        return seq

    def set_status(self, owner_kind: str, owner_id: str, seq: int, status: str) -> None:
        with self._lock, self._db:
            if status == "accepted":
                self._db.execute("UPDATE plan_versions SET status='superseded' WHERE owner_kind=? AND owner_id=? "
                                 "AND status='accepted'", (owner_kind, owner_id))
            self._db.execute("UPDATE plan_versions SET status=? WHERE owner_kind=? AND owner_id=? AND seq=?",
                             (status, owner_kind, owner_id, seq))

    def update_version(self, owner_kind: str, owner_id: str, seq: int, *, snapshot: dict | None = None,
                       plan: str | None = None) -> None:
        """Lock flags are the only in-place edit; everything else about a version is immutable."""
        with self._lock, self._db:
            if snapshot is not None:
                self._db.execute("UPDATE plan_versions SET snapshot=? WHERE owner_kind=? AND owner_id=? AND seq=?",
                                 (json.dumps(snapshot), owner_kind, owner_id, seq))
            if plan is not None:
                self._db.execute("UPDATE plan_versions SET plan=? WHERE owner_kind=? AND owner_id=? AND seq=?",
                                 (plan, owner_kind, owner_id, seq))

    def get_version(self, owner_kind: str, owner_id: str, seq: int) -> dict[str, Any] | None:
        return self._ver(self._one("SELECT * FROM plan_versions WHERE owner_kind=? AND owner_id=? AND seq=?",
                                   (owner_kind, owner_id, seq)))

    def latest_version(self, owner_kind: str, owner_id: str, status: str) -> dict[str, Any] | None:
        return self._ver(self._one("SELECT * FROM plan_versions WHERE owner_kind=? AND owner_id=? AND status=? "
                                   "ORDER BY seq DESC LIMIT 1", (owner_kind, owner_id, status)))

    def list_versions(self, owner_kind: str, owner_id: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT seq,kind,status,snapshot,diff,restored_from,created_at FROM plan_versions "
                         "WHERE owner_kind=? AND owner_id=? ORDER BY seq DESC", (owner_kind, owner_id))
        return [self._ver(r) for r in rows]  # type: ignore[misc]

    # --- share links (read-only, revocable; one active link per trip) -----------------

    def create_share(self, owner_kind: str, owner_id: str) -> str:
        token = new_token()
        with self._lock, self._db:
            self._db.execute("DELETE FROM shares WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id))
            self._db.execute("INSERT INTO shares(token,owner_kind,owner_id,created_at) VALUES(?,?,?,?)",
                             (token, owner_kind, owner_id, _now()))
        return token

    def revoke_share(self, owner_kind: str, owner_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM shares WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id))

    def share_for(self, owner_kind: str, owner_id: str) -> str | None:
        row = self._one("SELECT token FROM shares WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id))
        return row["token"] if row else None

    def share_by_token(self, token: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM shares WHERE token = ?", (token,))
