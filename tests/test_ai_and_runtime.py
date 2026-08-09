import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from feline.config import AIConfig, AppConfig
from feline.core.events import NewsEvent
from feline.intelligence.service import AIWorker, AnalysisJob
from feline.runtime import FelineRuntime


VALID = {"instrument": "EURUSD", "event_type": "macro", "direction": "up", "importance": 0.7, "confidence": 0.6, "time_horizon": "hours", "summary": "test", "evidence": ["headline"]}


class FakeClient:
    def __init__(self, delay=0, result=None, error=None):
        self.delay, self.result, self.error = delay, result, error

    async def analyze(self, job):
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


class AITests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_ai_returns_safe_result(self):
        results = []
        worker = AIWorker(AIConfig(request_timeout_seconds=0.1), FakeClient(error=ConnectionError()), results.append)
        worker.start()
        worker.submit_nowait(AnalysisJob(NewsEvent(headline="h", body="b", instruments=("EURUSD",))))
        await worker.queue.join()
        self.assertFalse(results[0].available)
        await worker.stop()

    async def test_invalid_output_returns_safe_result(self):
        results = []
        async def collect(value): results.append(value)
        worker = AIWorker(AIConfig(), FakeClient(result={"bad": True}), collect)
        worker.start()
        worker.submit_nowait(AnalysisJob(NewsEvent(headline="h", body="b")))
        await worker.queue.join()
        self.assertFalse(results[0].available)
        await worker.stop()

    async def test_market_loop_continues_while_ai_is_slow(self):
        with tempfile.TemporaryDirectory() as directory:
            config = replace(AppConfig(database_path=str(Path(directory) / "test.db"), tick_interval_seconds=0.01), ai=AIConfig(request_timeout_seconds=2))
            runtime = FelineRuntime(config, ai_client=FakeClient(delay=0.5, result=VALID))
            runtime.ai.start()
            self.assertTrue(runtime.submit_news(NewsEvent(headline="slow", body="slow", instruments=("EURUSD",))))
            await runtime.run(duration=0.12)
            ticks = runtime.database.count("market_events")
            self.assertGreaterEqual(ticks, 5, "market loop was blocked by slow AI")
            self.assertFalse(runtime.ai.task.done())
            await runtime.stop()
            runtime.database.close()

