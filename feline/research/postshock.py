from __future__ import annotations

from datetime import datetime, timedelta


def _timestamp(row: dict) -> datetime:
    return datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))


def _price(row: dict) -> float:
    return float(row.get("close", row.get("price")))


def _at_or_after(rows: list[dict], target: datetime) -> dict | None:
    return next((row for row in rows if _timestamp(row) >= target), None)


def interval_contamination(secondary_events, start: datetime, end: datetime, policy: str) -> dict:
    crossed = [event.event_id for event in secondary_events if start < event.scheduled_timestamp <= end and event.importance in {"high", "critical"}]
    return {
        "status": "censored" if crossed and policy == "censor" else "contains_secondary_event" if crossed else "clean",
        "secondary_event_ids": crossed,
        "use_in_aggregate": not (crossed and policy == "censor"),
    }


def decision_diagnostics(initial_return: float, post_return: float, shock: str, spread: float) -> list[dict]:
    """Describe the unchanged MacroEventStrategy gates without making a decision."""
    return [
        {"gate": "stabilization", "observed_value": shock, "threshold": "stabilized", "comparison": "==", "passed": shock == "stabilized"},
        {"gate": "shock_return_context", "observed_value": abs(post_return), "threshold": 0.01, "comparison": "<", "passed": abs(post_return) < 0.01},
        {"gate": "shock_velocity_context", "observed_value": abs(post_return), "threshold": 0.001, "comparison": "<", "passed": abs(post_return) < 0.001},
        {"gate": "shock_spread_context", "observed_value": spread, "threshold": 0.005, "comparison": "<", "passed": spread < 0.005},
        {"gate": "spread", "observed_value": spread, "threshold": 0.003, "comparison": "<=", "passed": spread <= 0.003},
        {"gate": "initial_move", "observed_value": abs(initial_return), "raw_value": initial_return, "threshold": 0.001, "comparison": ">=", "passed": abs(initial_return) >= 0.001},
        {"gate": "post_move", "observed_value": abs(post_return), "raw_value": post_return, "threshold": 0.0005, "comparison": ">=", "passed": abs(post_return) >= 0.0005},
    ]


