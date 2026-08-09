import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feline.research.features import (
    FEATURE_DEFINITIONS, FeatureDefinition, FeaturePhase, FeatureSnapshot,
    FeatureValue, LookaheadError, analyze_features, build_feature_set,
    candle_shape, correlation, label_columns, predictor_columns, predictors_valid_as_of,
)


UTC = timezone.utc


class FeaturePrimitiveTests(unittest.TestCase):
    def test_future_predictor_is_rejected_in_every_phase(self):
        cutoff = datetime(2024, 1, 1, tzinfo=UTC)
        for phase in (FeaturePhase.PRE_EVENT, FeaturePhase.ANNOUNCEMENT, FeaturePhase.STABILIZATION):
            definition = FeatureDefinition("future", phase, "test", "fraction", "test")
            with self.subTest(phase=phase), self.assertRaises(LookaheadError):
                FeatureSnapshot(phase, cutoff).add(FeatureValue(definition, 1, cutoff + timedelta(seconds=1)))

    def test_labels_are_separate_and_rejected_by_snapshot(self):
        labels = label_columns(FEATURE_DEFINITIONS)
        self.assertIn("label_post_stabilization_5m_return", labels)
        self.assertTrue(set(labels).isdisjoint(predictor_columns(FEATURE_DEFINITIONS)))
        definition = next(item for item in FEATURE_DEFINITIONS if item.label)
        with self.assertRaises(ValueError):
            FeatureSnapshot(FeaturePhase.OUTCOME, datetime.now(UTC)).add(FeatureValue(definition, 1, None))

    def test_valid_predictors_as_of_excludes_labels_and_rejects_future(self):
        cutoff=datetime(2024,1,1,tzinfo=UTC);predictor=next(item for item in FEATURE_DEFINITIONS if item.predictor);label=next(item for item in FEATURE_DEFINITIONS if item.label)
        self.assertEqual(predictors_valid_as_of([FeatureValue(predictor,2,cutoff),FeatureValue(label,9,cutoff)],cutoff),{predictor.name:2})
        with self.assertRaises(LookaheadError):predictors_valid_as_of([FeatureValue(predictor,2,cutoff+timedelta(minutes=1))],cutoff)

    def test_zero_range_candle_shape_is_safe(self):
        from feline.core.events import CandleUpdate
        now = datetime.now(UTC)
        candle = CandleUpdate(timestamp=now, instrument="EURUSD", timeframe="1m", open_time=now-timedelta(minutes=1), close_time=now,
                              open=1.1, high=1.1, low=1.1, close=1.1, volume=0, complete=True)
        self.assertEqual(candle_shape(candle, 1.1), (0.0, 0.0, 0.0, 0.0))

    def test_correlation_small_or_constant_is_safe(self):
        self.assertIsNone(correlation([1, 2], [2, 3]))
        self.assertIsNone(correlation([1, 1, 1], [1, 2, 3]))


