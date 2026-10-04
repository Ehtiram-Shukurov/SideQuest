"""Group planning endpoints.

Flow: the organizer creates a group and shares an invite link; participants join with a display
name and get their own bearer token (no accounts). Everyone saves their own availability and
preferences. "Plan for everyone" picks the window that fits the most people, builds a multi-member
trip and runs the existing agent. Anyone left out is asked whether they could make it; a "yes"
updates their availability and re-plans. The organizer accepts or rejects proposals; a proposal
built from older inputs is stale and cannot be accepted.

Privacy: budgets, access needs and dietary needs are visible only to their owner. Others see only
a within / over / unknown status. Roles come from the token on the server, never from the client.
Inputs, members, questions and plan versions (proposals, the accepted plan and its history) persist in
SQLite and survive a restart; the planner run in progress does not. Splitting the group into
parallel activities is NOT supported."""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime, BaseModel, Field, model_validator

from server.agent.model import ModelError, ModelProvider
from server.agent.runner import RunLimits, run_agent
from server.models import Location, Member, TimeWindow, Trip, local
from server.planning.export import collect_facts, ics_from_version, version_summary
from server.planning.overlap import best_window
from server.providers.live import LiveWorld
from server.providers.synthetic import SyntheticWorld
from server.tools.context import Proposal, ToolContext

from .store import Store

PRIVATE_CODES = {"BUDGET_PER_PERSON": "A traveller's budget is not satisfied or not known yet.",
                 "ACCESSIBILITY": "A traveller's access requirement is not confirmed for a stop."}
MAX_WINDOWS = 8
# Interest matching is a plain keyword match (plus these few synonyms), not understanding.
SYNONYMS = {"views": ("scenic", "viewpoint", "overlook", "view"), "scenery": ("scenic", "viewpoint", "overlook"),
            "hiking": ("trail", "hike", "outdoor", "park"), "walks": ("walk", "trail", "park", "outdoor"),
            "nature": ("park", "outdoor", "garden", "trail"), "parks": ("park", "garden"), "coffee": ("cafe", "coffee"),
            "food": ("food", "cafe", "restaurant"), "museums": ("museum", "gallery", "culture"),
            "art": ("art", "gallery", "museum")}


# --- request models --------------------------------------------------------------

