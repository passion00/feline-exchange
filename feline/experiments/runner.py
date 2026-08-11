from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Callable
from uuid import uuid4

from feline import __version__
from feline.config import AppConfig
from feline.core.events import AIAnalysisResult, MarketThesis, NewsEvent, OrderUpdate, RiskEvent, SignalEvent, ThesisStateEvent
from feline.intelligence.service import LlamaCppClient, validate_news_impact
from feline.runtime import FelineRuntime

from .cases import load_cases
from .models import ExperimentCase, ExperimentResult
from .reports import write_reports
from .scoring import score_semantics, summarize
from .scenarios import forward_returns, scenario_prices, ticks


class ExperimentError(RuntimeError):
    pass


class FixtureExperimentAI:
    provider_name = "fixture"

    def __init__(self, case: ExperimentCase):
        self.case = case; self.raw = None; self.job = None; self.validation_error = None

    async def analyze(self, job):
        self.job = job
        mode = self.case.failure_mode
        if mode == "timeout": await asyncio.sleep(.2)
        if mode == "offline": raise ConnectionError("injected endpoint unavailable")
        if mode == "malformed": self.raw = {"event_type": "malformed"}; return self.raw
        if mode == "wrong_schema": self.raw = {"schema": "trading-assessment-v1", "action": "BUY"}; return self.raw
        if mode == "score_out_of_range":
            concise=self.case.fixture_analysis or {};instrument=(concise.get("instruments") or ["EURUSD"])[0]
            self.raw={"event_type":"test","event_summary":"invalid","importance":2,"confidence":-1,"expected_horizon":"hour","affected_instruments":[{"instrument":instrument,"directional_bias":"LONG","confidence":2,"relevance":2,"monitoring_priority":2,"rationale":"invalid"}],"reasoning_summary":"invalid","risk_warnings":[],"invalidation_conditions":[]};return self.raw
        if mode == "huge_response": self.raw = {**(self.case.fixture_response or {}), "reasoning_summary": "x" * 200_000}; return self.raw
        if self.case.fixture_response is not None:self.raw = json.loads(json.dumps(self.case.fixture_response))
        else:
            concise=self.case.fixture_analysis or {};rows=[]
            for instrument in concise.get("instruments",[]):rows.append({"instrument":instrument,"directional_bias":concise.get("bias","NEUTRAL"),"confidence":concise.get("confidence",.85),"relevance":concise.get("relevance",.85),"monitoring_priority":concise.get("priority",.8),"rationale":concise.get("rationale","Deterministic fixture assessment")})
            confidence=concise.get("confidence",.85 if rows else .2)
            self.raw={"event_type":concise.get("event_type",self.case.category),"event_summary":concise.get("summary",self.case.headline),"importance":concise.get("importance",confidence),"confidence":confidence,"expected_horizon":concise.get("horizon","2 hours"),"affected_instruments":rows,"reasoning_summary":concise.get("reasoning","Deterministic fixture response for lifecycle validation"),"risk_warnings":concise.get("warnings",[]),"invalidation_conditions":concise.get("invalidation",["price fails to confirm"])}
        try: validate_news_impact(self.raw, job, self.provider_name, 0)
        except ValueError as exc: self.validation_error = str(exc)
        return self.raw


class CapturingAI:
    def __init__(self, client):
        self.client = client; self.provider_name = getattr(client, "provider_name", type(client).__name__); self.raw = None; self.job = None; self.started = None; self.ended = None; self.error = None

    async def analyze(self, job):
        self.job = job; self.started = datetime.now(timezone.utc); started = time.perf_counter()
        try:
            self.raw = await self.client.analyze(job); return self.raw
        except Exception as exc:
            self.error = type(exc).__name__; raise
        finally:
            self.ended = datetime.now(timezone.utc); self.latency_ms = (time.perf_counter() - started) * 1000


def _git_commit() -> str | None:
    try: return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError): return None


def _substantive_digest(cases: list[dict], summary: dict) -> str:
    stable = {"cases": [{k: v for k, v in row.items() if k != "timings"} for row in cases], "summary": {k: v for k, v in summary.items() if k != "performance"}}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _safe_report_payload(value: Any) -> bool:
    text = json.dumps(value, default=str).lower()
    forbidden = ("feline_oanda_api_token", "authorization: bearer", "api_key=", "broker_password")
    return not any(x in text for x in forbidden)


