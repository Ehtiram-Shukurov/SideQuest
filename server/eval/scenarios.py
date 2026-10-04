"""A small, fixed scenario set for evaluating the planner end to end on SYNTHETIC data.

These are held out from the unit tests' scripted flows on purpose: a model is run against them
and an INDEPENDENT code re-check of whatever it saved counts hard-constraint violations. Venues,
prices, hours and the forecast are the invented ones in server/providers/synthetic.py, so a result
says how well the loop behaves, not how good real recommendations are."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from server.models import Location, Member, TimeWindow, Trip
from server.providers.synthetic import SyntheticWorld
from server.tools.context import ToolContext

TZ = "America/Chicago"
NOW = datetime(2026, 6, 4, 12, tzinfo=UTC)  # inside the synthetic forecast horizon
DAY = (2026, 6, 6)


@dataclass(frozen=True)
class Scenario:
    id: str
    request: str
    start_hour: int
    minutes: int
    budget_dollars: float | None
    expect: str  # "plan": a saved plan is the right outcome; "ask": a question; "either": both are fine
    why: str
    mode: str = "walk"
    avoid_rain_pct: int | None = None


SCENARIOS = (
    Scenario("short_outing", "Get outside for a bit and come back here.", 10, 90, 10, "plan",
             "A short free outing must include outbound and return travel."),
    Scenario("tight_budget", "Some food and one cultural stop.", 10, 180, 5, "either",
             "The museum costs more than the cap; the plan must stay in budget or the agent must ask."),
    Scenario("closing_time", "Coffee, then a museum.", 14, 150, 40, "either",
             "The cafe closes at 15:00 and the museum stops admitting at 16:00; hours must be respected."),
    Scenario("too_short", "A proper museum visit.", 10, 45, 40, "ask",
             "The visit plus travel cannot fit 45 minutes: expect a specific conflict and a question."),
    Scenario("rainy_afternoon", "An outdoor walk.", 13, 120, 20, "either",
             "The afternoon forecast is wet and the traveller limits rain: no outdoor stop may be saved.",
             avoid_rain_pct=30),
    Scenario("evening_unknown_price", "Dinner at a market.", 17, 180, 40, "plan",
             "The market's price is unknown: a saved plan must be provisional, never 'checked'."),
)


def build_ctx(s: Scenario) -> ToolContext:
    tz = ZoneInfo(TZ)
    start = datetime(*DAY, s.start_hour, tzinfo=tz).astimezone(UTC)
    end = start + timedelta(minutes=s.minutes)
    here = Location(id="here", name="Home (synthetic)", lat=44.97, lon=-93.26, timezone=TZ)
    me = Member(id="m1", display_name="You", availability=(TimeWindow(start=start, end=end),),
                budget_cap_minor=None if s.budget_dollars is None else int(s.budget_dollars * 100),
                transport_modes=(s.mode,),
                avoid_rain_above=None if s.avoid_rain_pct is None else s.avoid_rain_pct / 100)
    trip = Trip(id=f"eval-{s.id}", owner_id="m1", mode="solo", title=s.id, timezone=TZ, currency="USD",
                origin=here, endpoint=here, window_start=start, window_end=end, members=(me,))
    world = SyntheticWorld(trip, now=NOW)
    return ToolContext(trip=trip, places=world, routes=world, weather=world, now=NOW)
