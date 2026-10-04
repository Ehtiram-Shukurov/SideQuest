"""MVP web API.

    uv run uvicorn server.api.app:app --port 8000

A trip is a server-side session: the tool context (research caches, assembled plans), the model
conversation when it is paused on a question, the current saved plan, and at most one pending
replan proposal. One agent run at a time (free-tier friendly).

The Gemini key stays server-side. Device location is sent by the browser with the plan request
(location is REQUIRED); live tracking during the quest happens in the browser and is not sent
to the server. Solo trips are owned by a bearer token (stored hashed); their plan versions,
accepted pointer, history and share links persist in SQLite and survive a restart. A paused model
conversation and an unanswered replan proposal do not survive a restart.
"""
from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from server.agent.config import load_dotenv, provider_from_env
from server.agent.model import ModelError, ModelProvider, ToolResult
from server.agent.runner import RunLimits, run_agent, trip_snapshot
from server.api.security import Hardening, install_log_redaction
from server.groups.routes import register_groups
from server.groups.store import Store
from server.models import Evidence, Location, Member, Place, Plan, TimeWindow, Trip, local
from server.planning.changes import (
    diff_plans,
    extend_window,
    running_late,
    set_budget,
    set_lock,
    set_minutes,
)
from server.planning.export import (
    collect_facts,
    directions_links,
    ics_from_version,
    public_view,
    version_summary,
)
from server.planning.validators import validate_plan
from server.providers.base import PlaceSummary
from server.providers.live import LiveWorld
from server.providers.routing import FossgisRouting, RouteCache
from server.providers.synthetic import SyntheticWorld
from server.tools.context import Proposal, ToolContext

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
LIMITS = RunLimits(max_turns=24, max_tool_calls=40, max_validations=6, max_seconds=300.0)


class PlanRequest(BaseModel):
    request: str = Field(min_length=3, max_length=500)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    tz: str = "UTC"
    minutes: int = Field(default=120, ge=30, le=600)
    budget_dollars: float | None = Field(default=None, ge=0, le=100000)
    no_budget_limit: bool = False
    mode: Literal["walk", "bike", "car"] = "walk"
    data: Literal["live", "demo"] = "live"
    avoid_rain_pct: int | None = Field(default=None, ge=0, le=100)  # reject outdoor stops above this chance


class AnswerRequest(BaseModel):
    choice: int | None = Field(default=None, ge=0, le=20)
    text: str | None = Field(default=None, min_length=1, max_length=300)


class Change(BaseModel):
    type: Literal["lock", "unlock", "remove_stop", "swap_stop", "set_minutes", "set_budget", "running_late"]
    block_id: str | None = Field(default=None, max_length=80)
    minutes: int | None = Field(default=None, ge=5, le=600)
    budget_dollars: float | None = Field(default=None, ge=0, le=100000)
    no_budget_limit: bool = False


class ReplanRequest(BaseModel):
    changes: list[Change] = Field(min_length=1, max_length=10)


class RestoreRequest(BaseModel):
    seq: int = Field(ge=1)


class ShareToggle(BaseModel):
    enabled: bool


class DecisionRequest(BaseModel):
    decision: Literal["accept", "reject"]


def build_trip(req: PlanRequest, now: datetime) -> Trip:
    try:
        ZoneInfo(req.tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, f"unknown time zone {req.tz!r}") from None
    start = now.replace(second=0, microsecond=0)
    end = start + timedelta(minutes=req.minutes)
    here = Location(id="here", name="Your location", lat=req.lat, lon=req.lon, timezone=req.tz)
    cap = None if req.budget_dollars is None else int(round(req.budget_dollars * 100))
    me = Member(id="m1", display_name="You", availability=(TimeWindow(start=start, end=end),),
                budget_cap_minor=cap, budget_uncapped=req.no_budget_limit, transport_modes=(req.mode,),
                has_car=True if req.mode == "car" else None, car_capacity=4 if req.mode == "car" else None,
                avoid_rain_above=None if req.avoid_rain_pct is None else req.avoid_rain_pct / 100)
    return Trip(id=f"trip-{uuid.uuid4().hex[:8]}", owner_id="m1", mode="solo", title="Quick outing", timezone=req.tz,
                currency="USD", origin=here, endpoint=here, window_start=start, window_end=end, members=(me,))


# --- session state -----------------------------------------------------------------

