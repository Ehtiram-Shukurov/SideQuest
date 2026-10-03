"""Structured changes to a trip and plan, plus plan diffs. Pure functions: no I/O, no model.

A user's edit ("extend the window", "I'm running late", "lock this stop") becomes one of these
calls, never just chat text, so the planner and validator see the new constraints."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from server.models import Plan, TimeWindow, Trip, local

MAX_WINDOW_MIN = 600
MIN_WINDOW_MIN = 30


def with_window(trip: Trip, start: datetime, end: datetime) -> Trip:
    """New trip window; travellers' availability follows it (solo trips). Bumps the version so
    any plan built under the old constraints is treated as stale."""
    if end <= start:
        raise ValueError("the window must end after it starts")
    if (end - start) > timedelta(minutes=MAX_WINDOW_MIN):
        raise ValueError(f"the window cannot exceed {MAX_WINDOW_MIN // 60} hours")
    if (end - start) < timedelta(minutes=MIN_WINDOW_MIN):
        raise ValueError(f"the window must be at least {MIN_WINDOW_MIN} minutes")
    members = tuple(m.model_copy(update={"availability": (TimeWindow(start=start, end=end),)})
                    if m.role != "viewer" else m for m in trip.members)
    return trip.model_copy(update={"window_start": start, "window_end": end, "members": members,
                                   "constraints_version": trip.constraints_version + 1})


def extend_window(trip: Trip, minutes: int) -> Trip:
    return with_window(trip, trip.window_start, trip.window_end + timedelta(minutes=minutes))


def set_minutes(trip: Trip, minutes: int) -> Trip:
    return with_window(trip, trip.window_start, trip.window_start + timedelta(minutes=minutes))


def running_late(trip: Trip, now: datetime, minutes: int) -> Trip:
    """The user starts `minutes` from now; the deadline does not move."""
    new_start = max(trip.window_start, now + timedelta(minutes=minutes))
    return with_window(trip, new_start, trip.window_end)


def set_budget(trip: Trip, dollars: float | None, uncapped: bool) -> Trip:
    cap = None if dollars is None else int(round(dollars * 100))
    members = tuple(m.model_copy(update={"budget_cap_minor": cap, "budget_uncapped": uncapped})
                    if m.role != "viewer" else m for m in trip.members)
    return trip.model_copy(update={"members": members, "constraints_version": trip.constraints_version + 1})


def set_lock(plan: Plan, block_id: str, locked: bool) -> Plan:
    if not any(b.id == block_id for b in plan.blocks):
        raise KeyError(block_id)
    blocks = tuple(b.model_copy(update={"locked": locked}) if b.id == block_id else b for b in plan.blocks)
    return plan.model_copy(update={"blocks": blocks})


def diff_plans(old: Plan, new: Plan, tz: str) -> dict[str, Any]:
    """Added / removed / moved / unchanged stops, matched by stable block id."""
    def span(b):
        return f"{local(b.start, tz):%H:%M}-{local(b.end, tz):%H:%M}"

    o, n = {b.id: b for b in old.blocks}, {b.id: b for b in new.blocks}
    out: dict[str, Any] = {"added": [], "removed": [], "moved": [], "unchanged": []}
    for bid, b in n.items():
        if bid not in o:
            out["added"].append({"id": bid, "name": b.name, "when": span(b)})
        elif (o[bid].start, o[bid].end) != (b.start, b.end):
            out["moved"].append({"id": bid, "name": b.name, "from": span(o[bid]), "to": span(b)})
        else:
            out["unchanged"].append({"id": bid, "name": b.name, "when": span(b)})
    for bid, b in o.items():
        if bid not in n:
            out["removed"].append({"id": bid, "name": b.name, "when": span(b)})
    bits = [f"{len(out[k])} {k}" for k in ("added", "removed", "moved", "unchanged") if out[k]]
    out["summary"] = ", ".join(bits) or "no changes"
    return out
