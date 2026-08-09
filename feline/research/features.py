from __future__ import annotations

import csv
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from feline import __version__
from feline.core.events import CandleUpdate
from feline.replay.mixed import read_mixed_events
from feline.replay.session_report import file_checksum

FEATURE_ENGINE_VERSION = "1.0"


class FeaturePhase(str, Enum):
    PRE_EVENT = "PRE_EVENT"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    STABILIZATION = "STABILIZATION"
    OUTCOME = "OUTCOME"


class LookaheadError(ValueError):
    """Raised when a predictor is not observable at its requested snapshot."""


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    phase: FeaturePhase
    description: str
    units: str
    availability_rule: str
    predictor: bool = True
    label: bool = False
    implementation_version: str = FEATURE_ENGINE_VERSION

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["phase"] = self.phase.value
        result["predictor_or_label"] = "label" if self.label else "predictor"
        result["future_outcome"] = self.label
        return result


@dataclass(frozen=True)
class FeatureValue:
    definition: FeatureDefinition
    value: Any
    available_at: datetime | None


@dataclass
class FeatureSnapshot:
    phase: FeaturePhase
    as_of: datetime
    values: dict[str, FeatureValue]

    def __init__(self, phase: FeaturePhase, as_of: datetime):
        self.phase, self.as_of, self.values = phase, as_of, {}

    def add(self, value: FeatureValue) -> None:
        if value.definition.label:
            raise ValueError("outcome labels do not belong in predictor snapshots")
        if value.available_at is not None and value.available_at > self.as_of:
            raise LookaheadError(
                f"{value.definition.name} available at {value.available_at.isoformat()} "
                f"after snapshot {self.as_of.isoformat()}"
            )
        self.values[value.definition.name] = value


def predictors_valid_as_of(values: Iterable[FeatureValue], as_of: datetime) -> dict[str, Any]:
    """Return observable predictors, rejecting any future-dated value."""
    result: dict[str, Any] = {}
    for value in values:
        if value.definition.label:
            continue
        if value.available_at is not None and value.available_at > as_of:
            raise LookaheadError(f"{value.definition.name} is not available as of {as_of.isoformat()}")
        result[value.definition.name] = value.value
    return result


def predictor_columns(definitions: Iterable[FeatureDefinition]) -> list[str]:
    return [item.name for item in definitions if item.predictor and not item.label]


def label_columns(definitions: Iterable[FeatureDefinition]) -> list[str]:
    return [item.name for item in definitions if item.label]


def _definition(name: str, phase: FeaturePhase, description: str, units: str, rule: str,
                *, label: bool = False) -> FeatureDefinition:
    return FeatureDefinition(name, phase, description, units, rule, not label, label)


