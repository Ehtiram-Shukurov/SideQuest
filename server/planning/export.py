"""Exports and the public (share) view: Google Maps directions links, iCalendar, redaction.

Nothing here talks to a model or the network. A shared view never contains the owner's start
location, budget, cost totals, validation issues or the model's explanation text (the model may
have mentioned private details). Directions links in shared and group views skip every leg that
touches the start/meeting point, so no home coordinates leak through a URL."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from server.models import Evidence, Place, Plan

_MODE = {"walk": "walking", "bike": "bicycling", "car": "driving"}
NOT_A_BOOKING = "A SideQuest suggestion, not a booking."
SHARED_NOTE = "Shared read-only view. Budgets, start locations and personal details are not included."


def cost_text(costs) -> str:
    if not costs or costs[0].unknown:
        return "price unknown"
    c = costs[0]
    if c.max_minor == 0:
        return "free"
    lo, hi = c.min_minor / 100, c.max_minor / 100
    return f"${lo:.0f}" if lo == hi else f"${lo:.0f}-${hi:.0f}"


def collect_facts(plan: Plan, details: Mapping[str, Place], evidence: Mapping[str, Evidence]):
    """JSON-ready places and evidence behind a plan, so a stored version can be re-validated later."""
    pids = {b.place_id for b in plan.blocks if b.place_id in details}
    eids = {e for pid in pids for e in details[pid].evidence_ids} | {
        c.evidence_id for b in plan.blocks for c in b.costs if c.evidence_id}
    return ({pid: details[pid].model_dump(mode="json") for pid in pids},
            {e: evidence[e].model_dump(mode="json") for e in eids if e in evidence})


# --- Google Maps directions ----------------------------------------------------------------

def directions_links(blocks: list[dict], legs: list[dict], origin: dict | None = None) -> list[dict[str, str]]:
    """One link per leg. Legs touching the start/end are included only when `origin` is given."""
    coords = {b["place_id"]: (b["lat"], b["lon"]) for b in blocks if b.get("place_id") and b.get("lat") is not None}
    names = {b["place_id"]: b["name"] for b in blocks if b.get("place_id")}
    if origin:
        coords["here"], names["here"] = (origin["lat"], origin["lon"]), "your start"
    out = []
    for leg in legs:
        a, b = coords.get(leg.get("from")), coords.get(leg.get("to"))
        if not a or not b:
            continue
        q = urlencode({"api": 1, "origin": f"{a[0]:.6f},{a[1]:.6f}", "destination": f"{b[0]:.6f},{b[1]:.6f}",
                       "travelmode": _MODE.get(leg.get("mode"), "walking")})
        out.append({"label": f"{names[leg['from']]} \u2192 {names[leg['to']]}",
                    "url": "https://www.google.com/maps/dir/?" + q})
    return out


# --- iCalendar -------------------------------------------------------------------------------

def _esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def _fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets (continuation lines start with one space)."""
    parts, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (73 if parts else 74):
            parts.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    parts.append(cur)
    return "\r\n ".join(parts)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def ics_for_plan(plan: Plan, places: Mapping[str, Place], title: str, now: datetime) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SideQuest//Plan//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:" + _esc(title)]
    for b in plan.blocks:
        p = places.get(b.place_id)
        lines += ["BEGIN:VEVENT", f"UID:{plan.id}-{b.id}@sidequest", f"DTSTAMP:{_stamp(now)}",
                  f"DTSTART:{_stamp(b.start)}", f"DTEND:{_stamp(b.end)}", "SUMMARY:" + _esc(b.name),
                  "DESCRIPTION:" + _esc(f"{cost_text(b.costs)}. {NOT_A_BOOKING}")]
        if p is not None:
            lines += ["LOCATION:" + _esc(p.name), f"GEO:{p.lat:.6f};{p.lon:.6f}"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


# --- public (share) view ---------------------------------------------------------------------

def public_view(base: Mapping[str, Any], *, title: str, synthetic: bool) -> dict[str, Any]:
    """Read-only view built from a stored plan JSON. Whitelist only: anything new stays private."""
    blocks = [{"name": b["name"], "start": b["start"], "end": b["end"], "lat": b.get("lat"), "lon": b.get("lon"),
               "cost": b.get("cost"), "facts": [{"field": f["field"], "status": f["status"]} for f in b.get("facts", [])],
               "step_free": b.get("step_free")} for b in base["blocks"]]
    return {
        "title": title, "date": base.get("date"), "synthetic": bool(synthetic),
        "window": f"{blocks[0]['start']}-{blocks[-1]['end']}" if blocks else "", "blocks": blocks,
        "directions": directions_links(base["blocks"], base.get("legs", []), None),
        "confidence": base.get("confidence", "unassessed"),
        "verify": [{"name": v["name"], "field": v["field"], "url": v.get("url")} for v in base.get("verify", [])],
        "notes": base.get("notes", []), "shared_note": SHARED_NOTE}


def version_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    """A short, private-field-free description of a stored version for history lists."""
    snap = row.get("snapshot") or {}
    prop = snap.get("proposal") or snap.get("base") or {}
    return {"seq": row["seq"], "kind": row["kind"], "status": row["status"], "created_at": row["created_at"],
            "stops": [b["name"] for b in prop.get("blocks", [])], "overall": prop.get("overall"),
            "confidence": prop.get("confidence"), "change": (row.get("diff") or {}).get("summary"),
            "restored_from": row.get("restored_from"), "current": row["status"] == "accepted"}


def ics_from_version(row: Mapping[str, Any], title: str, now: datetime) -> str:
    plan = Plan.model_validate_json(row["plan"])
    places = {pid: Place.model_validate(d) for pid, d in (row.get("places") or {}).items()}
    return ics_for_plan(plan, places, title, now)