class GroupCreate(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    request: str = Field(min_length=3, max_length=500)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    tz: str = "UTC"
    data: Literal["live", "demo"] = "live"
    display_name: str = Field(min_length=1, max_length=40)


class JoinRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class WindowIn(BaseModel):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _ok(self) -> WindowIn:
        d = self.end - self.start
        if d < timedelta(minutes=30) or d > timedelta(hours=14):
            raise ValueError("each availability window must be between 30 minutes and 14 hours")
        return self


class InputsIn(BaseModel):
    windows: list[WindowIn] = Field(default_factory=list, max_length=5)
    budget_dollars: float | None = Field(default=None, ge=0, le=100000)
    no_budget_limit: bool = False
    transport: list[Literal["walk", "bike", "car"]] = Field(default_factory=lambda: ["walk"], min_length=1, max_length=3)
    car_seats: int | None = Field(default=None, ge=1, le=8)
    interests: list[str] = Field(default_factory=list, max_length=8)
    dietary: list[str] = Field(default_factory=list, max_length=5)
    step_free: bool = False
    avoid_rain_pct: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _clean(self) -> InputsIn:
        self.interests = [s.strip()[:30] for s in self.interests if s.strip()]
        self.dietary = [s.strip()[:30] for s in self.dietary if s.strip()]
        return self


class GroupPatch(BaseModel):
    request: str | None = Field(default=None, min_length=3, max_length=500)
    title: str | None = Field(default=None, min_length=1, max_length=80)


class InviteToggle(BaseModel):
    enabled: bool


class AnswerIn(BaseModel):
    answer: Literal["yes", "no", "alt"]
    alt_start: AwareDatetime | None = None
    alt_end: AwareDatetime | None = None


class DecisionIn(BaseModel):
    decision: Literal["accept", "reject"]


# --- in-memory plan state ---------------------------------------------------------

@dataclass
class GRun:
    id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    error: str | None = None
    status: str | None = None
    message: str = ""


@dataclass
class Runtime:
    candidate: dict[str, Any] | None = None  # {"seq": version number, "snap": stored snapshot}
    accepted: dict[str, Any] | None = None
    run: GRun | None = None
    needs_replan: bool = False


def _fmt_window(start: datetime, end: datetime, tz: str) -> str:
    a, b = local(start, tz), local(end, tz)
    t = lambda d: d.strftime("%I:%M %p").lstrip("0")  # noqa: E731
    return f"{a:%a} {t(a)}-{t(b)}"


def _win_json(w: TimeWindow) -> dict[str, str]:
    return {"start": w.start.isoformat(), "end": w.end.isoformat()}


def _windows_of(inp: dict[str, Any] | None) -> list[TimeWindow]:
    return [TimeWindow(start=datetime.fromisoformat(w["start"]), end=datetime.fromisoformat(w["end"]))
            for w in (inp or {}).get("windows", [])]


def member_from_inputs(row: dict[str, Any], inp: dict[str, Any], window: TimeWindow) -> Member:
    cap = None if inp.get("budget_cents") is None else int(inp["budget_cents"])
    modes = tuple(inp.get("transport") or ["walk"])
    car = "car" in modes
    return Member(
        id=row["id"], display_name=row["name"], role="organizer" if row["role"] == "organizer" else "participant",
        availability=(window,), budget_cap_minor=cap, budget_uncapped=bool(inp.get("uncapped")),
        transport_modes=modes, has_car=car, car_capacity=(inp.get("car_seats") or 4) if car else None,
        requires_step_free=True if inp.get("step_free") else None,
        avoid_rain_above=None if inp.get("rain_pct") is None else inp["rain_pct"] / 100,
        interests=tuple(inp.get("interests") or ()), dietary=tuple(inp.get("dietary") or ()))


def register_groups(app: FastAPI, *, store: Store, provider_factory: Callable[[], ModelProvider],
                    now_fn: Callable[[], datetime], busy: threading.Lock, limits: RunLimits,
                    proposal_json: Callable[[Proposal, ToolContext], dict[str, Any]], routing: Any = None,
                    place_cache: Any = None) -> None:
    runtimes: dict[str, Runtime] = {}

    def rt(gid: str) -> Runtime:
        st = runtimes.get(gid)
        if st is None:  # first touch since startup: reload the saved plan and any pending proposal
            st = Runtime()
            acc, prop = store.latest_version("group", gid, "accepted"), store.latest_version("group", gid, "proposal")
            if acc:
                st.accepted = {"seq": acc["seq"], "snap": acc["snapshot"]}
            if prop:
                st.candidate = {"seq": prop["seq"], "snap": prop["snapshot"]}
            runtimes[gid] = st
        return st

    def auth(gid: str, authorization: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        group = store.get_group(gid)
        if group is None:
            raise HTTPException(404, "unknown group")
        token = (authorization or "").removeprefix("Bearer ").strip()
        member = store.member_by_token(gid, token) if token else None
        if member is None:  # also true after the organizer removes someone: access ends immediately
            raise HTTPException(401, "you are not a member of this group (or your access was removed)")
        return group, member

    def organizer(gid: str, authorization: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        group, member = auth(gid, authorization)
        if member["role"] != "organizer":
            raise HTTPException(403, "only the organizer can do that")
        return group, member

    def check_windows(ws: list[WindowIn]) -> None:
        now = now_fn()
        for w in ws:
            if w.end < now - timedelta(hours=1) or w.start > now + timedelta(days=30):
                raise HTTPException(422, "availability must be within the next 30 days")

    def to_stored(inp: InputsIn) -> dict[str, Any]:
        return {"windows": [_win_json(TimeWindow(start=w.start, end=w.end)) for w in inp.windows],
                "budget_cents": None if inp.budget_dollars is None else int(round(inp.budget_dollars * 100)),
                "uncapped": inp.no_budget_limit, "transport": list(inp.transport), "car_seats": inp.car_seats,
                "interests": inp.interests, "dietary": inp.dietary, "step_free": inp.step_free,
                "rain_pct": inp.avoid_rain_pct}

    # --- creating, joining, membership --------------------------------------------

    @app.post("/api/groups")
    def create_group(body: GroupCreate) -> dict[str, Any]:
        try:
            ZoneInfo(body.tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(422, f"unknown time zone {body.tz!r}") from None
        made = store.create_group(title=body.title.strip(), request=body.request.strip(), lat=body.lat, lon=body.lon,
                                  tz=body.tz, data_mode=body.data, organizer_name=body.display_name.strip())
        return made

    @app.get("/api/invites/{token}")
    def peek_invite(token: str) -> dict[str, Any]:
        g = store.group_by_invite(token)
        if g is None:
            raise HTTPException(404, "this invite link is invalid or was revoked")
        return {"title": g["title"], "members": len(store.list_members(g["id"]))}

    @app.post("/api/invites/{token}/join")
    def join(token: str, body: JoinRequest) -> dict[str, Any]:
        g = store.group_by_invite(token)
        if g is None:
            raise HTTPException(404, "this invite link is invalid or was revoked")
        made = store.add_member(g["id"], body.name.strip())
        if made is None:
            raise HTTPException(409, "this group is full")
        return {"group_id": g["id"], **made}

    @app.post("/api/groups/{gid}/invite")
    def toggle_invite(gid: str, body: InviteToggle, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        organizer(gid, authorization)
        return {"invite_token": store.set_invite(gid, enabled=body.enabled)}

    @app.delete("/api/groups/{gid}/members/{mid}")
    def remove_member(gid: str, mid: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        organizer(gid, authorization)
        if not store.remove_member(gid, mid):
            raise HTTPException(404, "no such member (the organizer cannot be removed)")
        return {"removed": mid}

    @app.patch("/api/groups/{gid}")
    def patch_group(gid: str, body: GroupPatch, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        organizer(gid, authorization)
        store.update_group(gid, title=body.title, request=body.request)
        return {"ok": True}

    @app.put("/api/groups/{gid}/me/inputs")
    def save_my_inputs(gid: str, body: InputsIn, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _, me = auth(gid, authorization)
        check_windows(body.windows)
        store.save_inputs(gid, me["id"], to_stored(body))
        return {"saved": True}

    # --- planning ---------------------------------------------------------------------

    def prepare(gid: str) -> tuple[Trip, dict[str, Any], Any, int]:
        group = store.get_group(gid)
        assert group is not None
        members = {m["id"]: m for m in store.list_members(gid)}
        inputs = store.all_inputs(gid)
        wins = {mid: _windows_of(inp) for mid, inp in inputs.items() if mid in members}
        bw = best_window(wins)
        if bw is None:
            raise HTTPException(409, "No shared time of at least 30 minutes yet. Ask people to add their availability.")
        tz = group["tz"]
        for mid in sorted(bw.excluded):  # ask people the best window leaves out
            text = f"Could you make {_fmt_window(bw.window.start, bw.window.end, tz)}?"
            store.add_question(gid, mid, bw.window.start.isoformat(), bw.window.end.isoformat(), text)
        for mid in members:  # a newer plan supersedes older open questions
            store.supersede_pending(gid, mid, (bw.window.start.isoformat(), bw.window.end.isoformat()) if mid in bw.excluded else None)
        attendees = [members[mid] for mid in members if mid in bw.attendees]
        owner = next((m for m in attendees if m["role"] == "organizer"), attendees[0])
        here = Location(id="here", name="Meeting point", lat=group["lat"], lon=group["lon"], timezone=tz)
        trip = Trip(id=f"group-{gid}", owner_id=owner["id"], mode="group", title=group["title"], timezone=tz,
                    currency="USD", origin=here, endpoint=here, window_start=bw.window.start, window_end=bw.window.end,
                    members=tuple(member_from_inputs(m, inputs[m["id"]], bw.window) for m in attendees))
        return trip, group, bw, store.get_group(gid)["version"]

    def launch_plan(gid: str, provider: ModelProvider) -> str:
        """Caller holds `busy`; the worker releases it."""
        try:
            trip, group, bw, version = prepare(gid)
        except Exception:
            busy.release()
            raise
        now = now_fn()
        run = GRun(id=f"g{int(now.timestamp() * 1000):x}")
        state = rt(gid)
        state.run, state.needs_replan = run, False

        def work() -> None:
            try:
                world: Any = SyntheticWorld(trip, now=now) if group["data_mode"] == "demo" else LiveWorld(
                    lat=group["lat"], lon=group["lon"], tz=group["tz"], origin_id="here", now=now, routing=routing,
                    cache=place_cache)
                ctx = ToolContext(trip=trip, places=world, routes=world, weather=world, now=now, private_mode=True)
                res = run_agent(provider=provider, ctx=ctx, request=group["request"], limits=limits,
                                on_event=lambda e: run.events.append({"seq": e.seq, "kind": e.kind, "summary": e.summary}))
                run.status, run.message = res.status, res.message
                if res.proposal:
                    all_names = {m["id"]: m["name"] for m in store.list_members(gid)}
                    snap = make_snapshot(group, res.proposal, ctx, version,
                                         [{"id": mid, "name": all_names.get(mid, "?")} for mid in sorted(bw.excluded)])
                    places, evid = collect_facts(res.proposal.plan, ctx.details, ctx.evidence)
                    seq = store.add_version("group", gid, "proposal", "proposal", snap,
                                            plan=res.proposal.plan.model_dump_json(), places=places, evidence=evid,
                                            explanation=res.proposal.explanation)
                    state.candidate = {"seq": seq, "snap": snap}
                elif res.status == "needs_clarification" and res.clarification:
                    run.message = "The planner needs more information: " + str(res.clarification.get("question", ""))
            except Exception as exc:  # never includes credentials
                run.error = f"{type(exc).__name__}: {str(exc)[:200]}"
            finally:
                busy.release()  # release first: a client that sees 'done' may start the next run at once
                run.done = True

        threading.Thread(target=work, daemon=True).start()
        return run.id

    @app.post("/api/groups/{gid}/plan")
    def plan_for_everyone(gid: str, authorization: str | None = Header(default=None)):
        organizer(gid, authorization)
        try:
            provider = provider_factory()
        except ModelError as exc:
            return JSONResponse({"detail": f"Model not configured: {exc}"}, status_code=503)
        if not busy.acquire(blocking=False):
            return JSONResponse({"detail": "A plan is already running; wait for it to finish."}, status_code=429)
        return {"run_id": launch_plan(gid, provider)}

    def try_autoplan(gid: str) -> bool:
        """After a 'yes' / new time: re-plan right away if the planner is free, else flag it."""
        try:
            provider = provider_factory()
        except ModelError:
            rt(gid).needs_replan = True
            return False
        if not busy.acquire(blocking=False):
            rt(gid).needs_replan = True
            return False
        try:
            launch_plan(gid, provider)
            return True
        except HTTPException:
            rt(gid).needs_replan = True
            return False

    @app.post("/api/groups/{gid}/questions/{qid}/answer")
    def answer_question(gid: str, qid: str, body: AnswerIn, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _, me = auth(gid, authorization)
        q = store.get_question(gid, qid)
        if q is None:
            raise HTTPException(404, "unknown question")
        if q["member_id"] != me["id"]:
            raise HTTPException(403, "that question is addressed to someone else")
        if q["status"] != "pending":
            raise HTTPException(409, "you already answered that question")
        inp = store.get_inputs(me["id"]) or {
            "windows": [], "budget_cents": None, "uncapped": False, "transport": ["walk"], "car_seats": None,
            "interests": [], "dietary": [], "step_free": False, "rain_pct": None}
        stored = dict(inp)
        stored["windows"] = list(inp.get("windows", []))
        add: tuple[str, str] | None = None
        if body.answer == "yes":
            add = (q["win_start"], q["win_end"])
        elif body.answer == "alt":
            if body.alt_start is None or body.alt_end is None:
                raise HTTPException(422, "give a start and end time for your alternative")
            w = WindowIn(start=body.alt_start, end=body.alt_end)
            check_windows([w])
            add = (w.start.astimezone(UTC).isoformat(), w.end.astimezone(UTC).isoformat())
        replanning = False
        if add is not None:
            if len(stored["windows"]) >= MAX_WINDOWS:
                raise HTTPException(422, "too many availability windows; remove one first")
            stored["windows"].append({"start": add[0], "end": add[1]})
            store.save_inputs(gid, me["id"], stored)
        store.answer_question(gid, qid, body.answer, add if body.answer == "alt" else None)
        if add is not None:
            replanning = try_autoplan(gid)
        return {"answered": body.answer, "replanning": replanning}

    @app.post("/api/groups/{gid}/decision")
    def decide(gid: str, body: DecisionIn, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        group, _ = organizer(gid, authorization)
        state = rt(gid)
        if state.candidate is None:
            raise HTTPException(409, "there is no proposal to decide on")
        if body.decision == "accept":
            if state.candidate["snap"]["version"] != group["version"]:
                raise HTTPException(409, "this proposal is stale: inputs changed since it was made. Plan again.")
            store.set_status("group", gid, state.candidate["seq"], "accepted")
            state.accepted, state.candidate = state.candidate, None
        else:
            store.set_status("group", gid, state.candidate["seq"], "rejected")
            state.candidate = None
        return {"decided": body.decision}

    # --- state (what each viewer is allowed to see) ----------------------------------------

    def make_snapshot(group: dict[str, Any], proposal: Proposal, ctx: ToolContext, version: int,
                      excluded: list[dict[str, str]]) -> dict[str, Any]:
        """Everything needed to show a plan to ANY viewer later. It holds private values (per-person cost,
        private issue details); `render` is the only place they are filtered, per viewer."""
        base = proposal_json(proposal, ctx)
        rep = proposal.plan.validation
        assert rep is not None
        text = " ".join(f"{b['name']} {' '.join(b.get('categories', []))}" for b in base["blocks"]).lower()
        people = []
        for m in ctx.trip.members:
            chk = next((c for c in rep.checks if c.code == "BUDGET_PER_PERSON" and c.participant_ids == (m.id,)), None)
            status = {"pass": "within budget", "fail": "over budget", "unknown": "budget unknown"}.get(chk.status if chk else "unknown")
            hit = [i for i in m.interests if any(t in text for t in (i.lower(), *SYNONYMS.get(i.lower(), ())))]
            t = rep.per_person_totals.get(m.id)
            people.append({"id": m.id, "name": m.display_name, "budget_status": status,
                           "fit": {"matched": hit, "total": len(m.interests)},
                           "cost": None if t is None else {
                               "low": t.low_minor / 100, "high": t.high_minor / 100,
                               "cap": None if t.cap_minor is None else t.cap_minor / 100, "has_unknown": t.has_unknown}})
        keep = ("id", "state", "overall", "explanation", "blocks", "legs", "checks_passed", "notes", "confidence", "verify", "date")
        return {"base": {k: base[k] for k in keep}, "people": people, "excluded": excluded, "version": version,
                "issues": [{"status": c.status, "code": c.code, "message": c.message, "participants": list(c.participant_ids)}
                           for c in rep.checks if c.status != "pass"],
                "window": _fmt_window(ctx.trip.window_start, ctx.trip.window_end, group["tz"]),
                "data": {"synthetic": bool(ctx.places.synthetic), "places": ctx.places.name}}

    def render(entry: dict[str, Any], group: dict[str, Any], viewer: str) -> dict[str, Any]:
        snap = entry["snap"]
        issues = []
        for i in snap["issues"]:
            private = i["code"] in PRIVATE_CODES and viewer not in i["participants"]
            issues.append({"status": i["status"], "code": i["code"],
                           "message": PRIVATE_CODES[i["code"]] if private else i["message"]})
        people = []
        for p in snap["people"]:
            row = {k: p[k] for k in ("id", "name", "budget_status", "fit")}
            if p["id"] == viewer:  # only you see your own amounts
                row["my_cost"] = p["cost"]
            people.append(row)
        qs = {q["member_id"]: q for q in store.list_questions(group["id"]) if q["status"] != "expired"}
        out = dict(snap["base"])
        out.update({"issues": issues, "totals": None, "people": people, "seq": entry["seq"],
                    "excluded": [{"id": e["id"], "name": e["name"], "question": (qs.get(e["id"]) or {}).get("status")}
                                 for e in snap["excluded"]],
                    "stale": snap["version"] != group["version"], "window": snap["window"], "data": snap["data"],
                    "attending": any(p["id"] == viewer for p in snap["people"])})
        return out

    @app.get("/api/groups/{gid}")
    def get_group(gid: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        group, me = auth(gid, authorization)
        members = store.list_members(gid)
        inputs = store.all_inputs(gid)
        state = rt(gid)
        entry = state.candidate or state.accepted
        status_by: dict[str, str] = {p["id"]: p["budget_status"] for p in entry["snap"]["people"]} if entry else {}
        pub = []
        for m in members:  # PUBLIC fields only: no budget, access or dietary needs
            inp = inputs.get(m["id"]) or {}
            pub.append({"id": m["id"], "name": m["name"], "role": m["role"], "is_me": m["id"] == me["id"],
                        "submitted": bool(inp.get("windows")), "windows": inp.get("windows", []),
                        "transport": inp.get("transport", []), "interests": inp.get("interests", []),
                        "budget_status": status_by.get(m["id"])})
        mine = inputs.get(me["id"])
        my_inputs = None if mine is None else {
            "windows": mine.get("windows", []), "budget_dollars": None if mine.get("budget_cents") is None else mine["budget_cents"] / 100,
            "no_budget_limit": mine.get("uncapped", False), "transport": mine.get("transport", ["walk"]),
            "car_seats": mine.get("car_seats"), "interests": mine.get("interests", []), "dietary": mine.get("dietary", []),
            "step_free": mine.get("step_free", False), "avoid_rain_pct": mine.get("rain_pct")}
        names = {m["id"]: m["name"] for m in members}
        questions = []
        for q in store.list_questions(gid):
            if q["status"] == "expired":
                continue
            if q["member_id"] == me["id"] or me["role"] == "organizer":
                questions.append({"id": q["id"], "text": q["text"], "status": q["status"], "for": names.get(q["member_id"], "?"),
                                  "mine": q["member_id"] == me["id"], "start": q["win_start"], "end": q["win_end"]})
        run = None
        if state.run:
            r = state.run
            run = {"id": r.id, "done": r.done, "error": r.error, "status": r.status, "message": r.message,
                   "events": [e for e in r.events if e["kind"] != "tool_call"]}
        is_org = me["role"] == "organizer"
        return {
            "group": {"id": gid, "title": group["title"], "request": group["request"], "version": group["version"],
                      "tz": group["tz"], "data_mode": group["data_mode"],
                      "invite_token": group["invite_token"] if is_org else None},
            "me": {"id": me["id"], "name": me["name"], "role": me["role"], "inputs": my_inputs},
            "members": pub, "questions": questions, "run": run, "needs_replan": state.needs_replan and is_org,
            "candidate": render(state.candidate, group, me["id"]) if state.candidate else None,
            "accepted": render(state.accepted, group, me["id"]) if state.accepted else None,
            "share_token": store.share_for("group", gid) if is_org else None}

    # --- history, sharing, export, delete -------------------------------------------------

    @app.get("/api/groups/{gid}/history")
    def group_history(gid: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        auth(gid, authorization)
        return {"versions": [version_summary(r) for r in store.list_versions("group", gid)]}

    @app.post("/api/groups/{gid}/share")
    def group_share(gid: str, body: InviteToggle, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        organizer(gid, authorization)
        if not body.enabled:
            store.revoke_share("group", gid)
            return {"share_token": None}
        if store.latest_version("group", gid, "accepted") is None:
            raise HTTPException(409, "accept a plan before sharing it")
        return {"share_token": store.share_for("group", gid) or store.create_share("group", gid)}

    @app.get("/api/groups/{gid}/export.ics")
    def group_ics(gid: str, authorization: str | None = Header(default=None)) -> Response:
        group, _ = auth(gid, authorization)
        row = store.latest_version("group", gid, "accepted")
        if row is None or not row["plan"]:
            raise HTTPException(404, "no accepted plan to export")
        return Response(ics_from_version(row, group["title"], now_fn()), media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="sidequest-group-plan.ics"'})

    @app.delete("/api/groups/{gid}")
    def delete_group(gid: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        organizer(gid, authorization)
        store.delete_group(gid)
        runtimes.pop(gid, None)
        return {"deleted": True}