FEATURE_DEFINITIONS: tuple[FeatureDefinition, ...] = (
    *(_definition(f"pre_return_{m}m", FeaturePhase.PRE_EVENT,
                  f"Close return over the {m} minutes ending at announcement.", "fraction",
                  "completed candles with close_time <= announcement") for m in (5, 15, 30, 60)),
    *(_definition(f"pre_realized_vol_{m}m", FeaturePhase.PRE_EVENT,
                  f"Population standard deviation of one-minute close returns in the prior {m} minutes.", "fraction",
                  "completed candles with close_time <= announcement") for m in (15, 30, 60)),
    *(_definition(f"pre_range_{m}m", FeaturePhase.PRE_EVENT,
                  f"(maximum high - minimum low) / announcement reference close over prior {m} minutes.", "fraction",
                  "completed candles with close_time <= announcement") for m in (15, 30, 60)),
    _definition("pre_last_body_fraction", FeaturePhase.PRE_EVENT, "Absolute body / candle range of last completed pre-event candle.", "fraction", "last candle close_time <= announcement"),
    _definition("pre_last_upper_wick_fraction", FeaturePhase.PRE_EVENT, "Upper wick / candle range of last completed pre-event candle.", "fraction", "last candle close_time <= announcement"),
    _definition("pre_last_lower_wick_fraction", FeaturePhase.PRE_EVENT, "Lower wick / candle range of last completed pre-event candle.", "fraction", "last candle close_time <= announcement"),
    _definition("pre_last_range_fraction", FeaturePhase.PRE_EVENT, "Candle high-low range / pre-event reference close.", "fraction", "last candle close_time <= announcement"),
    _definition("shock_direction", FeaturePhase.ANNOUNCEMENT, "Existing research initial-shock direction.", "category", "existing shock observation is complete"),
    _definition("shock_return", FeaturePhase.ANNOUNCEMENT, "Existing announcement shock signed return.", "fraction", "existing shock observation is complete"),
    _definition("shock_magnitude", FeaturePhase.ANNOUNCEMENT, "Absolute existing announcement shock return.", "fraction", "existing shock observation is complete"),
    _definition("shock_velocity", FeaturePhase.ANNOUNCEMENT, "Existing shock return divided by elapsed observation minutes.", "fraction/minute", "existing shock observation is complete"),
    _definition("shock_reference_price", FeaturePhase.ANNOUNCEMENT, "Last completed pre-event close used as shock reference.", "price", "existing shock observation is complete"),
    _definition("shock_observation_timestamp", FeaturePhase.ANNOUNCEMENT, "Completion time of the shock observation.", "timestamp", "existing shock observation is complete"),
    _definition("announcement_candle_body_fraction", FeaturePhase.ANNOUNCEMENT, "First completed post-announcement candle absolute body / range.", "fraction", "first post-announcement candle is complete"),
    _definition("announcement_candle_upper_wick_fraction", FeaturePhase.ANNOUNCEMENT, "First completed post-announcement candle upper wick / range.", "fraction", "first post-announcement candle is complete"),
    _definition("announcement_candle_lower_wick_fraction", FeaturePhase.ANNOUNCEMENT, "First completed post-announcement candle lower wick / range.", "fraction", "first post-announcement candle is complete"),
    _definition("announcement_candle_range_fraction", FeaturePhase.ANNOUNCEMENT, "First post-announcement candle range / shock reference price.", "fraction", "first post-announcement candle is complete"),
    _definition("stabilization_seconds", FeaturePhase.STABILIZATION, "Elapsed seconds from announcement to deterministic stabilization.", "seconds", "stabilization transition has occurred"),
    _definition("stabilization_price", FeaturePhase.STABILIZATION, "Completed-candle reference price at stabilization.", "price", "stabilization transition has occurred"),
    _definition("stabilization_return_from_pre_event", FeaturePhase.STABILIZATION, "Stabilization reference / pre-event reference - 1.", "fraction", "stabilization transition has occurred"),
    _definition("stabilization_retracement_fraction", FeaturePhase.STABILIZATION, "Existing v0.9.1 retracement definition.", "fraction", "stabilization transition has occurred"),
    _definition("stabilization_impulse_retention_fraction", FeaturePhase.STABILIZATION, "Existing v0.9.1 impulse-retention definition.", "fraction", "stabilization transition has occurred"),
    _definition("decision_post_move", FeaturePhase.STABILIZATION, "Structured macro strategy post-move gate observation.", "fraction", "strategy evaluation diagnostics are available"),
    _definition("decision_initial_move", FeaturePhase.STABILIZATION, "Structured macro strategy initial-move gate observation.", "fraction", "strategy evaluation diagnostics are available"),
    _definition("decision_spread", FeaturePhase.STABILIZATION, "Structured macro strategy spread gate observation.", "fraction", "strategy evaluation diagnostics are available"),
    _definition("stabilization_state_at_decision", FeaturePhase.STABILIZATION, "Structured stabilization gate observation.", "category", "strategy evaluation diagnostics are available"),
    _definition("volatility_decay_ratio", FeaturePhase.STABILIZATION, "Population volatility of last 3 completed returns before stabilization divided by population volatility of completed returns from announcement through first 3 post-event minutes.", "ratio", "only candles complete at/before stabilization; null for zero denominator"),
    _definition("pre_stabilization_range_3m", FeaturePhase.STABILIZATION, "High-low range over final 3 minutes ending at stabilization / stabilization price.", "fraction", "only candles complete at/before stabilization"),
    _definition("label_post_stabilization_5m_return", FeaturePhase.OUTCOME, "Clean existing stabilization-to-5m return.", "fraction", "5 minutes after stabilization has elapsed and interval is clean", label=True),
    _definition("label_post_stabilization_15m_return", FeaturePhase.OUTCOME, "Clean existing stabilization-to-15m return.", "fraction", "15 minutes after stabilization has elapsed and interval is clean", label=True),
    _definition("label_direction_normalized_5m_return", FeaturePhase.OUTCOME, "Clean +5m return multiplied by sign(initial shock).", "fraction", "clean +5m outcome is complete", label=True),
    _definition("label_direction_normalized_15m_return", FeaturePhase.OUTCOME, "Clean +15m return multiplied by sign(initial shock).", "fraction", "clean +15m outcome is complete", label=True),
    _definition("label_post_stabilization_classification", FeaturePhase.OUTCOME, "Existing v0.9.1 post-stabilization descriptive classification.", "category", "existing configured classification horizon is complete", label=True),
)


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _number(value: Any) -> float | None:
    if value in (None, "", "null", "None"):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def candle_shape(candle: CandleUpdate, reference: float | None) -> tuple[float, float, float, float | None]:
    width = candle.high - candle.low
    if width <= 0:
        return 0.0, 0.0, 0.0, 0.0 if reference else None
    return (abs(candle.close - candle.open) / width,
            (candle.high - max(candle.open, candle.close)) / width,
            (min(candle.open, candle.close) - candle.low) / width,
            width / reference if reference else None)


