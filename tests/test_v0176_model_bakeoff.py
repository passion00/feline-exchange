from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from feline.config import AIConfig,AppConfig
from feline.experiments.runner import run_news_intelligence
from feline.experiments.reports import evaluate_model_bakeoff
from feline.intelligence.assets import LocalAIAssets,ModelCatalog
from feline.intelligence.service import LlamaCppClient
from feline.intelligence.service import AnalysisJob
from feline.core.events import NewsEvent
from datetime import datetime,timezone

class ModelBakeoffTests(unittest.TestCase):
 def test_candidate_catalog_is_pinned_and_not_default(self):
  catalog=ModelCatalog();model=catalog.get("qwen35-4b-q5km")
  self.assertEqual(catalog.default_model_id,"qwen3-4b-q4km");self.assertEqual(model.quantization,"Q5_K_M")
  self.assertEqual(model.sha256,"8814232b85594dcd46c50e5b8b29324a7efe9e746edbe8a3d1df3d3fce7aad39");self.assertEqual(model.size_bytes,3143656608)
 def test_model_override_does_not_persist_preference(self):
  with tempfile.TemporaryDirectory() as td:
   preference=Path(td)/"preference.json";config=AppConfig(ai=replace(AIConfig(),preference_path=str(preference)))
   report=run_news_intelligence(config,suite="smoke",ai_mode="fixture",limit=1,report_path=Path(td)/"r",progress=None,model_id="qwen35-4b-q5km")
   self.assertEqual(report["metadata"]["model_id"],"qwen35-4b-q5km");self.assertFalse(preference.exists())
 def test_candidate_reasoning_seed_schema_and_alias(self):
  seen=[]
  class Response:
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self):return json.dumps({"choices":[{"message":{"content":"{}"}}]}).encode()
  config=replace(AIConfig(),model="feline/qwen35-4b-q5km",inference_seed=17)
  with patch("feline.intelligence.service.urlrequest.urlopen",side_effect=lambda req,timeout:(seen.append(json.loads(req.data)) or Response())):
   job=AnalysisJob(NewsEvent(timestamp=datetime.now(timezone.utc),headline="h",body="b"),purpose="analyze_news_for_market_impact",context={"instrument_universe":[{"instrument":"EURUSD","tradable":True,"shortable":True}]})
   self.assertEqual(LlamaCppClient(config)._request(job),{})
  body=seen[0];self.assertEqual(body["model"],"feline/qwen35-4b-q5km");self.assertEqual(body["seed"],17);self.assertTrue(body["chat_template_kwargs"]["enable_thinking"]);self.assertEqual(body["response_format"]["type"],"json_schema")
 def test_existing_qwen_remains_available(self):self.assertEqual(ModelCatalog().get("qwen3-4b-q4km").family,"Qwen3")
 def test_explicit_catalog_model_ignores_saved_custom_path(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);pref=root/"preference.json";pref.write_text(json.dumps({"custom_model_path":"/tmp/old.gguf"}))
   assets=LocalAIAssets(replace(AIConfig(),model_id="qwen35-4b-q5km",preference_path=str(pref)),root=root)
   self.assertEqual(assets.model_path().name,"Qwen3.5-4B-Q5_K_M.gguf")
 def test_predeclared_bakeoff_gates_retain_mixed_candidate(self):
  def report(score,fp=.2):
   return {"schema_version":"news-intelligence-experiment-v1","metadata":{"experiment_id":"x","ai_provider":"local","model":"m","reasoning":"thinking"},"cases":[],"summary":{"engineering":{"schema_failures":0,"consistency_failures":0,"safety_failures":0,"unexpected_order_attempts":0,"lifecycle_failures":0},"performance":{"successful_responses":1,"timeouts":0,"mean_latency_ms":1000,"median_latency_ms":1000,"p95_latency_ms":1000,"completion_tokens":1},"ai_quality":{"not_evaluated":0,"strong_match":1,"partial_match":0,"mismatch":0,"mean_semantic_score":score},"relevance":{"relevant_thesis_rate":1.,"irrelevant_false_positive_rate":fp,"irrelevant_abstention_rate":1-fp},"instrument_quality":{"unsupported_instrument_proposals":0},"direction":{"causal_consistency":{"rate":1.}},"lifecycle":{"THESES_CREATED":1},"execution":{"confirmation_candidates":0}}}
  with tempfile.TemporaryDirectory() as td:
   paths=[]
   for i,payload in enumerate((report(.8),report(.8),report(.81),report(.81,.3))):
    path=Path(td)/str(i);path.mkdir();(path/"report.json").write_text(json.dumps(payload));paths.append(path)
   result=evaluate_model_bakeoff(*paths)
  self.assertFalse(result["promote_candidate"]);self.assertEqual(result["decision"],"RETAIN_CONTROL")

if __name__=="__main__":unittest.main()
