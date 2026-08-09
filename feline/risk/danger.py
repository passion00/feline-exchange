from __future__ import annotations

from datetime import datetime, timedelta, timezone

from feline.config import RiskConfig
from feline.core.events import EconomicEvent


class EventDangerMode:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.events: list[EconomicEvent] = []

    def schedule(self, event: EconomicEvent) -> None:
        if event.scheduled_at is not None and event.importance.lower() in {"high", "critical"}:
            self.events.append(event)

    def active(self, now: datetime | None = None) -> bool:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        before = timedelta(minutes=self.config.event_minutes_before)
        after = timedelta(minutes=self.config.event_minutes_after)
        self.events = [event for event in self.events if event.scheduled_at and now <= event.scheduled_at + after]
        return any(event.scheduled_at - before <= now <= event.scheduled_at + after for event in self.events if event.scheduled_at)