def realized_volatility(candles: list[CandleUpdate]) -> float | None:
    returns = [candles[i].close / candles[i - 1].close - 1 for i in range(1, len(candles)) if candles[i - 1].close]
    if not returns:
        return None
    center = mean(returns)
    return math.sqrt(sum((value - center) ** 2 for value in returns) / len(returns))


def _window(candles: list[CandleUpdate], end: datetime, minutes: int) -> list[CandleUpdate]:
    start = end - timedelta(minutes=minutes)
    return [item for item in candles if start <= item.close_time <= end]


def _diagnostics(value: str) -> dict[str, Any]:
    try:
        rows = json.loads(value or "[]")
        return {str(row.get("gate")): row.get("observed_value") for row in rows if isinstance(row, dict)}
    except (json.JSONDecodeError, TypeError):
        return {}


def _quality_for_event(manifest_path: Path, event_id: str) -> str:
    quality_path = manifest_path.parent.parent.parent / "historical" / "processed" / f"fomc_{event_id.split('-')[1]}" / "data_quality.json"
    if not quality_path.exists():
        return "legacy_quality_unknown"
    try:
        data = json.loads(quality_path.read_text(encoding="utf-8"))
        rows = data.get("events", {})
        row = rows.get(event_id) if isinstance(rows, dict) else next((x for x in rows if x.get("event_id") == event_id), None)
        return (row or {}).get("quality_classification", (row or {}).get("classification", "legacy_quality_unknown"))
    except (json.JSONDecodeError, TypeError):
        return "legacy_quality_unknown"


