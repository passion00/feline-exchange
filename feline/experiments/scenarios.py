from __future__ import annotations

from datetime import datetime, timedelta

from feline.core.events import PriceTick


def scenario_prices(name: str, count: int = 66) -> list[float]:
    base = 1.0
    if name in {"none", "flat"}: return [base] * count
    if name == "confirms_up": return [base + i * .001 for i in range(count)]
    if name == "confirms_down": return [base - i * .001 for i in range(count)]
    if name == "volatile_no_direction": return [base + (.001 if i % 2 else -.001) for i in range(count)]
    if name == "initial_confirmation_then_reversal": return [base + i * .001 for i in range(8)] + [base + .007 - (i - 7) * .001 for i in range(8, count)]
    if name == "confirmation_after_expiry": return [base] * 8 + [base + (i - 7) * .001 for i in range(8, count)]
    if name == "excessive_gap": return [base] * 7 + [base * 1.05 + (i - 7) * .001 for i in range(7, count)]
    raise ValueError(f"Unknown price scenario: {name}")


def ticks(instrument: str, start: datetime, scenario: str, count: int = 66) -> list[PriceTick]:
    result = []
    for index, mid in enumerate(scenario_prices(scenario, count)):
        spread = max(mid * .00002, .000001)
        result.append(PriceTick(id=f"experiment:{instrument}:{scenario}:{index}", timestamp=start + timedelta(minutes=index), ingestion_timestamp=start + timedelta(minutes=index), instrument=instrument, bid=mid-spread/2, ask=mid+spread/2, source="experiment_fixture"))
    return result


def forward_returns(prices: list[float], horizons=(5, 15, 30, 60)) -> dict[str, float | None]:
    if not prices: return {f"return_{x}m": None for x in horizons}
    return {f"return_{x}m": round(prices[x] / prices[0] - 1, 9) if x < len(prices) else None for x in horizons}
