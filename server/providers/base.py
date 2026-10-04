"""Provider interfaces. Real adapters implement these; tools never import a concrete provider.

Adapters must distinguish "no results" (empty list) from failure (ProviderError), report
what they cannot do (unsupported mode, beyond forecast horizon) and never invent data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from server.models import Evidence, Place
from server.models.weather import ForecastPeriod
from server.planning.assemble import LegEstimate


CATEGORY_NAMES = ("food", "outdoor", "scenic", "culture")

# Everyday words a model (or a person) uses, mapped to a provider category. Used only as a fallback
# when a literal name/tag match finds nothing, so a real venue name still wins.
QUERY_WORDS: dict[str, str] = {
    **dict.fromkeys(("coffee", "cafe", "café", "tea", "bakery", "breakfast", "brunch", "lunch", "dinner", "restaurant",
                     "eat", "food", "snack", "dessert", "ice cream", "drink", "bar", "pizza", "burger"), "food"),
    **dict.fromkeys(("park", "walk", "hike", "hiking", "garden", "trail", "outdoor", "outdoors", "outside", "nature",
                     "river", "lake", "green"), "outdoor"),
    **dict.fromkeys(("view", "views", "viewpoint", "scenic", "overlook", "lookout", "sunset"), "scenic"),
    **dict.fromkeys(("museum", "gallery", "art", "culture", "history", "attraction", "sightseeing", "exhibit"), "culture"),
}


def category_for_words(text: str) -> str | None:
    """The category an everyday phrase refers to ('coffee shop' -> food), or None."""
    t = text.strip().lower()
    if t in QUERY_WORDS:
        return QUERY_WORDS[t]
    return next((c for w, c in QUERY_WORDS.items() if len(w) > 2 and w in t), None)


class ProviderError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # "unavailable" | "timeout" | "unsupported" | "rate_limited"


@dataclass(frozen=True)
class PlaceSummary:
    id: str
    name: str
    categories: tuple[str, ...]
    lat: float
    lon: float
    source: str
    synthetic: bool = False


@dataclass(frozen=True)
class PlaceDetails:
    place: Place
    evidence: tuple[Evidence, ...]
    untrusted_text: str = ""  # free text from the source: DATA only, never instructions


class PlacesProvider(Protocol):
    name: str
    synthetic: bool

    def search(self, *, category: str, query: str, limit: int) -> list[PlaceSummary]: ...

    def details(self, place_id: str, visit_day: date) -> PlaceDetails | None: ...


class RoutesProvider(Protocol):
    name: str
    synthetic: bool
    modes: tuple[str, ...]

    def estimate(self, origin_id: str, dest_id: str, mode: str, depart: datetime) -> LegEstimate | None:
        """None = this provider has no estimate for the pair. Raises ProviderError on failure."""


class WeatherProvider(Protocol):
    name: str
    synthetic: bool
    horizon_days: int

    def forecast(self, lat: float, lon: float, start: datetime, end: datetime) -> list[ForecastPeriod]:
        """Empty list = no forecast available for that period (e.g. beyond the horizon)."""