def make_experiment(root: Path, experiment_id: str, *, shock: float = .002,
                    stabilization: bool = True, contaminated_5: bool = False,
                    split: str = "TRAIN") -> Path:
    event_time = datetime(2024, 9, 18, 18, 0, tzinfo=UTC)
    dataset = root / f"{experiment_id}.jsonl"
    rows = []
    price = 1.10
    for minute in range(-61, 31):
        close_time = event_time + timedelta(minutes=minute)
        close = price + minute * .00001
        if minute > 0:
            close += shock + minute * .00002
        rows.append({"type":"candle", "timestamp":close_time.isoformat(), "open_time":(close_time-timedelta(minutes=1)).isoformat(),
                     "close_time":close_time.isoformat(), "instrument":"EURUSD", "timeframe":"1m", "open":close-.00002,
                     "high":close+.0001, "low":close-.0001, "close":close, "source":"fixture", "provenance":"native"})
    dataset.write_text("".join(json.dumps(row)+"\n" for row in rows))
    manifest = root / f"{experiment_id}-manifest.json"
    manifest.write_text(json.dumps({"events":[{"event_id":"fomc-2024-09-18", "timestamp":event_time.isoformat(),
        "dataset_path":dataset.name, "instrument":"EURUSD", "central_bank":"FOMC"}]}))
    directory = root / experiment_id; directory.mkdir()
    (directory / "experiment.json").write_text(json.dumps({"schema_version":"1.1", "experiment_id":experiment_id,
        "manifest_path":str(manifest), "feline_version":"0.9.1"}))
    stable_time = event_time + timedelta(minutes=5) if stabilization else None
    event = {"event_id":"fomc-2024-09-18", "central_bank":"FOMC", "event_type":"rate_decision", "instrument":"EURUSD",
        "year":"2024", "split":split, "primary_timestamp":event_time.isoformat(), "strategy_outcome":"NO_TRADE",
        "direction":"neutral", "confidence":"0.2", "no_trade_reason":"insufficient_move",
        "decision_diagnostics":json.dumps([{"gate":"stabilization","observed_value":"stabilized"},
          {"gate":"initial_move","observed_value":abs(shock)}, {"gate":"post_move","observed_value":.0006},
          {"gate":"spread","observed_value":.0002}]), "shock_magnitude":shock,
        "stabilization_time":stable_time.isoformat() if stable_time else "", "stabilization_duration_seconds":"300" if stable_time else "",
        "stabilization_price":"1.1025" if stable_time else "", "post_stabilization_outcome":"CONTINUATION" if stable_time else "NO_STABILIZATION",
        "retracement_fraction":"0.2" if stable_time else "", "impulse_retention_fraction":"0.8" if stable_time else "",
        "quality_flags":"[\"native_ohlc\"]"}
    fields=list(event)
    with (directory/"events.csv").open("w",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerow(event)
    with (directory/"horizons.csv").open("w",newline="") as handle:
        fields=["event_id","split","reference_basis","horizon_minutes","return_value","contamination_status"]
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        if stabilization:
            for minutes,value in ((5,.001),(15,.002)):
                writer.writerow({"event_id":event["event_id"],"split":split,"reference_basis":"stabilization","horizon_minutes":minutes,
                                 "return_value":value,"contamination_status":"contains_secondary_event" if contaminated_5 and minutes==5 else "clean"})
    return directory


class FeatureBuildTests(unittest.TestCase):
    def _rows(self, result):
        with (Path(result["output_directory"])/"features.csv").open() as handle:return list(csv.DictReader(handle))

    def test_build_preserves_identity_split_and_direction_normalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);a=make_experiment(root,"up",shock=.002,split="VALIDATION");b=make_experiment(root,"down",shock=-.002,split="TEST")
            result=build_feature_set([a,b],root/"out");rows=self._rows(result)
            self.assertEqual(len(rows),2);self.assertEqual({r["experiment_id"] for r in rows},{"up","down"})
            self.assertEqual({r["split"] for r in rows},{"VALIDATION","TEST"})
            self.assertAlmostEqual(float(next(r for r in rows if r["experiment_id"]=="up")["label_direction_normalized_5m_return"]),.001)
            self.assertAlmostEqual(float(next(r for r in rows if r["experiment_id"]=="down")["label_direction_normalized_5m_return"]),-.001)
            self.assertAlmostEqual(float(next(r for r in rows if r["experiment_id"]=="down")["label_direction_normalized_15m_return"]),-.002)

    def test_contaminated_label_is_null_and_metadata_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);result=build_feature_set([make_experiment(root,"x",contaminated_5=True)],root/"out");row=self._rows(result)[0]
            self.assertEqual(row["label_post_stabilization_5m_return"],"")
            self.assertEqual(row["label_post_stabilization_5m_contamination"],"contains_secondary_event")
            self.assertNotEqual(row["label_post_stabilization_15m_return"],"")

    def test_no_stabilization_is_retained_with_null_predictors_and_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);result=build_feature_set([make_experiment(root,"x",stabilization=False)],root/"out");row=self._rows(result)[0]
            self.assertEqual(row["stabilization_price"],"");self.assertEqual(row["label_post_stabilization_5m_return"],"")
            self.assertEqual(row["label_post_stabilization_classification"],"NO_STABILIZATION")

    def test_build_is_deterministic_and_schema_marks_roles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);experiment=make_experiment(root,"x");first=build_feature_set([experiment],root/"out");second=build_feature_set([experiment],root/"out")
            self.assertEqual(first["feature_set_id"],second["feature_set_id"])
            schema=json.loads((Path(first["output_directory"])/"feature_schema.json").read_text())
            label=next(item for item in schema["features"] if item["name"]=="label_post_stabilization_5m_return")
            self.assertTrue(label["label"]);self.assertTrue(label["future_outcome"]);self.assertEqual(label["phase"],"OUTCOME")

    def test_pre_features_use_only_candles_complete_at_cutoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);experiment=make_experiment(root,"x");result=build_feature_set([experiment],root/"out");row=self._rows(result)[0]
            # Fixture jumps only after announcement; pre drift remains small.
            self.assertLess(abs(float(row["pre_return_5m"])),.001)
            self.assertEqual(row["shock_observation_timestamp"],"2024-09-18T18:01:00+00:00")

    def test_analysis_handles_legacy_schema_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);result=build_feature_set([make_experiment(root,"x")],root/"out")
            analyzed=analyze_features(Path(result["output_directory"])/"features.csv")
            self.assertEqual(analyzed["events"],1);self.assertTrue(Path(analyzed["summary"]).exists());self.assertTrue(Path(analyzed["report"]).exists())


if __name__ == "__main__":
    unittest.main()
