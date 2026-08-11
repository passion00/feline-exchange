from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from feline.config import AIConfig, AppConfig
from feline.core.events import MarketThesis, NewsEvent
from feline.experiments.models import ExperimentCase, SemanticExpectation
from feline.experiments.runner import _run_case
from feline.experiments.scoring import summarize
from feline.intelligence.service import AnalysisJob, LlamaCppClient, extract_json_object, news_impact_json_schema, validate_news_impact


NOW=datetime(2026,1,1,tzinfo=timezone.utc)


def job() -> AnalysisJob:
    event=NewsEvent(id="n",timestamp=NOW,ingestion_timestamp=NOW,headline="Material company news",body="Evidence only",source="fixture")
    return AnalysisJob(event,purpose="analyze_news_for_market_impact",context={"instrument_universe":[{"instrument":"AAPL","asset_class":"equity","tradable":True,"shortable":True}],"default_expiry_minutes":240})


def valid(assets=None) -> dict:
    return {"event_type":"company","event_summary":"Material company news","importance":.8,"confidence":.8,"expected_horizon":"hours","affected_instruments":[{"instrument":"AAPL","directional_bias":"LONG","confidence":.8,"relevance":.9,"monitoring_priority":.7,"rationale":"Direct company impact"}] if assets is None else assets,"reasoning_summary":"Direct evidence","risk_warnings":[],"invalidation_conditions":["report withdrawn"]}


class ContractTests(unittest.TestCase):
    def test_nested_score_bounds_types_and_missing_key_remain_fail_closed(self):
        for key,value in (("monitoring_priority",2),("monitoring_priority",-0.1),("relevance","high")):
            row=valid();row["affected_instruments"][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):validate_news_impact(row,job())
        row=valid();del row["affected_instruments"][0]["rationale"]
        with self.assertRaisesRegex(ValueError,"rationale"):validate_news_impact(row,job())

    def test_unknown_instrument_and_action_fields_remain_rejected(self):
        row=valid();row["affected_instruments"][0]["instrument"]="MADEUP"
        with self.assertRaisesRegex(ValueError,"outside supplied universe"):validate_news_impact(row,job())
        for location in ("top","nested"):
            row=valid()
            if location=="top":row["order"]="BUY"
            else:row["affected_instruments"][0]["suggested_action"]="BUY"
            with self.subTest(location=location),self.assertRaisesRegex(ValueError,"broker actions"):validate_news_impact(row,job())

    def test_valid_empty_assets_is_intentional_abstention_contract(self):
        thesis=validate_news_impact(valid([]),job());self.assertIsInstance(thesis,MarketThesis);self.assertEqual(thesis.affected_assets,())

    def test_json_extraction_accepts_fence_or_prose_but_not_malformed_or_ambiguous(self):
        value=valid([]);encoded=json.dumps(value)
        self.assertEqual(extract_json_object(f"```json\n{encoded}\n```"),value)
        self.assertEqual(extract_json_object(f"Here is the object:\n{encoded}\nDone."),value)
        for content in ('{"broken":', '{"a":1} {"b":2}'):
            with self.subTest(content=content),self.assertRaises(json.JSONDecodeError):extract_json_object(content)

    def test_managed_local_request_uses_strict_dynamic_schema_external_does_not(self):
        captured=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({"choices":[{"message":{"content":json.dumps(valid([]))}}]}).encode()
        def opener(request,timeout):captured.append(json.loads(request.data));return Response()
        with patch("feline.intelligence.service.urlrequest.urlopen",side_effect=opener):
            LlamaCppClient(AIConfig(provider="managed_local"))._request(job())
            LlamaCppClient(AIConfig(provider="openai_compatible"))._request(job())
        self.assertEqual(captured[0]["response_format"]["type"],"json_schema");self.assertNotIn("response_format",captured[1])
        schema=news_impact_json_schema(job());asset=schema["properties"]["affected_instruments"]["items"]
        self.assertEqual(asset["properties"]["instrument"]["enum"],["AAPL"]);self.assertEqual(asset["properties"]["monitoring_priority"]["maximum"],1)
        prompt=captured[0]["messages"][0]["content"]
        self.assertIn("affected_instruments: []",prompt);self.assertIn("decimal number in [0.0, 1.0]",prompt);self.assertNotIn("expected answer",prompt.lower())


class AccountingTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_schema_failure_is_not_semantically_evaluated(self):
        item=ExperimentCase("unknown","safety","h","b","fixture",NOW.isoformat(),({"instrument":"AAPL","asset_class":"equity","shortable":True},),SemanticExpectation(False,(),(),("equity",),False),fixture_analysis={"event_type":"x","instruments":["MADEUP"],"bias":"LONG"},price_scenario="none")
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);result=await _run_case(item,AppConfig(database_path=str(root/"normal.db")),root/"experiment.db","fixture")
        self.assertEqual(result["ai"]["status"],"INVALID_SCHEMA");self.assertEqual(result["engineering"]["schema_status"],"FAIL");self.assertEqual(result["semantic"]["category"],"not_evaluated");self.assertNotEqual(result["semantic"]["category"],"abstained");self.assertTrue(result["engineering"]["passed"]);self.assertEqual(result["execution"]["external_orders"],0)

    async def test_valid_empty_assets_is_semantic_abstention_not_ai_error(self):
        item=ExperimentCase("empty","relevance","h","b","fixture",NOW.isoformat(),({"instrument":"AAPL","asset_class":"equity","shortable":True},),SemanticExpectation(False,(),(),("equity",),False),fixture_response=valid([]),price_scenario="none")
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);result=await _run_case(item,AppConfig(database_path=str(root/"normal.db")),root/"experiment.db","fixture")
        self.assertEqual(result["ai"]["status"],"VALID");self.assertEqual(result["engineering"]["schema_status"],"PASS");self.assertEqual(result["semantic"]["category"],"abstained");self.assertEqual(result["semantic"]["score"],1);self.assertEqual(result["semantic"]["evaluation_status"],"EVALUATED")

    def test_aggregate_requests_and_semantics_reconcile(self):
        def row(status,schema,evaluation,category,available=False):
            return {"ai":{"status":status,"transport_status":"ERROR" if status in {"TIMEOUT","TRANSPORT_ERROR"} else "SUCCESS","available":available,"error":"TimeoutError" if status=="TIMEOUT" else None,"affected_instruments":[],"unsupported_instruments":[],"proposed_instruments":[]},"engineering":{"passed":True,"schema_status":schema,"thesis_persistence_status":"NOT_APPLICABLE","lifecycle_status":"NOT_APPLICABLE"},"semantic":{"category":category,"evaluation_status":evaluation,"score":1 if evaluation=="EVALUATED" else None},"expected":{"relevant":False,"acceptable_biases":[]},"timings":{"latency_ms":1},"safety_invariants":[],"lifecycle":{"states":[]},"execution":{}}
        summary=summarize([row("VALID","PASS","EVALUATED","strong_match",True),row("INVALID_SCHEMA","FAIL","INVALID_SCHEMA","not_evaluated"),row("TIMEOUT","NOT_EVALUATED","TIMEOUT","not_evaluated")])
        performance=summary["performance"];self.assertEqual(performance["ai_requests"],3);self.assertEqual(performance["successful_responses"],1);self.assertEqual(performance["invalid_responses"],1);self.assertEqual(performance["timeouts"],1);self.assertTrue(performance["requests_reconciled"]);self.assertEqual(summary["ai_quality"]["not_evaluated"],2)


if __name__=="__main__":unittest.main()