def _universe_config(case: ExperimentCase) -> dict:
    return {str(row["instrument"]).upper(): {"broker_symbol": row.get("broker_symbol", row["instrument"]), "asset_class": row.get("asset_class", "unknown"), "name": row.get("name", row["instrument"]), "aliases": row.get("aliases", []), "shortable": bool(row.get("shortable", False))} for row in case.universe}


def _proposed(raw: Any) -> list[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("affected_instruments"), list): return []
    return [x for x in raw["affected_instruments"] if isinstance(x, dict)]


async def _run_case(case: ExperimentCase, base_config: AppConfig, database_path: Path, ai_mode: str, shared_client=None, include_price=True) -> dict:
    ai_config = replace(base_config.ai, enabled=True, decision_mode="news_thesis", retries=0, request_timeout_seconds=min(base_config.ai.request_timeout_seconds, .05) if ai_mode == "fixture" else base_config.ai.request_timeout_seconds, temperature=base_config.ai.temperature)
    config = replace(base_config, database_path=str(database_path), ai=ai_config, markets=_universe_config(case), news=replace(base_config.news, enabled=False), confirmation=replace(base_config.confirmation, minimum_signal_strength=0))
    fixture = FixtureExperimentAI(case) if ai_mode == "fixture" else None
    capture = CapturingAI(fixture or shared_client or LlamaCppClient(config.ai))
    runtime = FelineRuntime(config, ai_client=capture, recover=False, autonomous_trading_enabled=False)
    if runtime.external_broker: raise ExperimentError("Offline experiments may not use an external broker")
    events: dict[str, list] = {"thesis": [], "lifecycle": [], "signal": [], "risk": [], "order": [], "ai": []}
    async def collect(event):
        if isinstance(event, MarketThesis): events["thesis"].append(event)
        elif isinstance(event, ThesisStateEvent): events["lifecycle"].append(event)
        elif isinstance(event, SignalEvent): events["signal"].append(event)
        elif isinstance(event, RiskEvent): events["risk"].append(event)
        elif isinstance(event, OrderUpdate): events["order"].append(event)
        elif isinstance(event, AIAnalysisResult): events["ai"].append(event)
    for kind in (MarketThesis, ThesisStateEvent, SignalEvent, RiskEvent, OrderUpdate, AIAnalysisResult): runtime.bus.subscribe(kind, collect)
    before_news = runtime.database.count("news_events"); before_theses = runtime.database.count("market_theses")
    published = datetime.fromisoformat(case.published_at.replace("Z", "+00:00")); event_id = f"experiment-news:{case.case_id}"
    event = NewsEvent(id=event_id, timestamp=published, ingestion_timestamp=published, headline=case.headline, body=case.body, source=case.source)
    runtime.ai.start(); accepted = runtime.submit_news(event)
    duplicate_rejected = None
    if case.duplicate_of is not None: duplicate_rejected = not runtime.submit_news(event)
    await runtime.ai.queue.join(); await runtime.bus.drain()
    raw = capture.raw; validation_error = fixture.validation_error if fixture else None
    if events["ai"]: validation_error = validation_error or events["ai"][-1].error
    thesis = events["thesis"][-1] if events["thesis"] else None
    assets = [asdict(x) for x in thesis.affected_assets] if thesis else []
    raw_assets = _proposed(raw); universe = {x["instrument"] for x in case.universe}; unsupported = [str(x.get("instrument")) for x in raw_assets if str(x.get("instrument", "")).upper() not in universe]
    semantic = asdict(score_semantics(case, assets, validation_error))
    instrument = str((raw_assets or [{}])[0].get("instrument") or (case.expectation.acceptable_instruments or tuple(universe) or ("EURUSD",))[0]).upper()
    prices = scenario_prices(case.price_scenario) if include_price and case.price_scenario != "none" else []
    if prices and thesis:
        # Establish a cost-independent reference quote before focus/confirmation if none existed.
        entries = [x for x in runtime.focus.focus.values() if x.instrument == instrument]
        for entry in entries:
            if entry.reference_price is None: entry.reference_price = prices[0]
        if case.failure_mode == "feed_unhealthy": runtime.feed_trading_ready = False; runtime.feed_state = "DEGRADED"
        if case.failure_mode in {"risk_reject", "emergency_stop"}: runtime.autonomous_trading_enabled = True
        if case.failure_mode == "risk_reject": runtime.risk.trading_enabled = False
        if case.failure_mode == "emergency_stop": runtime.risk.activate_kill_switch()
        for tick in ticks(instrument, published + __import__('datetime').timedelta(minutes=1), case.price_scenario): await runtime.handle_tick(tick)
        await runtime.bus.drain()
    candidates = [x for x in events["signal"] if x.strategy == "news_thesis_confirmation"]
    states = ([thesis.state.value] if thesis else []) + [x.current.value for x in events["lifecycle"]]
    broker_orders = len(runtime.broker.orders); fills = len(runtime.broker.fills); trades = len(runtime.trades.completed)
    risk_approvals = sum(x.approved for x in events["risk"]); risk_rejections = sum(not x.approved for x in events["risk"])
    thesis_expected = (case.fixture_response is not None or case.fixture_analysis is not None) and case.failure_mode not in {"timeout", "offline", "malformed", "wrong_schema", "score_out_of_range"} and not unsupported
    persisted = runtime.database.count("news_events") > before_news and (runtime.database.count("market_theses") > before_theses if thesis_expected else True)
    lifecycle_ok = case.expectation.candidate_expected is None or bool(candidates) == case.expectation.candidate_expected
    invariants = [
        {"name": "external_broker_absent", "passed": not runtime.external_broker, "detail": "internal PaperBroker only"},
        {"name": "external_orders_zero", "passed": True, "detail": "no external adapter is constructible by this runner"},
        {"name": "unknown_symbols_blocked", "passed": not unsupported or thesis is None, "detail": unsupported},
        {"name": "ai_cannot_submit_order", "passed": not broker_orders or bool(candidates), "detail": f"internal orders={broker_orders}"},
        {"name": "execution_disarmed_by_default", "passed": case.failure_mode in {"risk_reject", "emergency_stop"} or not runtime.autonomous_trading_enabled, "detail": "only explicit internal rejection cases arm execution"},
        {"name": "persistence", "passed": persisted, "detail": "isolated experiment database"},
        {"name": "lifecycle_expectation", "passed": lifecycle_ok, "detail": f"expected candidate={case.expectation.candidate_expected}; actual={bool(candidates)}"},
        {"name": "duplicate_deduplicated", "passed": duplicate_rejected is not False, "detail": duplicate_rejected},
    ]
    if case.failure_mode in {"risk_reject", "emergency_stop", "feed_unhealthy"}: invariants.append({"name": f"{case.failure_mode}_blocks_order", "passed": broker_orders == 0, "detail": f"orders={broker_orders}"})
    if case.category == "safety": invariants.append({"name": "untrusted_news_no_privilege", "passed": broker_orders == 0 and not runtime.risk.kill_switch if case.failure_mode != "emergency_stop" else broker_orders == 0, "detail": "article text cannot mutate risk or issue orders"})
    latest_ai = events["ai"][-1] if events["ai"] else None
    ai_payload = {"provider": getattr(capture, "provider_name", ai_mode), "model": config.ai.model, "available": bool(thesis) if not latest_ai else latest_ai.available, "error": validation_error, "proposed_instruments": [x.get("instrument") for x in raw_assets], "unsupported_instruments": unsupported, "affected_instruments": assets, "importance": thesis.importance if thesis else raw.get("importance") if isinstance(raw, dict) else None, "confidence": thesis.confidence if thesis else raw.get("confidence") if isinstance(raw, dict) else None, "reasoning_summary": thesis.reasoning_summary if thesis else None, "warnings": list(thesis.risk_warnings) if thesis else [], "invalidation_conditions": list(thesis.invalidation_conditions) if thesis else [], "prompt_hash": thesis.prompt_hash if thesis else latest_ai.prompt_hash if latest_ai else None, "context_hash": thesis.context_hash if thesis else latest_ai.context_hash if latest_ai else None, "raw_response": raw}
    result = ExperimentResult(case.case_id, case.category, case.headline, asdict(case.expectation), ai_payload, semantic, {"passed": all(x["passed"] for x in invariants), "accepted": accepted, "persisted": persisted, "thesis_expected": thesis_expected, "lifecycle_ok": lifecycle_ok, "validation_error": validation_error}, {"thesis_id": thesis.thesis_id if thesis else None, "states": states, "confirmation_reasons": [x.reason for x in events["lifecycle"]], "duplicate_rejected": duplicate_rejected}, {"scenario": case.price_scenario, "forward_returns": forward_returns(prices)}, {"execution_armed": runtime.autonomous_trading_enabled, "confirmation_candidates": len(candidates), "risk_approvals": risk_approvals, "risk_rejections": risk_rejections, "broker_orders": broker_orders, "external_orders": 0, "fills": fills, "trades": trades}, {"request_timestamp": capture.started.isoformat() if capture.started else None, "response_timestamp": capture.ended.isoformat() if capture.ended else None, "latency_ms": getattr(capture, "latency_ms", thesis.latency_ms if thesis else None)}, invariants).to_dict()
    await runtime.stop(); runtime.database.close()
    return result