def _extract_event(event: dict[str, str], horizons: list[dict[str, str]], manifest_entry: dict[str, Any],
                   manifest_path: Path, experiment_id: str) -> dict[str, Any]:
    announcement = _parse_time(event.get("primary_timestamp"))
    if announcement is None:
        raise ValueError(f"event {event.get('event_id')} has no primary timestamp")
    dataset = (manifest_path.parent / manifest_entry["dataset_path"]).resolve()
    candles = [item for item in read_mixed_events(dataset) if isinstance(item, CandleUpdate) and item.timeframe == "1m" and item.complete]
    candles.sort(key=lambda item: item.close_time)
    pre = [item for item in candles if item.close_time <= announcement]
    post = [item for item in candles if item.close_time > announcement]
    stable_time = _parse_time(event.get("stabilization_time"))
    shock_time = _parse_time(event.get("shock_detection_time")) or (post[0].close_time if post else None)
    pre_ref = pre[-1].close if pre else None
    shock_return = _number(event.get("shock_magnitude"))
    row: dict[str, Any] = {
        "event_id": event["event_id"], "experiment_id": experiment_id,
        "year": int(event.get("year") or announcement.year), "split": event.get("split"),
        "central_bank": event.get("central_bank"), "instrument": event.get("instrument"),
        "announcement_timestamp": announcement.isoformat(),
        "stabilization_timestamp": stable_time.isoformat() if stable_time else None,
        "strategy_outcome": event.get("strategy_outcome"), "no_trade_reason": event.get("no_trade_reason") or None,
        "data_quality_status": _quality_for_event(manifest_path, event["event_id"]),
        "quality_flags": event.get("quality_flags"),
    }
    pre_snapshot = FeatureSnapshot(FeaturePhase.PRE_EVENT, announcement)
    definitions = {item.name: item for item in FEATURE_DEFINITIONS}
    for minutes in (5, 15, 30, 60):
        values = _window(candles, announcement, minutes)
        value = values[-1].close / values[0].close - 1 if len(values) >= 2 and values[0].close else None
        pre_snapshot.add(FeatureValue(definitions[f"pre_return_{minutes}m"], value, values[-1].close_time if values else None))
    for minutes in (15, 30, 60):
        values = _window(candles, announcement, minutes)
        pre_snapshot.add(FeatureValue(definitions[f"pre_realized_vol_{minutes}m"], realized_volatility(values), values[-1].close_time if values else None))
        price_range = (max(x.high for x in values) - min(x.low for x in values)) / pre_ref if values and pre_ref else None
        pre_snapshot.add(FeatureValue(definitions[f"pre_range_{minutes}m"], price_range, values[-1].close_time if values else None))
    shape = candle_shape(pre[-1], pre_ref) if pre else (None,) * 4
    for name, value in zip(("pre_last_body_fraction", "pre_last_upper_wick_fraction", "pre_last_lower_wick_fraction", "pre_last_range_fraction"), shape):
        pre_snapshot.add(FeatureValue(definitions[name], value, pre[-1].close_time if pre else None))
    row.update({name: value.value for name, value in pre_snapshot.values.items()})

    if shock_time:
        announcement_snapshot = FeatureSnapshot(FeaturePhase.ANNOUNCEMENT, shock_time)
        signed_shock = shock_return
        elapsed = max((shock_time - announcement).total_seconds() / 60, 1.0)
        values = {
            "shock_direction": "up" if (signed_shock or 0) > 0 else "down" if (signed_shock or 0) < 0 else "neutral",
            "shock_return": signed_shock, "shock_magnitude": abs(signed_shock) if signed_shock is not None else None,
            "shock_velocity": signed_shock / elapsed if signed_shock is not None else None,
            "shock_reference_price": pre_ref, "shock_observation_timestamp": shock_time.isoformat(),
        }
        first = next((item for item in post if item.close_time <= shock_time), post[0] if post and post[0].close_time == shock_time else None)
        announcement_shape = candle_shape(first, pre_ref) if first else (None,) * 4
        values.update(dict(zip(("announcement_candle_body_fraction", "announcement_candle_upper_wick_fraction", "announcement_candle_lower_wick_fraction", "announcement_candle_range_fraction"), announcement_shape)))
        for name, value in values.items():
            announcement_snapshot.add(FeatureValue(definitions[name], value, shock_time))
        row.update({name: value.value for name, value in announcement_snapshot.values.items()})
    else:
        row.update({item.name: None for item in FEATURE_DEFINITIONS if item.phase == FeaturePhase.ANNOUNCEMENT})

    if stable_time:
        stable_snapshot = FeatureSnapshot(FeaturePhase.STABILIZATION, stable_time)
        stable_candles = [item for item in candles if item.close_time <= stable_time]
        stable_price = _number(event.get("stabilization_price")) or (stable_candles[-1].close if stable_candles else None)
        diagnostics = _diagnostics(event.get("decision_diagnostics", ""))
        initial_period = [item for item in candles if announcement <= item.close_time <= min(stable_time, announcement + timedelta(minutes=3))]
        recent_period = _window(candles, stable_time, 3)
        initial_vol, recent_vol = realized_volatility(initial_period), realized_volatility(recent_period)
        decay = recent_vol / initial_vol if initial_vol not in (None, 0) and recent_vol is not None else None
        stable_range = ((max(x.high for x in recent_period) - min(x.low for x in recent_period)) / stable_price
                        if recent_period and stable_price else None)
        values = {
            "stabilization_seconds": _number(event.get("stabilization_duration_seconds")),
            "stabilization_price": stable_price,
            "stabilization_return_from_pre_event": stable_price / pre_ref - 1 if stable_price and pre_ref else None,
            "stabilization_retracement_fraction": _number(event.get("retracement_fraction")),
            "stabilization_impulse_retention_fraction": _number(event.get("impulse_retention_fraction")),
            "decision_post_move": _number(diagnostics.get("post_move")),
            "decision_initial_move": _number(diagnostics.get("initial_move")),
            "decision_spread": _number(diagnostics.get("spread")),
            "stabilization_state_at_decision": diagnostics.get("stabilization"),
            "volatility_decay_ratio": decay,
            "pre_stabilization_range_3m": stable_range,
        }
        for name, value in values.items():
            stable_snapshot.add(FeatureValue(definitions[name], value, stable_time))
        row.update({name: value.value for name, value in stable_snapshot.values.items()})
    else:
        row.update({item.name: None for item in FEATURE_DEFINITIONS if item.phase == FeaturePhase.STABILIZATION})

    horizon_map = {(item.get("reference_basis"), int(item["horizon_minutes"])): item for item in horizons}
    direction = 1 if (shock_return or 0) > 0 else -1 if (shock_return or 0) < 0 else 0
    for minutes in (5, 15):
        item = horizon_map.get(("stabilization", minutes))
        clean = bool(item and item.get("contamination_status", "clean") == "clean" and stable_time)
        value = _number(item.get("return_value")) if clean else None
        row[f"label_post_stabilization_{minutes}m_return"] = value
        row[f"label_direction_normalized_{minutes}m_return"] = value * direction if value is not None and direction else None
        row[f"label_post_stabilization_{minutes}m_contamination"] = item.get("contamination_status") if item else None
    row["label_post_stabilization_classification"] = event.get("post_stabilization_outcome") or ("NO_STABILIZATION" if not stable_time else None)
    return row


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=2, check=True).stdout.strip()
    except Exception:
        return "unknown"