@dataclass
class TripSession:
    id: str
    req: PlanRequest
    ctx: ToolContext
    provider: ModelProvider | None
    current: Proposal | None = None  # the saved plan the user is working with
    current_seq: int | None = None  # its version number in the store
    candidate_seq: int | None = None
    candidate: Proposal | None = None  # a replan proposal waiting for accept / reject
    checkpoint: tuple[Trip, set[str]] | None = None  # trip + exclusions before a replan
    model_session: Any = None  # paused model conversation (only while a question is open)
    pending_results: list[ToolResult] = field(default_factory=list)
    pending_kind: str = "plan"
    pending_run_id: str | None = None
    choices: list[dict[str, Any]] = field(default_factory=list)


class Run:
    def __init__(self, trip_id: str, kind: str) -> None:
        self.trip_id = trip_id
        self.kind = kind
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.result: dict[str, Any] | None = None
        self.error: str | None = None


# --- JSON shaping ------------------------------------------------------------------

def _cost_text(costs) -> str:
    if not costs or costs[0].unknown:
        return "price unknown"
    c = costs[0]
    if c.max_minor == 0:
        return "free"
    lo, hi = c.min_minor / 100, c.max_minor / 100
    return f"${lo:.0f}" if lo == hi else f"${lo:.0f}-${hi:.0f}"


