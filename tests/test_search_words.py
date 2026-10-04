"""Regression for a bug found only by running a REAL model: it searched with everyday words
("coffee", "park") that matched no venue name or OSM tag, got zero results every time, and looped
until the turn limit. Words now map to categories and an empty search says what is available."""
from __future__ import annotations

from server.providers.base import category_for_words
from server.tools.planning_tools import build_registry

from .test_agent_loop import make_ctx, solo


def test_everyday_words_map_to_categories_but_real_names_still_win():
    assert [category_for_words(w) for w in ("coffee", "coffee shop", "Lunch", "river walk", "art museum", "viewpoint")] == [
        "food", "food", "food", "outdoor", "culture", "scenic"]
    assert category_for_words("zzz") is None and category_for_words("a") is None  # no accidental matches


def test_synthetic_search_understands_words_and_falls_back_from_a_missing_literal_match():
    w = make_ctx(solo()).places
    names = lambda **kw: {p.id for p in w.search(limit=10, **{"category": "", "query": "", **kw})}  # noqa: E731
    assert "corner-cafe" in names(query="coffee")  # no venue is named 'coffee': the word means the food category
    assert "corner-cafe" in names(category="coffee")  # a model that puts the word in `category`
    assert names(query="park") >= {"lakeside-trail", "river-overlook"}
    assert names(query="Corner Cafe") == {"corner-cafe"}  # a real name is matched literally, not widened
    assert names(query="xyzzy") == set()


def test_an_empty_search_tells_the_model_what_is_available_instead_of_leaving_it_to_guess():
    ctx = make_ctx(solo())
    out = build_registry(ctx).call("search_places", {"query": "xyzzy"}).data
    assert out["status"] == "no_results" and out["results"] == []
    assert set(out["available_categories"]) >= {"food", "outdoor", "culture"} and all(n > 0 for n in out["available_categories"].values())
    assert "Do not retry" in out["hint"] and "food" in out["hint"]
    ok = build_registry(ctx).call("search_places", {"query": "coffee"}).data  # and the loop's old query now works
    assert ok["status"] == "ok" and any(r["id"] == "corner-cafe" for r in ok["results"])