def _artifact_checksum(directory: Path) -> str:
    digest = sha256()
    for name in ("experiment.json", "events.csv", "horizons.csv"):
        path = directory / name
        digest.update(name.encode()); digest.update(path.read_bytes())
    return digest.hexdigest()


def build_feature_set(experiment_directories: Iterable[Path], output_root: Path = Path("data/reports/features")) -> dict[str, Any]:
    directories = [Path(item).resolve() for item in experiment_directories]
    if not directories:
        raise ValueError("at least one experiment directory is required")
    inputs, rows = [], []
    for directory in directories:
        experiment = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
        manifest_path = Path(experiment["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = {item["event_id"]: item for item in manifest["events"]}
        with (directory / "events.csv").open(newline="", encoding="utf-8") as handle:
            events = list(csv.DictReader(handle))
        with (directory / "horizons.csv").open(newline="", encoding="utf-8") as handle:
            horizons = list(csv.DictReader(handle))
        experiment_id = experiment["experiment_id"]
        for event in events:
            event_id = event["event_id"]
            if event_id not in entries:
                raise ValueError(f"{event_id} is absent from manifest {manifest_path}")
            rows.append(_extract_event(event, [item for item in horizons if item["event_id"] == event_id], entries[event_id], manifest_path, experiment_id))
        inputs.append({"experiment_id": experiment_id, "path": str(directory), "artifact_checksum": _artifact_checksum(directory), "schema_version": experiment.get("schema_version")})
    if len({(row["experiment_id"], row["event_id"]) for row in rows}) != len(rows):
        raise ValueError("duplicate experiment/event identity")
    identity = {"feline_version": __version__, "git_commit": _git_commit(), "feature_engine_version": FEATURE_ENGINE_VERSION,
                "inputs": sorted(inputs, key=lambda item: item["experiment_id"]),
                "feature_definition_checksum": sha256(json.dumps([item.to_dict() for item in FEATURE_DEFINITIONS], sort_keys=True).encode()).hexdigest()}
    feature_set_id = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    directory = output_root / feature_set_id
    directory.mkdir(parents=True, exist_ok=True)
    columns = ["event_id", "experiment_id", "year", "split", "central_bank", "instrument", "announcement_timestamp", "stabilization_timestamp", "strategy_outcome", "no_trade_reason", "data_quality_status", "quality_flags"]
    columns += predictor_columns(FEATURE_DEFINITIONS) + label_columns(FEATURE_DEFINITIONS)
    columns += ["label_post_stabilization_5m_contamination", "label_post_stabilization_15m_contamination"]
    with (directory / "features.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader()
        for row in sorted(rows, key=lambda item: (item["announcement_timestamp"], item["experiment_id"], item["event_id"])):
            writer.writerow({name: row.get(name) for name in columns})
    schema = {"schema_version": "1.0", "feature_engine_version": FEATURE_ENGINE_VERSION,
              "identity_columns": columns[:12], "features": [item.to_dict() for item in FEATURE_DEFINITIONS],
              "predictor_columns": predictor_columns(FEATURE_DEFINITIONS), "label_columns": label_columns(FEATURE_DEFINITIONS)}
    (directory / "feature_schema.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    provenance = {**identity, "feature_set_id": feature_set_id, "events": len(rows), "output_directory": str(directory)}
    (directory / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    analysis = analyze_features(directory / "features.csv")
    return {"feature_set_id": feature_set_id, "events": len(rows), "output_directory": str(directory), "feature_report": analysis["report"]}


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1]); result = [0.0] * len(values); index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]: end += 1
        rank = (index + end - 1) / 2 + 1
        for position in range(index, end): result[ordered[position][0]] = rank
        index = end
    return result


def correlation(left: list[float], right: list[float], *, spearman: bool = False) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    if spearman:
        left, right = _rank(left), _rank(right)
    lx, rx = mean(left), mean(right)
    numerator = sum((x - lx) * (y - rx) for x, y in zip(left, right))
    denominator = math.sqrt(sum((x - lx) ** 2 for x in left) * sum((y - rx) ** 2 for y in right))
    return numerator / denominator if denominator else None


def analyze_features(features_path: Path) -> dict[str, Any]:
    features_path = Path(features_path)
    directory = features_path.parent
    with features_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    schema = json.loads((directory / "feature_schema.json").read_text(encoding="utf-8"))
    predictors = schema["predictor_columns"]
    numeric = [name for name in predictors if next(item for item in schema["features"] if item["name"] == name)["units"] not in {"category", "timestamp"}]
    targets = ["label_direction_normalized_5m_return", "label_direction_normalized_15m_return"]
    relationships: dict[str, Any] = {}
    for feature in numeric:
        relationships[feature] = {}
        for target in targets:
            pairs = [(_number(row.get(feature)), _number(row.get(target))) for row in rows]
            pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
            x, y = [item[0] for item in pairs], [item[1] for item in pairs]
            relationships[feature][target] = {"n": len(pairs), "feature_mean": mean(x) if x else None,
                "feature_median": median(x) if x else None, "pearson": correlation(x, y), "spearman": correlation(x, y, spearman=True)}
    outcomes: dict[str, int] = {}
    for row in rows:
        key = row.get("label_post_stabilization_classification") or "UNAVAILABLE"; outcomes[key] = outcomes.get(key, 0) + 1
    missingness = {name: sum(_number(row.get(name)) is None if next((x for x in schema["features"] if x["name"] == name), {}).get("units") not in {"category", "timestamp"} else not row.get(name) for row in rows) for name in predictors}
    subgroup: dict[str, Any] = {}
    buckets: dict[str, Any] = {}
    for feature in ("shock_magnitude", "stabilization_seconds", "pre_realized_vol_30m", "pre_return_30m", "decision_post_move", "volatility_decay_ratio"):
        subgroup[feature] = {}
        for outcome in ("CONTINUATION", "MEAN_REVERSION", "FLAT"):
            values = [_number(row.get(feature)) for row in rows if row.get("label_post_stabilization_classification") == outcome]
            values = [value for value in values if value is not None]
            subgroup[feature][outcome] = {"n": len(values), "mean": mean(values) if values else None, "median": median(values) if values else None}
        values = sorted(value for value in (_number(row.get(feature)) for row in rows) if value is not None)
        if len(values) >= 3:
            lower, upper = values[(len(values) - 1) // 3], values[(2 * (len(values) - 1)) // 3]
            buckets[feature] = {"boundaries": {"low_max": lower, "high_min": upper}, "groups": {}}
            for name, predicate in (("low", lambda x: x <= lower), ("middle", lambda x: lower < x < upper), ("high", lambda x: x >= upper)):
                selected = [row for row in rows if (value := _number(row.get(feature))) is not None and predicate(value)]
                buckets[feature]["groups"][name] = {"n": len(selected), **{
                    target: {"n": len(target_values), "mean": mean(target_values) if target_values else None, "median": median(target_values) if target_values else None}
                    for target in targets
                    for target_values in [[value for value in (_number(row.get(target)) for row in selected) if value is not None]]}}
    summary = {"schema_version": "1.0", "events": len(rows), "years": sorted({int(row["year"]) for row in rows if row.get("year")}),
               "stabilized": sum(bool(row.get("stabilization_timestamp")) for row in rows),
               "no_stabilization": sum(not row.get("stabilization_timestamp") for row in rows),
               "outcome_counts": outcomes, "missingness": missingness, "relationships": relationships,
               "outcome_subgroups": subgroup, "quantile_exploration": buckets,
               "warning": "Descriptive exploratory statistics only: small samples, multiple comparisons, and tiny subgroups do not establish significance or profitability; do not tune thresholds from this report."}
    (directory / "feature_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    sections = []
    for title, feature in (("Shock magnitude", "shock_magnitude"), ("Stabilization time", "stabilization_seconds"),
                           ("Pre-event volatility", "pre_realized_vol_30m"), ("Pre-event directional drift", "pre_return_30m"),
                           ("Decision post-move observation", "decision_post_move"), ("Volatility decay", "volatility_decay_ratio")):
        relation = relationships.get(feature, {})
        sections.append(f"### {title}\n\n- Clean +5m: {relation.get(targets[0])}\n- Clean +15m: {relation.get(targets[1])}\n- Outcome groups: {subgroup.get(feature)}\n")
    report = f"""# Macro event feature report

- Events: {len(rows)}
- Years: {summary['years']}
- Stabilized: {summary['stabilized']}; no stabilization: {summary['no_stabilization']}
- Outcomes: {outcomes}

## Interpretation warning

Correlations and buckets are descriptive and exploratory. Multiple comparisons and small subgroup sizes can be misleading. No relationship here establishes profitability, no threshold should be tuned from this report, and a later year such as 2025 should remain a holdout.

## Predictor relationships with clean direction-normalized outcomes

{''.join(sections)}
### Up-shock vs down-shock

Counts: {dict((key, sum(row.get('shock_direction') == key for row in rows)) for key in ('up','down','neutral'))}. Direction normalization makes positive returns mean continuation in either direction.

### Existing shock buckets

The feature engine preserves the experiments' measurements but does not invent or optimize bucket thresholds. Use event provenance for existing small/medium grouping analysis.

## Missingness

{missingness}

## Descriptive low/middle/high exploration

Boundaries and sample sizes are recorded in `feature_summary.json`. Ties can make groups overlap at a boundary; these descriptive buckets are not trading thresholds.
"""
    report_path = directory / "feature_report.md"
    report_path.write_text(report, encoding="utf-8")
    return {"events": len(rows), "summary": str(directory / "feature_summary.json"), "report": str(report_path)}
