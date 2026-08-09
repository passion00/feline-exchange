from __future__ import annotations
from dataclasses import replace
import json,sqlite3,tempfile,unittest
from pathlib import Path
from feline.config import AppConfig,AIConfig
from feline.gui.controller import WorkstationController

FIXTURES=Path(__file__).parent/"fixtures"

class ReplayIsolationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.config=replace(AppConfig(),database_path=str(Path(self.tmp.name)/"test.db"),ai=AIConfig(enabled=False))
 def tearDown(self):self.tmp.cleanup()
 def run_fixture(self,controller,name):
  controller.start_replay(str(FIXTURES/name),"MAX");controller.future.result(timeout=5);return controller.snapshot()
 def test_unique_sessions_and_2026_to_2024_isolation(self):
  c=WorkstationController(self.config);self.run_fixture(c,"macro_continuation.jsonl");old=c.session["replay_session_id"];self.run_fixture(c,"fomc_2024_synthetic.jsonl");new=c.session["replay_session_id"]
  self.assertNotEqual(old,new);messages=c.drain(10000);self.assertTrue(messages);self.assertTrue(all(x["replay_session_id"]==new for x in messages));self.assertFalse(any("2026-" in str(x.get("source_timestamp","")) for x in messages));self.assertTrue(all(x["replay_session_id"]==new for x in c.snapshot()["fills"]));c.shutdown()
 def test_2024_to_2026_isolation_and_no_wall_clock_leakage(self):
  c=WorkstationController(self.config);self.run_fixture(c,"fomc_2024_synthetic.jsonl");self.run_fixture(c,"macro_mean_reversion.jsonl");messages=c.drain(10000);source=[str(x["source_timestamp"]) for x in messages if x.get("source_timestamp")];self.assertTrue(source);self.assertTrue(all(value.startswith("2026-") for value in source));c.shutdown()
 def test_macro_only_strategy_provenance_and_session_persistence(self):
  c=WorkstationController(self.config);snapshot=self.run_fixture(c,"fomc_2024_synthetic.jsonl");self.assertEqual(c.session["strategy_configuration"]["mode"],"macro_only");self.assertFalse(c.runtime.config.strategy.enabled);self.assertTrue(c.records["signals"]);self.assertTrue(all(x["strategy"]=="macro_event" and x["strategy_version"]=="0.8.1" for x in c.records["signals"]));self.assertFalse(snapshot["orders"])
  sid=c.session["replay_session_id"];connection=sqlite3.connect(self.config.database_path);rows=connection.execute("select payload from market_events").fetchall();snapshots=connection.execute("select timestamp,payload from portfolio_snapshots").fetchall();session_count=connection.execute("select count(*) from replay_sessions where replay_session_id=?",(sid,)).fetchone()[0];connection.close();self.assertTrue(any(json.loads(x[0]).get("replay_session_id")==sid for x in rows));self.assertTrue(snapshots);self.assertTrue(all(x[0].startswith("2024-") and json.loads(x[1]).get("replay_session_id")==sid for x in snapshots));self.assertEqual(session_count,1);c.shutdown()
 def test_report_json_markdown_checksum_horizons_and_no_trade(self):
  c=WorkstationController(self.config);self.run_fixture(c,"macro_no_trade.jsonl");report=c.build_report();sid=c.session["replay_session_id"];self.assertEqual(report["metadata"]["replay_session_id"],sid);self.assertEqual(len(report["metadata"]["dataset_checksum"]),64);self.assertEqual(report["macro_analysis"]["strategy"]["state"],"NO_TRADE");self.assertIn(1,report["horizons"]);self.assertFalse(report["ai"]["available"]);self.assertTrue(all(x["replay_session_id"]==sid for x in report["signals"]));self.assertEqual(report["orders"],[]);self.assertEqual(report["trades"],[])
  path=Path(self.tmp.name)/"report.json";json_path,md_path=c.export_report(path);loaded=json.loads(json_path.read_text());self.assertEqual(loaded["schema_version"],report["schema_version"]);self.assertIn("Feline Replay Report",md_path.read_text())
  with self.assertRaises(FileExistsError):c.export_report(path)
  c.shutdown()
 def test_horizons_belong_only_to_current_event(self):
  c=WorkstationController(self.config);self.run_fixture(c,"fed_macro.jsonl");self.assertIn(15,c.snapshot()["horizons"]);self.run_fixture(c,"macro_no_trade.jsonl");self.assertNotIn(15,c.snapshot()["horizons"]);self.assertEqual(c.snapshot()["macro"]["event_id"],"fed-abstain");c.shutdown()

if __name__=="__main__":unittest.main()
