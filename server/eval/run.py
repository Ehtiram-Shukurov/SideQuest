"""Run the scenario set against a model and report outcomes, violations, unknowns and cost.

    uv run python -m server.eval.run                 # uses GEMINI_API_KEY from .env (free tier: ~4-8 requests per scenario)
    uv run python -m server.eval.run --only too_short

Opt-in: it spends model quota. Nothing here is a unit test. A saved plan is re-validated by code
INDEPENDENTLY of the agent loop, so "violations" counts failed hard checks that slipped through
(it should always be 0). Data is synthetic; this measures the loop, not real recommendations."""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from server.agent.config import load_dotenv, provider_from_env
from server.agent.model import ModelError, ModelProvider
from server.agent.runner import RunLimits, run_agent
from server.planning.validators import validate_plan

from .scenarios import SCENARIOS, Scenario, build_ctx


def judge(s: Scenario, status: str) -> bool:
    got = {"proposal_saved": "plan", "needs_clarification": "ask"}.get(status, "other")
    return got == s.expect or (s.expect == "either" and got in ("plan", "ask"))


def run_scenario(provider: ModelProvider, s: Scenario, limits: RunLimits | None = None) -> dict[str, Any]:
    ctx = build_ctx(s)
    t0 = time.monotonic()
    res = run_agent(provider=provider, ctx=ctx, request=s.request,
                    limits=limits or RunLimits(max_turns=16, max_tool_calls=24, max_validations=6, max_seconds=180))
    out: dict[str, Any] = {"id": s.id, "expected": s.expect, "status": res.status, "ok": judge(s, res.status),
                           "tool_calls": res.tool_calls, "tokens": res.usage.get("total_tokens", 0),
                           "seconds": round(time.monotonic() - t0, 1), "violations": 0, "unknown_checks": 0,
                           "state": None, "confidence": None}
    if res.proposal is not None:
        plan = res.proposal.plan
        rep = validate_plan(ctx.trip, plan, ctx.details, forecast=ctx.forecast, evidence=ctx.evidence)
        out.update(violations=sum(1 for c in rep.checks if c.status == "fail"),
                   unknown_checks=sum(1 for c in rep.checks if c.status == "unknown"),
                   state=plan.state, confidence=rep.confidence)
        if s.id == "evening_unknown_price" and rep.overall == "checked":
            out["violations"] += 1  # an unknown price must keep the plan provisional
    return out


def table(rows: list[dict[str, Any]]) -> str:
    head = f"{'scenario':<24}{'expect':<8}{'status':<22}{'ok':<5}{'calls':<7}{'tokens':<8}{'sec':<7}{'viol':<6}{'unk':<5}state"
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(f"{r['id']:<24}{r['expected']:<8}{r['status']:<22}{'yes' if r['ok'] else 'NO':<5}{r['tool_calls']:<7}"
                     f"{r['tokens']:<8}{r['seconds']:<7}{r['violations']:<6}{r['unknown_checks']:<5}{r['state'] or '-'}")
    n = len(rows)
    lines.append(f"\nexpected outcome met: {sum(r['ok'] for r in rows)}/{n}   hard violations: {sum(r['violations'] for r in rows)}   "
                 f"avg calls: {sum(r['tool_calls'] for r in rows) / max(n, 1):.1f}   tokens: {sum(r['tokens'] for r in rows)}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="run a single scenario id")
    ap.add_argument("--json", help="also write results to this file")
    args = ap.parse_args()
    load_dotenv()
    try:
        provider = provider_from_env()
    except ModelError as exc:
        print(f"Not configured: {exc}")
        return 2
    chosen = [s for s in SCENARIOS if not args.only or s.id == args.only]
    if not chosen:
        print("unknown scenario; choose from:", ", ".join(s.id for s in SCENARIOS))
        return 2
    rows = []
    for s in chosen:
        print(f"running {s.id} ...", flush=True)
        rows.append(run_scenario(provider, s))
    print("\n" + table(rows))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
    return 0 if all(r["ok"] and r["violations"] == 0 for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
