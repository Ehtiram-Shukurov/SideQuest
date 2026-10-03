"""Group availability: find the window that fits the most people.

Pure functions over absolute (UTC) time windows. The planner schedules one shared window, so we
pick the segment where the most members are free (ties: the longest). Anyone left out is reported
so the app can ask them whether they could make it. Splits are not supported."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

from server.models import TimeWindow

MIN_MINUTES = 30
MAX_MINUTES = 480


@dataclass(frozen=True)
class BestWindow:
    window: TimeWindow
    attendees: frozenset[str]
    excluded: frozenset[str]  # members who submitted availability but are not free in this window


def best_window(windows: Mapping[str, Sequence[TimeWindow]], *, min_minutes: int = MIN_MINUTES,
                max_minutes: int = MAX_MINUTES) -> BestWindow | None:
    """The segment of at least `min_minutes` with the most members free; None if there is none."""
    people = {m for m, ws in windows.items() if ws}
    if not people:
        return None
    points = sorted({t for ws in windows.values() for w in ws for t in (w.start, w.end)})
    segs: list[list] = []
    for a, b in zip(points, points[1:]):
        who = frozenset(m for m, ws in windows.items() if any(w.start <= a and b <= w.end for w in ws))
        if not who:
            continue
        if segs and segs[-1][1] == a and segs[-1][2] == who:
            segs[-1][1] = b  # same people stay free across the boundary: merge
        else:
            segs.append([a, b, who])
    best = None
    for a, b, who in segs:
        if b - a < timedelta(minutes=min_minutes):
            continue
        key = (len(who), b - a)
        if best is None or key > best[0]:
            best = (key, a, b, who)
    if best is None:
        return None
    _, a, b, who = best
    b = min(b, a + timedelta(minutes=max_minutes))
    return BestWindow(TimeWindow(start=a, end=b), who, frozenset(people - who))


def everyone_overlap(windows: Mapping[str, Sequence[TimeWindow]]) -> bool:
    """True if one window fits every member who submitted availability."""
    bw = best_window(windows)
    return bw is not None and not bw.excluded
