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
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)

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

    def list_questions(self, gid: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM questions WHERE group_id = ? ORDER BY created_at, id", (gid,))

    def get_question(self, gid: str, qid: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM questions WHERE id = ? AND group_id = ?", (qid, gid))

    def answer_question(self, gid: str, qid: str, status: str, alt: tuple[str, str] | None = None) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE questions SET status = ?, alt_start = ?, alt_end = ?, answered_at = ? "
                             "WHERE id = ? AND group_id = ?", (status, alt[0] if alt else None, alt[1] if alt else None,
                                                               _now(), qid, gid))