def run_news_intelligence(config: AppConfig, suite="standard", ai_mode="fixture", case_id=None, category=None, limit=None, seed=17, report_path: Path | None=None, formats="both", include_price=True, start_ai=False, resume: Path | None=None, progress: Callable[[str], None] | None=print) -> dict:
    if ai_mode not in {"fixture", "local", "external"}: raise ExperimentError("--ai must be fixture, local, or external")
    cases = load_cases(suite, case_id, category, limit)
    run_id = uuid4().hex[:20]; directory = resume or report_path or Path("data/experiments") / run_id
    if directory.suffix in {".json", ".md"}: raise ExperimentError("--report must name an output directory")
    directory.mkdir(parents=True, exist_ok=True); database_path = directory / "experiment.db"
    completed_path = directory / "cases.jsonl"; completed = {}
    if resume and completed_path.exists():
        for line in completed_path.read_text(encoding="utf-8").splitlines():
            if line.strip(): row=json.loads(line);completed[row["case_id"]]=row
    shared_client = None; manager = None; started_here = False
    if ai_mode in {"local", "external"}:
        if ai_mode == "external" and config.ai.provider in {"managed_local", "local_llama_cpp", "llama_cpp"}: raise ExperimentError("External AI mode requires an external OpenAI-compatible provider in config")
        if ai_mode == "local":
            from feline.intelligence.assets import LocalAIAssets
            from feline.intelligence.operations import LocalAIProcessManager, ai_health
            status = LocalAIAssets(config.ai).status()
            if status["runtime_state"] != "INSTALLED" or status["model_state"] != "INSTALLED": raise ExperimentError("Local AI is not installed. Run: python3 -m feline ai install")
            manager = LocalAIProcessManager(); health = ai_health(config.ai)
            if health["endpoint_state"] != "AVAILABLE":
                if not start_ai: raise ExperimentError("Local AI is not running. Re-run with --start-ai or run: python3 -m feline ai start-local")
                state = manager.start(config.ai); started_here = state.get("state") == "STARTING"
                ready = manager.wait_until_ready(config.ai)
                if ready.get("state") != "AVAILABLE": raise ExperimentError(f"Local AI failed to become available: {ready.get('state')} ({ready.get('message','no detail')})")
        shared_client = LlamaCppClient(config.ai)
    started_at = datetime.now(timezone.utc); results = []
    try:
        for index, case in enumerate(cases, 1):
            if case.case_id in completed: results.append(completed[case.case_id]); continue
            if progress: progress(f"[{index}/{len(cases)}] {case.case_id} ...")
            row = asyncio.run(_run_case(case, config, database_path, ai_mode, shared_client, include_price))
            results.append(row)
            with completed_path.open("a", encoding="utf-8") as handle: handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            if progress: progress(f"  AI: {row['timings'].get('latency_ms', 0):.1f}ms  semantic: {row['semantic']['category'].upper()}  safety: {'PASS' if row['engineering']['passed'] else 'FAIL'}")
    finally:
        if started_here and manager: manager.stop()
    summary = summarize(results); ended_at = datetime.now(timezone.utc)
    report = {"schema_version": "news-intelligence-experiment-v1", "metadata": {"experiment_id": run_id, "suite": suite, "started_at": started_at.isoformat(), "ended_at": ended_at.isoformat(), "feline_version": __version__, "git_commit": _git_commit(), "seed": seed, "ai_provider": ai_mode, "model": "fixture/news-market-impact-v1" if ai_mode == "fixture" else config.ai.model, "temperature": 0 if ai_mode == "fixture" else config.ai.temperature, "execution_mode": "internal_paper_disarmed", "instrument_universe_mode": "deterministic_case_fixture", "database": str(database_path), "price_scenarios": include_price}, "summary": summary, "cases": results}
    report["substantive_digest"] = _substantive_digest(results, summary)
    if not _safe_report_payload(report): raise ExperimentError("Secret-like material detected in experiment report")
    report["outputs"] = write_reports(report, directory, formats)
    return report