def _constraints_json(trip: Trip) -> dict[str, Any]:
    m = trip.travellers()[0]
    tz = trip.timezone
    return {
        "minutes": int((trip.window_end - trip.window_start).total_seconds() // 60),
        "window": f"{local(trip.window_start, tz):%H:%M}-{local(trip.window_end, tz):%H:%M}",
        "mode": (m.transport_modes or ("walk",))[0],
        "budget_dollars": None if m.budget_cap_minor is None else m.budget_cap_minor / 100,
        "no_budget_limit": m.budget_uncapped,
        "avoid_rain_pct": None if m.avoid_rain_above is None else round(m.avoid_rain_above * 100)}


def _weather_json(ctx: ToolContext) -> dict[str, Any] | None:
    if not ctx.forecast_retrieved:
        return None
    if not ctx.forecast:
        return {"available": False}
    probs = [f.precip_probability for f in ctx.forecast]
    return {"available": True, "max_pct": round(max(probs) * 100), "min_pct": round(min(probs) * 100)}


def route_notes(plan: Plan, synthetic: bool) -> list[str]:
    """Plan notes that say where this plan's route times actually came from (and give attribution)."""
    if synthetic:
        return ["DEMO DATA: venues, prices and forecasts are invented."]
    ids = [e for leg in plan.legs for e in leg.evidence_ids]
    osm, est = any("ev-route-osm" in e for e in ids), any("ev-route-est" in e for e in ids)
    if osm and not est:
        route = ("Route times come from OpenStreetMap routing (FOSSGIS service): typical speeds, no live traffic. "
                 "Routing data \u00a9 OpenStreetMap contributors.")
    elif osm:
        route = ("Some route times come from OpenStreetMap routing; the others are straight-line estimates. "
                 "Routing data \u00a9 OpenStreetMap contributors.")
    else:
        route = "Route times are straight-line estimates, not a routing engine."
    return [route, "Hours and prices come from community map data and may be missing or out of date.",
            "A saved plan is a suggestion, not a booking."]


def proposal_json(prop: Proposal, ctx: ToolContext) -> dict[str, Any]:
    tz = ctx.trip.timezone
    plan = prop.plan
    rep = plan.validation
    assert rep is not None
    blocks = []
    for b in plan.blocks:
        p = ctx.details.get(b.place_id)
        facts = []
        if p is not None:
            for eid in p.evidence_ids:
                e = ctx.evidence.get(eid)
                if e is not None:
                    facts.append({"field": e.field, "status": e.status, "url": e.url})
        blocks.append({
            "id": b.id, "place_id": b.place_id, "name": b.name, "start": f"{local(b.start, tz):%H:%M}",
            "end": f"{local(b.end, tz):%H:%M}", "lat": p.lat if p else None, "lon": p.lon if p else None,
            "cost": _cost_text(b.costs), "shortened": b.shortened, "locked": b.locked,
            "facts": facts, "step_free": None if p is None else p.step_free,
            "categories": list(p.categories) if p else []})
    legs = [{"from": l.from_id, "to": l.to_id, "mode": l.mode, "depart": f"{local(l.depart, tz):%H:%M}",
             "latest_arrival": f"{local(l.arrive_latest, tz):%H:%M}"} for l in plan.legs]
    issues = [{"status": c.status, "code": c.code, "message": c.message} for c in rep.checks if c.status != "pass"]
    tot = rep.per_person_totals.get(ctx.trip.owner_id)
    totals = None if tot is None else {
        "low": tot.low_minor / 100, "high": tot.high_minor / 100,
        "cap": None if tot.cap_minor is None else tot.cap_minor / 100, "has_unknown": tot.has_unknown}
    notes = route_notes(plan, bool(ctx.places.synthetic))
    return {"id": prop.id, "state": plan.state, "overall": rep.overall, "explanation": prop.explanation,
            "blocks": blocks, "legs": legs, "issues": issues, "totals": totals, "confidence": rep.confidence,
            "verify": [{"block_id": v.block_id, "name": v.name, "field": v.field, "source": v.source, "url": v.url}
                       for v in rep.verify],
            "checks_passed": sum(1 for c in rep.checks if c.status == "pass"), "notes": notes,
            "date": f"{local(plan.blocks[0].start, tz):%a %b %d}" if plan.blocks else None}


def build_result(sess: TripSession, *, status: str, message: str = "", proposal: Proposal | None = None,
                 clarification: dict[str, Any] | None = None, conflict: dict[str, Any] | None = None,
                 tool_calls: int = 0, tokens: int | None = None, diff: dict[str, Any] | None = None,
                 awaiting_decision: bool = False) -> dict[str, Any]:
    ctx = sess.ctx
    clar = None
    if clarification:
        clar = {"question": clarification.get("question", ""), "allow_text": True,
                "choices": [{"index": i, "label": c["label"], "kind": c["type"]}
                            for i, c in enumerate(sess.choices)]}
    prop = proposal_json(proposal, ctx) if proposal else None
    if prop:  # the owner's own links may start and end at their location
        prop["directions"] = directions_links(prop["blocks"], prop["legs"], {"lat": ctx.trip.origin.lat, "lon": ctx.trip.origin.lon})
    return {
        "status": status, "message": message, "trip_id": sess.id, "clarification": clar,
        "conflict": conflict, "tool_calls": tool_calls, "tokens": tokens, "diff": diff,
        "awaiting_decision": awaiting_decision, "constraints": _constraints_json(ctx.trip),
        "weather": _weather_json(ctx),
        "data": {"places": ctx.places.name, "synthetic": bool(ctx.places.synthetic)},
        "origin": {"lat": ctx.trip.origin.lat, "lon": ctx.trip.origin.lon},
        "proposal": prop}


def build_choices(ctx: ToolContext, clarification: dict[str, Any] | None,
                  conflict: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Clickable answers. Relaxations from a real conflict become STRUCTURED changes; the model's
    own suggested options are kept as plain-text answers."""
    out: list[dict[str, Any]] = []
    for r in (conflict or {}).get("relaxations", []):
        if r.get("type") == "extend_window":
            m = int(r["minutes"])
            out.append({"type": "extend_window", "minutes": m, "label": f"Extend the time window by {m} minutes"})
        elif r.get("type") == "drop_stop":
            pid = r["place_id"]
            p = ctx.details.get(pid) or ctx.candidates.get(pid)
            out.append({"type": "drop_stop", "place_id": pid, "label": f"Skip {p.name if p else pid}"})
    seen = {c["label"].lower() for c in out}
    for o in (clarification or {}).get("options", []):
        if o.strip() and o.strip().lower() not in seen:
            out.append({"type": "text", "text": o.strip(), "label": o.strip()})
            seen.add(o.strip().lower())
    return out


def apply_change(ctx: ToolContext, change: dict[str, Any]) -> str | None:
    """Apply a structured answer to the trip. Returns a note for the model, or None for text."""
    t = change["type"]
    if t == "extend_window":
        ctx.trip = extend_window(ctx.trip, int(change["minutes"]))
        end = local(ctx.trip.window_end, ctx.trip.timezone)
        return f"Time window extended by {change['minutes']} minutes; it now ends at {end:%H:%M}."
    if t == "drop_stop":
        ctx.excluded.add(change["place_id"])
        p = ctx.details.get(change["place_id"]) or ctx.candidates.get(change["place_id"])
        return f"The user chose to skip {p.name if p else change['place_id']}; it can no longer be used."
    return None


def _block_view(sess: TripSession) -> list[dict[str, Any]]:
    tz = sess.ctx.trip.timezone
    if sess.current is None:
        return []
    return [{"id": b.id, "place_id": b.place_id, "name": b.name, "start": f"{local(b.start, tz):%H:%M}",
             "end": f"{local(b.end, tz):%H:%M}", "locked": b.locked} for b in sess.current.plan.blocks]


def create_app(provider_factory: Callable[[], ModelProvider] = provider_from_env,
               now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
               db_path: str = ":memory:") -> FastAPI:
    load_dotenv()
    app = FastAPI(title="SideQuest MVP")
    app.add_middleware(Hardening)  # headers + CSP, rate limits, size limits (no CORS on purpose)
    install_log_redaction()  # tokens in URLs never reach the access log
    runs: dict[str, Run] = {}
    sessions: dict[str, TripSession] = {}
    busy = threading.Lock()  # one agent run at a time (solo and group share it)
    store = Store(db_path)
    # Real routes from the free FOSSGIS service (1 request/s limit, cached). SIDEQUEST_ROUTING=off disables it.
    routing = None if os.environ.get("SIDEQUEST_ROUTING", "on").lower() == "off" else FossgisRouting(cache=RouteCache(db_path))
    register_groups(app, store=store, routing=routing, provider_factory=provider_factory, now_fn=now_fn, busy=busy,
                    limits=LIMITS, proposal_json=proposal_json)

    def make_world(req: PlanRequest, trip: Trip, now: datetime) -> Any:
        return SyntheticWorld(trip, now=now) if req.data == "demo" else LiveWorld(
            lat=req.lat, lon=req.lon, tz=req.tz, origin_id="here", now=now, routing=routing)

    def rehydrate(trip_id: str) -> TripSession | None:
        """Rebuild a session from the store after a restart (or eviction). The accepted plan, its
        facts and its locks come back; the paused model conversation and a pending proposal do not."""
        row = store.get_solo(trip_id)
        if row is None:
            return None
        req, trip, now = PlanRequest.model_validate_json(row["req"]), Trip.model_validate_json(row["trip"]), now_fn()
        world = make_world(req, trip, now)
        ctx = ToolContext(trip=trip, places=world, routes=world, weather=world, now=now)
        sess = TripSession(trip_id, req, ctx, None)
        acc = store.latest_version("solo", trip_id, "accepted")
        if acc and acc["plan"]:
            plan = Plan.model_validate_json(acc["plan"])
            places = {pid: Place.model_validate(d) for pid, d in (acc["places"] or {}).items()}
            ctx.details.update(places)
            ctx.evidence.update({eid: Evidence.model_validate(d) for eid, d in (acc["evidence"] or {}).items()})
            ctx.candidates.update({pid: PlaceSummary(pid, p.name, p.categories, p.lat, p.lon, "stored plan",
                                                     bool(world.synthetic)) for pid, p in places.items()})
            if isinstance(world, LiveWorld):
                world.register_points({pid: (p.lat, p.lon) for pid, p in places.items()})
            sess.current = Proposal(id=f"prop-v{acc['seq']}", plan=plan, explanation=acc["explanation"], evidence_ids=())
            sess.current_seq, ctx.base_plan = acc["seq"], plan
        pending = store.latest_version("solo", trip_id, "proposal")
        if pending:  # its constraint changes were never saved, so it cannot be resumed safely
            store.set_status("solo", trip_id, pending["seq"], "superseded")
        sessions[trip_id] = sess
        return sess

    def get_session(trip_id: str) -> TripSession:
        sess = sessions.get(trip_id) or rehydrate(trip_id)
        if sess is None:
            raise HTTPException(404, "unknown trip")
        return sess

    def solo_auth(trip_id: str, authorization: str | None) -> TripSession:
        if store.get_solo(trip_id) is None:
            raise HTTPException(404, "unknown trip")
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not token or store.solo_by_token(trip_id, token) is None:
            raise HTTPException(401, "this trip needs its owner token")
        return get_session(trip_id)

    def record_version(sess: TripSession, kind: str, status: str, proposal: Proposal, *,
                       diff: dict[str, Any] | None = None, restored_from: int | None = None) -> int:
        snap = build_result(sess, status="proposal_saved", proposal=proposal)
        places, evid = collect_facts(proposal.plan, sess.ctx.details, sess.ctx.evidence)
        return store.add_version("solo", sess.id, kind, status, snap, plan=proposal.plan.model_dump_json(),
                                 places=places, evidence=evid, diff=diff, explanation=proposal.explanation,
                                 restored_from=restored_from)

    def save_trip(sess: TripSession) -> None:
        store.update_solo_trip(sess.id, sess.ctx.trip.model_dump_json())

    def restore(sess: TripSession) -> None:
        if sess.checkpoint:
            sess.ctx.trip, sess.ctx.excluded = sess.checkpoint[0], set(sess.checkpoint[1])
        sess.checkpoint = None
        sess.ctx.base_plan = sess.current.plan if sess.current else None

    def launch(sess: TripSession, kind: str, *, opening: str | None = None,
               tool_results: list[ToolResult] | None = None, resume: Any = None) -> str:
        """Start a background run. The caller holds `busy`; the worker releases it."""
        run_id = uuid.uuid4().hex[:12]
        run = Run(sess.id, kind)
        runs[run_id] = run
        for old in list(runs)[:-40]:
            runs.pop(old, None)
        ctx = sess.ctx
        ctx.validations_run, ctx.clarification, ctx.last_conflict = 0, None, None

        def work() -> None:
            try:
                res = run_agent(provider=sess.provider, ctx=ctx, request=sess.req.request, limits=LIMITS,
                                on_event=lambda e: run.events.append({"seq": e.seq, "kind": e.kind, "summary": e.summary}),
                                session=resume, opening=opening, tool_results=tool_results)
                sess.model_session, sess.pending_results, sess.pending_run_id, sess.choices = None, [], None, []
                if res.status == "needs_clarification":
                    sess.model_session, sess.pending_results = res.session, res.pending_results
                    sess.pending_kind, sess.pending_run_id = kind, run_id
                    sess.choices = build_choices(ctx, res.clarification, res.conflict)
                diff, awaiting = None, False
                if res.proposal and kind == "replan":
                    sess.candidate, awaiting = res.proposal, True
                    if sess.current:
                        diff = diff_plans(sess.current.plan, res.proposal.plan, ctx.trip.timezone)
                    sess.candidate_seq = record_version(sess, "replan", "proposal", res.proposal, diff=diff)
                elif res.proposal:
                    sess.current, sess.checkpoint = res.proposal, None
                    ctx.base_plan = res.proposal.plan
                    sess.current_seq = record_version(sess, "plan", "accepted", res.proposal)
                    save_trip(sess)
                elif kind == "replan" and res.status != "needs_clarification":
                    restore(sess)  # the replan produced nothing: keep the saved plan and its constraints
                run.result = build_result(sess, status=res.status, message=res.message, proposal=res.proposal,
                                          clarification=res.clarification, conflict=res.conflict,
                                          tool_calls=res.tool_calls, tokens=res.usage.get("total_tokens"),
                                          diff=diff, awaiting_decision=awaiting)
            except Exception as exc:  # surfaced to the user; never includes credentials
                run.error = f"{type(exc).__name__}: {str(exc)[:200]}"
                if kind == "replan":
                    restore(sess)
            finally:
                busy.release()  # release first: a client that sees 'done' may start the next run at once
                run.done = True

        threading.Thread(target=work, daemon=True).start()
        return run_id

    def render_current(sess: TripSession) -> dict[str, Any]:
        assert sess.current is not None
        return build_result(sess, status="proposal_saved", proposal=sess.current)

    # --- endpoints ---------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        try:
            provider_factory()
            model_ok = True
        except ModelError:
            model_ok = False
        return {"ok": True, "model_configured": model_ok}

    @app.post("/api/plans")
    def create_plan(req: PlanRequest):
        try:
            provider = provider_factory()
        except ModelError as exc:
            return JSONResponse({"detail": f"Model not configured: {exc}"}, status_code=503)
        now = now_fn()
        trip = build_trip(req, now)
        if not busy.acquire(blocking=False):
            return JSONResponse({"detail": "A plan is already running; wait for it to finish."}, status_code=429)
        try:
            world = make_world(req, trip, now)
            ctx = ToolContext(trip=trip, places=world, routes=world, weather=world, now=now)
            sess = TripSession(trip.id, req, ctx, provider)
            sessions[trip.id] = sess
            for old in list(sessions)[:-20]:
                sessions.pop(old, None)
            owner_token = store.create_solo(trip.id, req.model_dump_json(), trip.model_dump_json())
            run_id = launch(sess, "plan")
        except Exception:
            busy.release()
            raise
        return {"run_id": run_id, "trip_id": trip.id, "owner_token": owner_token}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        solo_auth(run.trip_id, authorization)
        return {"done": run.done, "events": run.events, "result": run.result, "error": run.error,
                "trip_id": run.trip_id, "kind": run.kind}

    @app.get("/api/runs/{run_id}/stream")
    def stream_run(run_id: str, authorization: str | None = Header(default=None)) -> StreamingResponse:
        """Live progress as server-sent events. Clients without streaming fall back to polling the run."""
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        solo_auth(run.trip_id, authorization)

        def events():
            sent, ticks, deadline = 0, 0, time.monotonic() + 900
            while time.monotonic() < deadline:
                for e in run.events[sent:]:
                    yield f"id: {e['seq']}\nevent: progress\ndata: {json.dumps(e)}\n\n"
                    sent += 1
                if run.done:
                    yield f"event: done\ndata: {json.dumps({'error': run.error})}\n\n"
                    return
                ticks += 1
                if ticks % 40 == 0:
                    yield ": keepalive\n\n"
                time.sleep(0.25)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/runs/{run_id}/answer")
    def answer(run_id: str, body: AnswerRequest, authorization: str | None = Header(default=None)):
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        sess = solo_auth(run.trip_id, authorization)
        if not run.done or sess.pending_run_id != run_id:
            raise HTTPException(409, "that question is no longer open")
        if body.choice is None and not body.text:
            raise HTTPException(422, "pick one of the options or type an answer")
        if body.choice is not None and body.choice >= len(sess.choices):
            raise HTTPException(422, "unknown option")
        if not busy.acquire(blocking=False):
            return JSONResponse({"detail": "A plan is already running; wait for it to finish."}, status_code=429)
        try:
            change = sess.choices[body.choice] if body.choice is not None else {"type": "text", "text": body.text}
            try:
                note = apply_change(sess.ctx, change)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            payload = {"answered": True, "answer": change.get("label") or change.get("text"),
                       "applied": note, "next": "constraints may have changed: call assemble_plan "
                                                "and validate_plan again"}
            results = [ToolResult(r.call_id, r.name, json.dumps(payload)) if r.name == "ask_user" else r
                       for r in sess.pending_results]
            model_session = sess.model_session
            if model_session is None or not any(r.name == "ask_user" for r in results):
                raise HTTPException(409, "the planner conversation was lost; start a new plan")
            new_id = launch(sess, sess.pending_kind, resume=model_session, tool_results=results)
        except Exception:
            busy.release()
            raise
        return {"run_id": new_id, "trip_id": sess.id}

    @app.get("/api/trips/{trip_id}")
    def get_trip(trip_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        sess = solo_auth(trip_id, authorization)
        return {"result": render_current(sess) if sess.current else None,
                "pending_replan": sess.candidate is not None, "share_token": store.share_for("solo", trip_id)}

    @app.post("/api/trips/{trip_id}/replan")
    def replan(trip_id: str, body: ReplanRequest, authorization: str | None = Header(default=None)):
        sess = solo_auth(trip_id, authorization)
        if sess.current is None:
            raise HTTPException(409, "there is no saved plan to change yet")
        if sess.candidate is not None:
            raise HTTPException(409, "accept or reject the pending change first")
        if sess.pending_run_id is not None:
            raise HTTPException(409, "answer the open question first")
        ctx = sess.ctx
        plan = sess.current.plan
        known = {b.id for b in plan.blocks}
        for c in body.changes:  # validate everything before touching state
            if c.type in ("lock", "unlock", "remove_stop", "swap_stop") and c.block_id not in known:
                raise HTTPException(422, f"unknown stop {c.block_id!r}")
            if c.type in ("set_minutes", "running_late") and c.minutes is None:
                raise HTTPException(422, f"{c.type} needs minutes")
        # locks are instant edits: no agent run
        for c in body.changes:
            if c.type in ("lock", "unlock"):
                plan = set_lock(plan, c.block_id, c.type == "lock")
        sess.current = dataclasses.replace(sess.current, plan=plan)
        if sess.current_seq is not None and any(c.type in ("lock", "unlock") for c in body.changes):
            # lock flags are the one in-place edit of a stored version
            store.update_version("solo", sess.id, sess.current_seq, snapshot=render_current(sess),
                                 plan=plan.model_dump_json())
        rest = [c for c in body.changes if c.type not in ("lock", "unlock")]
        if not rest:
            ctx.base_plan = plan
            return {"immediate": True, "trip_id": sess.id, "result": render_current(sess)}
        try:
            provider = provider_factory()
        except ModelError as exc:
            return JSONResponse({"detail": f"Model not configured: {exc}"}, status_code=503)
        if not busy.acquire(blocking=False):
            return JSONResponse({"detail": "A plan is already running; wait for it to finish."}, status_code=429)
        try:
            sess.checkpoint = (ctx.trip, set(ctx.excluded))
            notes: list[str] = []
            for c in rest:
                try:
                    if c.type in ("remove_stop", "swap_stop"):
                        blk = next(b for b in plan.blocks if b.id == c.block_id)
                        plan = set_lock(plan, blk.id, False)  # a removed stop cannot stay an anchor
                        ctx.excluded.add(blk.place_id)
                        notes.append(f"{'Remove' if c.type == 'remove_stop' else 'Swap out'} {blk.name}"
                                     + ("; find a different place for that slot" if c.type == "swap_stop" else ""))
                    elif c.type == "set_minutes":
                        ctx.trip = set_minutes(ctx.trip, c.minutes)
                        notes.append(f"Total time is now {c.minutes} minutes")
                    elif c.type == "set_budget":
                        ctx.trip = set_budget(ctx.trip, c.budget_dollars, c.no_budget_limit)
                        notes.append("Budget: " + ("no limit" if c.no_budget_limit else
                                     "unknown" if c.budget_dollars is None else f"${c.budget_dollars:.0f}"))
                    elif c.type == "running_late":
                        ctx.trip = running_late(ctx.trip, now_fn(), c.minutes)
                        notes.append(f"The user is running late: starting about {c.minutes} minutes from now; "
                                     "the end time is unchanged")
                except ValueError as exc:
                    restore(sess)
                    raise HTTPException(422, str(exc)) from None
            sess.current = dataclasses.replace(sess.current, plan=plan)
            ctx.base_plan = plan
            opening = json.dumps({
                "request": sess.req.request, "trip": trip_snapshot(ctx.trip),
                "replan": {"changes": notes, "current_stops": _block_view(sess),
                           "locked_stop_ids": [b.id for b in plan.blocks if b.locked],
                           "excluded_place_ids": sorted(ctx.excluded)}}, ensure_ascii=False)
            sess.provider = provider
            run_id = launch(sess, "replan", opening=opening)
        except Exception:
            busy.release()
            raise
        return {"run_id": run_id, "trip_id": sess.id}

    @app.post("/api/trips/{trip_id}/decision")
    def decision(trip_id: str, body: DecisionRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        sess = solo_auth(trip_id, authorization)
        if sess.candidate is None:
            raise HTTPException(409, "there is no pending change to decide on")
        if body.decision == "accept":
            sess.current, sess.candidate, sess.checkpoint = sess.candidate, None, None
            sess.ctx.base_plan = sess.current.plan
            if sess.candidate_seq is not None:
                store.set_status("solo", sess.id, sess.candidate_seq, "accepted")
                sess.current_seq, sess.candidate_seq = sess.candidate_seq, None
            save_trip(sess)
        else:
            sess.candidate = None
            if sess.candidate_seq is not None:
                store.set_status("solo", sess.id, sess.candidate_seq, "rejected")
                sess.candidate_seq = None
            restore(sess)
        return {"decided": body.decision, "trip_id": sess.id, "result": render_current(sess)}

    # --- saved plans: history, restore, share, export, delete -------------------------

    @app.get("/api/trips/{trip_id}/history")
    def trip_history(trip_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        solo_auth(trip_id, authorization)
        return {"versions": [version_summary(r) for r in store.list_versions("solo", trip_id)]}

    @app.post("/api/trips/{trip_id}/restore")
    def restore_version(trip_id: str, body: RestoreRequest, authorization: str | None = Header(default=None)):
        """Re-validate an older version against today's constraints and, if it still holds, save it as
        a NEW version. History is never rewritten; a version that no longer fits is refused."""
        sess = solo_auth(trip_id, authorization)
        if sess.candidate is not None or sess.pending_run_id is not None:
            raise HTTPException(409, "decide on the pending change or answer the open question first")
        row = store.get_version("solo", trip_id, body.seq)
        if row is None or not row["plan"] or row["status"] == "rejected":
            raise HTTPException(404, "that version cannot be restored")
        ctx = sess.ctx
        plan = Plan.model_validate_json(row["plan"])
        places = {pid: Place.model_validate(d) for pid, d in (row["places"] or {}).items()}
        evid = {eid: Evidence.model_validate(d) for eid, d in (row["evidence"] or {}).items()}
        report = validate_plan(ctx.trip, plan, places, forecast=ctx.forecast, evidence=evid)
        if report.overall == "failed":
            return JSONResponse({"detail": "That version no longer fits your current constraints.",
                                 "issues": [{"code": c.code, "message": c.message} for c in report.checks
                                            if c.status == "fail"][:6]}, status_code=409)
        new = plan.model_copy(update={
            "id": f"restore-v{body.seq}-{uuid.uuid4().hex[:4]}", "state": "provisional" if report.overall == "provisional" else "ready",
            "validation": report, "base_constraints_version": ctx.trip.constraints_version})
        ctx.details.update(places)
        ctx.evidence.update(evid)
        prop = Proposal(id=f"prop-restore-v{body.seq}", plan=new, explanation=f"Restored from version {body.seq}.", evidence_ids=())
        sess.current, ctx.base_plan = prop, new
        sess.current_seq = record_version(sess, "restore", "accepted", prop, restored_from=body.seq)
        save_trip(sess)
        return {"trip_id": sess.id, "result": render_current(sess)}

    @app.post("/api/trips/{trip_id}/share")
    def trip_share(trip_id: str, body: ShareToggle, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        sess = solo_auth(trip_id, authorization)
        if not body.enabled:
            store.revoke_share("solo", trip_id)
            return {"share_token": None}
        if sess.current is None:
            raise HTTPException(409, "save a plan first")
        return {"share_token": store.share_for("solo", trip_id) or store.create_share("solo", trip_id)}

    @app.get("/api/trips/{trip_id}/export.ics")
    def trip_ics(trip_id: str, authorization: str | None = Header(default=None)) -> Response:
        solo_auth(trip_id, authorization)
        row = store.latest_version("solo", trip_id, "accepted")
        if row is None or not row["plan"]:
            raise HTTPException(404, "no saved plan to export")
        return Response(ics_from_version(row, "SideQuest plan", now_fn()), media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="sidequest-plan.ics"'})

    @app.delete("/api/trips/{trip_id}")
    def trip_delete(trip_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        solo_auth(trip_id, authorization)
        store.delete_solo(trip_id)
        sessions.pop(trip_id, None)
        return {"deleted": True}

    def shared_parts(token: str):
        sh = store.share_by_token(token)
        ver = store.latest_version(sh["owner_kind"], sh["owner_id"], "accepted") if sh else None
        if sh is None or ver is None:
            raise HTTPException(404, "this share link is invalid, revoked or has no plan yet")
        snap = ver["snapshot"]
        if sh["owner_kind"] == "solo":
            return ver, snap["proposal"], snap["data"]["synthetic"], "A SideQuest plan"
        g = store.get_group(sh["owner_id"])
        return ver, snap["base"], snap["data"]["synthetic"], (g["title"] if g else "A SideQuest group plan")

    @app.get("/api/shared/{token}")
    def shared_plan(token: str) -> dict[str, Any]:
        _, base, synthetic, title = shared_parts(token)
        return public_view(base, title=title, synthetic=synthetic)

    @app.get("/api/shared/{token}/export.ics")
    def shared_ics(token: str) -> Response:
        ver, _, _, title = shared_parts(token)
        return Response(ics_from_version(ver, title, now_fn()), media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="sidequest-plan.ics"'})

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    app.mount("/fixtures", StaticFiles(directory=Path(__file__).resolve().parents[2] / "fixtures"),
              name="fixtures")

    return app


app = create_app(db_path=os.environ.get("SIDEQUEST_DB", "data/sidequest.db"))
