import asyncio
import tempfile
import unittest
from pathlib import Path

from feline.core.bus import EventBus
from feline.core.events import PriceTick, RiskEvent
from feline.storage.database import Database


class StorageEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_handling(self):
        bus = EventBus()
        received = []
        async def handler(event): received.append(event.id)
        bus.subscribe(PriceTick, handler)
        tick = PriceTick(instrument="X", bid=1, ask=1.1)
        await bus.publish(tick)
        await bus.drain()
        self.assertEqual(received, [tick.id])

    async def test_database_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "feline.db")
            db.persist_event(PriceTick(instrument="X", bid=1, ask=1.1))
            db.persist_event(RiskEvent(approved=False, rule="test", message="no"))
            self.assertEqual(db.count("market_events"), 1)
            self.assertEqual(db.count("risk_events"), 1)
            db.close()

