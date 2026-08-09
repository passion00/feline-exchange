from __future__ import annotations

from dataclasses import replace
from datetime import datetime,timedelta,timezone
import csv,json,tempfile,unittest
from pathlib import Path

from feline.config import AIConfig,AppConfig
from feline.research.compare import compare_experiments
from feline.research.corpus import TwelveDataDownloader,_quality_record,build_corpus,corpus_doctor,fomc_events,validate_twelvedata_payload

UTC=timezone.utc

def payload(event_time,missing=(),duplicate=False,bad_ohlc=False):
 values=[];missing=set(missing)
 for offset in range(-61,122):
  stamp=event_time+timedelta(minutes=offset)
  if stamp in missing:continue
  row={"datetime":stamp.strftime("%Y-%m-%d %H:%M:%S"),"open":"1.1000","high":"1.1010","low":"1.0990","close":"1.1005"};values.append(row)
 if duplicate:values.append(dict(values[-1]))
 if bad_ohlc:values[0]["high"]="1.0999"
 return {"meta":{"symbol":"EUR/USD","interval":"1min"},"values":list(reversed(values)),"status":"ok"}

class CorpusTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()

 def test_raw_quality_clean_duplicate_and_ohlc(self):
  event=datetime(2023,2,1,19,tzinfo=UTC)
  self.assertEqual(validate_twelvedata_payload(payload(event),event)["classification"],"clean")
  self.assertEqual(validate_twelvedata_payload(payload(event,duplicate=True),event)["classification"],"invalid")
  result=validate_twelvedata_payload(payload(event,bad_ohlc=True),event);self.assertEqual(result["classification"],"provider_ohlc_anomaly");self.assertIn("timestamp",result["ohlc_anomalies"][0])

 def test_gap_severity_is_generic(self):
  event=datetime(2022,5,4,18,tzinfo=UTC);noncritical=validate_twelvedata_payload(payload(event,{event-timedelta(minutes=55)}),event);critical=validate_twelvedata_payload(payload(event,{event+timedelta(minutes=2)}),event)
  self.assertEqual(noncritical["classification"],"noncritical_gap");self.assertTrue(noncritical["include"])
  self.assertEqual(critical["classification"],"critical_gap");self.assertFalse(critical["include"])

 def test_downloader_and_single_persistent_gap_recheck(self):
  event=fomc_events(2023)[0];stamp=datetime.fromisoformat(event["timestamp"].replace("Z","+00:00"));missing={stamp+timedelta(minutes=2)};calls=[]
  def transport(url,timeout):calls.append(url);return payload(stamp,missing)
  downloader=TwelveDataDownloader("test-key",transport,retries=1,minimum_interval=0);raw=self.root/"raw.json";status=downloader.download(raw,"EURUSD",stamp-timedelta(minutes=61),stamp+timedelta(minutes=121));self.assertEqual(status,"downloaded");self.assertNotIn("test-key",raw.read_text());quality=_quality_record(raw,event,downloader);self.assertEqual(quality["classification"],"critical_gap");self.assertEqual(len(quality["gap_recheck"]),1);self.assertGreaterEqual(len(calls),2)

 def _raws(self,year=2023,critical_index=None,noncritical_index=None):
  raw=self.root/"historical"/"raw"/f"fomc_{year}";raw.mkdir(parents=True)
  for index,event in enumerate(fomc_events(year)):
   stamp=datetime.fromisoformat(event["timestamp"].replace("Z","+00:00"));missing=set()
   if index==critical_index:missing.add(stamp+timedelta(minutes=2))
   if index==noncritical_index:missing.add(stamp-timedelta(minutes=55))
   (raw/f"eurusd_{event['timestamp'][:10]}.json").write_text(json.dumps(payload(stamp,missing)))

 def test_build_manifest_quarantine_reuse_and_no_implicit_run(self):
  self._raws(2023,critical_index=1,noncritical_index=2);calls=[]
  def runner(*args,**kwargs):calls.append(args);return {"experiment":{"experiment_id":"x"},"output_directory":"report"}
  config=replace(AppConfig(),database_path=self.root/"db.sqlite",ai=AIConfig(enabled=False));first=build_corpus([2023],skip_download=True,root=self.root,config=config,experiment_runner=runner)[0]
  self.assertEqual((first["included"],first["quarantined"]),(7,1));self.assertFalse(calls)
  manifest=json.loads((self.root/"research/manifests/fomc_2023.json").read_text());self.assertEqual((manifest["seed"],manifest["window_before_minutes"],manifest["window_after_minutes"],manifest["split"],manifest["contamination_policy"],manifest["bootstrap_samples"]),(17,60,120,[.5,.25,.25],"flag",1000));ids={x["event_id"] for x in manifest["events"]};self.assertNotIn("fomc-2023-03-22",ids);tagged=next(x for x in manifest["events"] if x["event_id"]=="fomc-2023-05-03");self.assertIn("persistent_noncritical_gap",tagged["tags"])
  checksums={p.name:p.read_bytes() for p in (self.root/"historical/processed/fomc_2023").glob("*.jsonl")};second=build_corpus([2023],skip_download=True,root=self.root,config=config,experiment_runner=runner)[0];self.assertEqual(first["included"],second["included"]);self.assertEqual(checksums,{p.name:p.read_bytes() for p in (self.root/"historical/processed/fomc_2023").glob("*.jsonl")})
  run=build_corpus([2023],run=True,skip_download=True,root=self.root,config=config,experiment_runner=runner)[0];self.assertEqual(run["experiment"],"x");self.assertEqual(len(calls),1)

 def test_doctor_detects_quality_manifest_mismatch(self):
  self._raws();build_corpus([2023],skip_download=True,root=self.root,config=replace(AppConfig(),database_path=self.root/"db.sqlite"));path=self.root/"research/manifests/fomc_2023.json";manifest=json.loads(path.read_text());manifest["events"].pop();path.write_text(json.dumps(manifest));result=corpus_doctor([2023],root=self.root);self.assertFalse(result["ok"]);self.assertTrue(any("inclusion mismatch" in x for x in result["results"][0]["issues"]))

 def test_comparison_normalizes_up_and_down_shocks(self):
  directories=[]
  for name,shock,ret in (("up",.01,.002),("down",-.01,-.003)):
   directory=self.root/name;directory.mkdir();(directory/"experiment.json").write_text(json.dumps({"experiment_id":name,"aggregate":{}}))
   with (directory/"events.csv").open("w",newline="") as handle:
    writer=csv.DictWriter(handle,fieldnames=["event_id","shock_magnitude","strategy_outcome","no_trade_reason","post_stabilization_outcome","stabilization_duration_seconds","missed_move_candidate"]);writer.writeheader();writer.writerow({"event_id":name,"shock_magnitude":shock,"strategy_outcome":"NO_TRADE","post_stabilization_outcome":"CONTINUATION","stabilization_duration_seconds":300,"missed_move_candidate":False})
   with (directory/"horizons.csv").open("w",newline="") as handle:
    writer=csv.DictWriter(handle,fieldnames=["event_id","reference_basis","horizon_minutes","return_value","contamination_status"]);writer.writeheader();writer.writerow({"event_id":name,"reference_basis":"stabilization","horizon_minutes":5,"return_value":ret,"contamination_status":"clean"})
   directories.append(directory)
  result=compare_experiments(directories);self.assertAlmostEqual(result["experiments"][0]["direction_normalized_post_stabilization"]["5"]["mean"],.002);self.assertAlmostEqual(result["experiments"][1]["direction_normalized_post_stabilization"]["5"]["mean"],.003)

if __name__=="__main__":unittest.main()
