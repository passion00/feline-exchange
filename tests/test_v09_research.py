from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import threading

import tempfile
import unittest

from feline.config import AIConfig, AppConfig
from feline.gui.controller import WorkstationController
from feline.research.catalog import load_manifest
from feline.research.engine import run_experiment, validate_manifest
from feline.research.episodes import build_episode, chronological_splits, horizon_contamination
from feline.research.registry import inspect_dataset
from feline.research.statistics import aggregate_results, bootstrap_interval, group_results
from feline.storage.database import Database

FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST = FIXTURES / "research" / "manifest.json"


def config(tmp_path: Path) -> AppConfig:
    return replace(AppConfig(), database_path=tmp_path / "research.db", ai=AIConfig(enabled=False))


class HistoricalResearchTests(unittest.TestCase):
 def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.tmp_path = Path(self.temp.name)
 def tearDown(self): self.temp.cleanup()

 def test_catalog_manifest_and_duplicate_validation(self):
    tmp_path=self.tmp_path
    manifest = load_manifest(MANIFEST)
    assert len(manifest.entries) == 6
    assert manifest.entries[1].secondary_events[0].relationship == "follow_up"
    data = json.loads(MANIFEST.read_text())
    data["events"].append(data["events"][0])
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(data))
    with self.assertRaisesRegex(ValueError, "duplicate event_id"): load_manifest(path)


 def test_dataset_registry_checksum_and_quality(self):
    entry = load_manifest(MANIFEST).entries[0]
    first = inspect_dataset(entry.dataset_path, "EURUSD")
    second = inspect_dataset(entry.dataset_path, "EURUSD")
    assert first.checksum == second.checksum
    assert first.instrument == "EURUSD"
    assert first.first_timestamp < first.last_timestamp
    with self.assertRaisesRegex(ValueError, "wrong instrument"): inspect_dataset(entry.dataset_path, "GBPUSD")


 def test_episode_coverage_secondary_and_splits(self):
    manifest = load_manifest(MANIFEST)
    episodes = [build_episode(x) for x in manifest.entries]
    assert episodes[-1].excluded and "missing_post_event" in episodes[-1].quality_flags
    contaminated = horizon_contamination(episodes[1].entry, 5, "flag")
    assert contaminated["status"] == "contains_secondary_event" and contaminated["use_in_aggregate"]
    assert not horizon_contamination(episodes[1].entry, 5, "censor")["use_in_aggregate"]
    splits = chronological_splits(episodes, manifest.split)
    ordered = sorted(episodes, key=lambda x: x.entry.event.scheduled_timestamp)
    assert splits[ordered[0].episode_id] == "TRAIN"
    assert splits[ordered[-1].episode_id] == "TEST"


 def test_aggregate_baseline_bootstrap_and_groups(self):
    rows = [{"status": "included", "central_bank": "FOMC", "strategy_outcome": "NO_TRADE", "direction": "neutral", "shock_magnitude": .01, "no_trade_reason": "insufficient_move", "missed_move_candidate": True, "split": "TEST", "horizons": {"5": {"return_value": .02, "mae": -.002, "mfe": .022, "contamination_status": "clean", "use_in_aggregate": True, "initial_shock_relation": "continuation"}}}]
    aggregate = aggregate_results(rows, 40, 7)
    assert aggregate["counts"]["FOMC"] == 1
    assert aggregate["horizons"]["5"]["initial_shock_baseline"]["continuation_rate"] == 1
    assert aggregate["missed_move_candidates"] == 1
    assert bootstrap_interval([1, 2, 3], samples=50, seed=9) == bootstrap_interval([1, 2, 3], samples=50, seed=9)
    assert "TEST" in group_results(rows, ["split"])


 def test_batch_research_exports_persistence_and_reproducibility(self):
    tmp_path=self.tmp_path
    cfg = config(tmp_path)
    first = run_experiment(MANIFEST, cfg, tmp_path / "reports-a")
    second = run_experiment(MANIFEST, cfg, tmp_path / "reports-b")
    assert first["aggregate"]["counts"] == {"total": 6, "included": 5, "excluded": 1, "FOMC": 3, "ECB": 2}
    assert first["aggregate"]["strategy_outcomes"] == {"CONTINUATION": 2, "MEAN_REVERSION": 2, "NO_TRADE": 1}
    comparable = lambda result: [(x["event_id"], x.get("strategy_outcome"), {k: v.get("return_value") for k, v in x.get("horizons", {}).items()}) for x in result["results"]]
    assert comparable(first) == comparable(second)
    out = Path(first["output_directory"])
    assert {"experiment.json", "summary.md", "events.csv", "horizons.csv", "exclusions.csv"} <= {x.name for x in out.iterdir()}
    assert "Classification statistics do not establish profitability" in (out / "summary.md").read_text()
    db = Database(cfg.database_path)
    assert db.count("research_experiments") == 2
    assert db.count("research_episodes") == 12
    assert db.integrity_report()["sqlite"] == "ok"
    db.close()


 def test_batch_progress_cancel_and_gui_projection(self):
    tmp_path=self.tmp_path
    cfg = config(tmp_path)
    cancel = threading.Event()
    progress = []
    def update(row):
        progress.append(row)
        if row.get("completed") == 1:
            cancel.set()
    result = run_experiment(MANIFEST, cfg, tmp_path / "cancelled", progress=update, cancel_event=cancel)
    assert result["experiment"]["status"] == "cancelled"
    assert result["results"]
    controller = WorkstationController(config(tmp_path / "gui"))
    future = controller.start_research(MANIFEST, tmp_path / "gui-reports")
    future.result(timeout=20)
    assert any(x["kind"] == "research" and x.get("state") == "completed" for x in controller.drain(500))
    controller.shutdown()


 def test_validation_is_explicit_and_no_lookahead(self):
    result = validate_manifest(MANIFEST)
    assert not result["valid"]
    assert result["excluded"][0]["event_id"] == "insufficient"
    # Native candles in the fixture are exposed at their close timestamp; no horizon
    # exists until the controller reaches that historical time.
    event = load_manifest(MANIFEST).entries[0].event
    assert event.scheduled_timestamp == datetime(2026, 3, 1, 19, 0, tzinfo=timezone.utc)