def calculate_post_shock(
    market: list[dict],
    event_time: datetime,
    stabilization_time: datetime | None,
    secondary_events=(),
    contamination_policy: str = "flag",
    flat_tolerance: float = 0.001,
    classification_minutes: int = 15,
) -> dict:
    """Calculate descriptive metrics from observations available at completed timestamps."""
    rows = sorted(market, key=_timestamp)
    pre = [row for row in rows if _timestamp(row) <= event_time]
    after = [row for row in rows if _timestamp(row) > event_time]
    if not pre or not after:
        return {"one_minute_reference": None, "incremental_horizons": {}, "stabilization_reference": None, "post_stabilization_horizons": {}, "post_stabilization_outcome": "NO_STABILIZATION", "retracement_fraction": None, "impulse_retention_fraction": None}
    pre_price = _price(pre[-1])
    one = _at_or_after(after, event_time + timedelta(minutes=1))
    incremental = {}
    if one:
        one_price = _price(one)
        for minutes in (5, 15, 30, 60):
            target = _at_or_after(after, event_time + timedelta(minutes=minutes))
            if target:
                incremental[str(minutes)] = {"reference_basis": "one_minute", "reference_timestamp": _timestamp(one).isoformat(), "target_timestamp": _timestamp(target).isoformat(), "return_value": _price(target) / one_price - 1}
    one_reference = {"timestamp": _timestamp(one).isoformat(), "price": _price(one)} if one else None
    if stabilization_time is None:
        return {"one_minute_reference": one_reference, "incremental_horizons": incremental, "initial_shock_reference_price": pre_price, "initial_shock_displacement": None, "initial_shock_direction": None, "stabilization_reference": None, "post_stabilization_horizons": {}, "post_stabilization_outcome": "NO_STABILIZATION", "retracement_fraction": None, "impulse_retention_fraction": None, "maximum_post_stabilization_extension": None, "maximum_post_stabilization_reversal": None, "time_to_post_event_extreme_seconds": None, "time_to_post_event_high_seconds": None, "time_to_post_event_low_seconds": None}
    stable = _at_or_after(after, stabilization_time)
    if not stable:
        return {"one_minute_reference": one_reference, "incremental_horizons": incremental, "stabilization_reference": None, "post_stabilization_horizons": {}, "post_stabilization_outcome": "NO_STABILIZATION", "retracement_fraction": None, "impulse_retention_fraction": None}
    stable_time = _timestamp(stable);stable_price = _price(stable);shock = stable_price - pre_price;direction = 1 if shock > 0 else -1 if shock < 0 else 0;post_horizons = {}
    for minutes in (5, 15, 30, 60):
        target_time = stable_time + timedelta(minutes=minutes);target = _at_or_after(rows, target_time)
        if not target:continue
        interval = [row for row in rows if stable_time <= _timestamp(row) <= _timestamp(target)];directional = [direction * (float(row.get("high", _price(row))) / stable_price - 1) for row in interval] + [direction * (float(row.get("low", _price(row))) / stable_price - 1) for row in interval]
        contamination = interval_contamination(secondary_events, stable_time, _timestamp(target), contamination_policy)
        post_horizons[str(minutes)] = {"reference_basis": "stabilization", "reference_timestamp": stable_time.isoformat(), "target_timestamp": _timestamp(target).isoformat(), "return_value": _price(target) / stable_price - 1, "mae": min(directional) if directional else 0., "mfe": max(directional) if directional else 0., **contamination}
    classification_row = post_horizons.get(str(classification_minutes));classification_return = classification_row["return_value"] if classification_row else None
    if direction == 0 or classification_return is None or abs(classification_return) <= flat_tolerance:outcome = "FLAT"
    elif direction * classification_return > 0:outcome = "CONTINUATION"
    else:outcome = "MEAN_REVERSION"
    observation = post_horizons.get("60") or post_horizons.get("30") or post_horizons.get("15") or post_horizons.get("5");later_price = _price(_at_or_after(rows, datetime.fromisoformat(observation["target_timestamp"]))) if observation else stable_price
    retention = (later_price - pre_price) / shock if shock else None;retracement = 1 - retention if retention is not None else None;after_stable=[row for row in rows if _timestamp(row) >= stable_time];high=max(after_stable,key=lambda row:float(row.get("high",_price(row))));low=min(after_stable,key=lambda row:float(row.get("low",_price(row))));directional_extremes=[(direction*(float(row.get("high",_price(row)))-stable_price)/pre_price,_timestamp(row)) for row in after_stable]+[(direction*(float(row.get("low",_price(row)))-stable_price)/pre_price,_timestamp(row)) for row in after_stable];extreme=max(directional_extremes,key=lambda x:abs(x[0])) if directional_extremes else (0.,stable_time)
    return {"one_minute_reference":one_reference,"incremental_horizons":incremental,"initial_shock_reference_price":pre_price,"initial_shock_displacement":shock,"initial_shock_direction":"up" if direction>0 else "down" if direction<0 else "neutral","stabilization_reference":{"timestamp":stable_time.isoformat(),"price":stable_price},"post_stabilization_horizons":post_horizons,"post_stabilization_outcome":outcome,"retracement_fraction":retracement,"impulse_retention_fraction":retention,"maximum_post_stabilization_extension":max((x[0] for x in directional_extremes),default=0.),"maximum_post_stabilization_reversal":min((x[0] for x in directional_extremes),default=0.),"time_to_post_event_extreme_seconds":(_timestamp_to_seconds(extreme[1],event_time)),"time_to_post_event_high_seconds":_timestamp_to_seconds(_timestamp(high),event_time),"time_to_post_event_low_seconds":_timestamp_to_seconds(_timestamp(low),event_time)}


def _timestamp_to_seconds(value: datetime, event_time: datetime) -> float:
    return (value - event_time).total_seconds()
