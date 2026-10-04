"""P4 (free-tier): move to another free model when the daily quota is used up; never wait for it."""
from __future__ import annotations

import json

import httpx
import pytest

from server.agent.config import provider_from_env
from server.agent.gemini import GeminiProvider
from server.agent.model import ModelRateLimited, ModelTurn, ToolResult
from server.agent.runner import run_agent
from server.tools.context import ToolContext

from .test_agent_loop import make_ctx, solo

OK = {"id": "i1", "status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": "ok"}]}]}
DAILY = {"error": {"message": "Rate limit exceeded for model m1 (limit: 20 requests per day on Free Tier)."}}
BURST = {"error": {"message": "Too many requests per minute."}}


def make(handler, **kw):
    seen: list[dict] = []
    sleeps: list[float] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        return handler(body, len(seen))

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return GeminiProvider("k", "m1", client=client, sleep=sleeps.append, **kw), seen, sleeps


def test_daily_quota_switches_model_without_waiting_and_stays_on_it_for_the_session():
    def handler(body, n):
        return httpx.Response(429, json=DAILY) if body["model"] == "m1" else httpx.Response(200, json=OK)

    provider, seen, sleeps = make(handler, fallback_models=["m2", "m3"])
    s = provider.start(system="s", tools=[])
    assert s.send(user="hi").text == "ok"
    assert [b["model"] for b in seen] == ["m1", "m2"] and sleeps == []  # a daily limit is not retried or slept on
    assert "m2" in s.note and "m1" in s.note
    s.send(tool_results=[ToolResult("c", "n", "{}")])
    assert [b["model"] for b in seen][-1] == "m2" and seen[-1]["previous_interaction_id"] == "i1"  # session stays put


def test_burst_limits_still_retry_the_same_model_and_all_models_exhausted_is_reported():
    calls = []

    def burst_then_ok(body, n):
        calls.append(body["model"])
        return httpx.Response(429, json=BURST) if n == 1 else httpx.Response(200, json=OK)

    provider, _, sleeps = make(burst_then_ok, fallback_models=["m2"])
    assert provider.start(system="s", tools=[]).send(user="hi").text == "ok"
    assert calls == ["m1", "m1"] and len(sleeps) == 1  # per-minute limit: retry the same model

    provider, seen, sleeps = make(lambda body, n: httpx.Response(429, json=DAILY), fallback_models=["m2", "m3"])
    with pytest.raises(ModelRateLimited) as exc:
        provider.start(system="s", tools=[]).send(user="hi")
    assert exc.value.daily and [b["model"] for b in seen] == ["m1", "m2", "m3"] and sleeps == []


def test_a_running_session_never_switches_model_mid_conversation():
    def handler(body, n):
        return httpx.Response(200, json=OK) if n == 1 else httpx.Response(429, json=DAILY)

    provider, seen, _ = make(handler, fallback_models=["m2"])
    s = provider.start(system="s", tools=[])
    s.send(user="hi")
    with pytest.raises(ModelRateLimited):
        s.send(tool_results=[ToolResult("c", "n", "{}")])
    assert [b["model"] for b in seen] == ["m1", "m1"]  # no hop to m2 once a conversation exists


def test_runner_shows_the_fallback_in_the_activity_feed_and_config_reads_the_env(monkeypatch):
    class Sess:
        note = "m1 is out of free quota or overloaded; using m2 for this run."

        def send(self, **_):
            return ModelTurn(text="done")

    class Prov:
        name = "x"

        def start(self, **_):
            return Sess()

    res = run_agent(provider=Prov(), ctx=make_ctx(solo()), request="x")
    assert [e.kind for e in res.events][:1] == ["info"] and "m2" in res.events[0].summary

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", " a , b ")
    assert provider_from_env().fallbacks == ("a", "b")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "none")
    assert provider_from_env().fallbacks == ()  # an explicit "none" disables fallbacks
