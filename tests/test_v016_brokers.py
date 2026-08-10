from __future__ import annotations
import asyncio,json,os,tempfile,time,unittest
from unittest.mock import patch
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse

from feline.brokers import BrokerCapabilities,BrokerProfile,BrokerProfileStore,BrokerRegistry,UnsupportedBrokerCapability
from feline.brokers.mock import MockBrokerAdapter
from feline.brokers.oanda import OandaBrokerAdapter
from feline.config import AIConfig,AppConfig
from feline.core.events import OrderRequest,OrderStatus,PriceTick,Side
from feline.runtime import FelineRuntime
from feline.market.realtime import RealtimeIngestionProvider,RealtimeSessionConfig
from feline.gui.controller import WorkstationController

UTC=timezone.utc

class HTTP:
 def __init__(self):self.requests=[]
 def json(self,request):
  self.requests.append((request.method,urlparse(request.full_url).path,json.loads(request.data) if request.data else None));path=urlparse(request.full_url).path
  if path.endswith('/summary'):return {'account':{'balance':'10000','NAV':'10010','marginAvailable':'9000'}}
  if path.endswith('/instruments'):return {'instruments':[{'name':'EUR_USD'},{'name':'XAU_USD'}]}
  if path.endswith('/openPositions'):return {'positions':[]}
  if path.endswith('/pendingOrders'):return {'orders':[]}
  if path.endswith('/orders') and request.method=='GET':return {'orders':[]}
  if path.endswith('/cancel'):return {'orderCancelTransaction':{'id':'c1'}}
  if path.endswith('/orders'):return {'orderFillTransaction':{'id':'t1','orderID':'o1','price':'1.1002','commission':'0'}}
  raise AssertionError(path)

class ProfileAndRegistryTests(unittest.TestCase):
 def test_discovery_capabilities_and_unsupported_failure(self):
  registry=BrokerRegistry.builtins();self.assertIn('oanda_v20',registry.names());caps=OandaBrokerAdapter.broker_capabilities;self.assertTrue(caps.market_orders);self.assertTrue(caps.account)
  with self.assertRaises(ValueError):caps.supports('imaginary')
  adapter=MockBrokerAdapter();adapter.broker_capabilities=BrokerCapabilities(authentication=False,quotes=True)
  with self.assertRaises(UnsupportedBrokerCapability):adapter.require('cancel_orders')
  with self.assertRaises(UnsupportedBrokerCapability):asyncio.run(adapter.historical_candles(object()))
 def test_profile_lifecycle_never_persists_credential(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'profiles.json';store=BrokerProfileStore(path);profile=BrokerProfile(name='Practice',account_id='abc',credential_env='SECRET_ENV');store.save(profile);self.assertEqual(store.get(profile.profile_id).account_id,'abc');text=path.read_text();self.assertNotIn('super-secret',text);self.assertIn('SECRET_ENV',text);store.remove(profile.profile_id);self.assertEqual(store.load(),[])
 def test_live_profile_requires_explicit_enablement(self):
  with self.assertRaises(ValueError):BrokerProfile(environment='live')
  with patch.dict(os.environ,{},clear=True):
   with self.assertRaises(PermissionError):OandaBrokerAdapter(BrokerProfile(environment='live',live_execution_enabled=True),token='not-a-real-token')

class MockBrokerTests(unittest.IsolatedAsyncioTestCase):
 async def test_external_runtime_connects_streams_persists_and_disconnects(self):
  broker=MockBrokerAdapter();provider=RealtimeIngestionProvider(broker,RealtimeSessionConfig(instruments=('EURUSD',),stale_after_seconds=10,feed_timeout_seconds=1))
  with tempfile.TemporaryDirectory() as td:
   runtime=FelineRuntime(AppConfig(database_path=str(Path(td)/'x.db'),ai=AIConfig(enabled=False)),provider=provider,execution_broker=broker,recover=False,autonomous_trading_enabled=False);await runtime.run(.03);await runtime.stop();self.assertGreater(runtime.tick_count,0);self.assertEqual(broker.state.value,'DISCONNECTED');self.assertEqual(runtime.database.count('broker_sessions'),1);self.assertEqual(runtime.database.count('realtime_quotes'),runtime.tick_count);runtime.database.close()
 async def test_connect_order_cancel_reject_reconcile_and_duplicate_audit(self):
  broker=MockBrokerAdapter();await broker.connect();self.assertEqual((await broker.account_snapshot())['state'],'CONNECTED');self.assertIn('EURUSD',await broker.discover_instruments());now=datetime.now(UTC);broker.update_quote(PriceTick(timestamp=now,instrument='EURUSD',bid=1.1,ask=1.1002));request=OrderRequest(timestamp=now,instrument='EURUSD',side=Side.BUY,quantity=1,expected_price=1.1001);filled=await broker.submit_order(request);self.assertEqual(filled.status,OrderStatus.FILLED);self.assertEqual(len(broker.fills),1);self.assertFalse((await broker.reconcile())['disagreement'])
  rejected=MockBrokerAdapter(reject=True);await rejected.connect();rejected.update_quote(broker.get_quote('EURUSD'));self.assertEqual((await rejected.submit_order(request)).status,OrderStatus.REJECTED)
  partial=MockBrokerAdapter(partial=True);await partial.connect();partial.update_quote(broker.get_quote('EURUSD'));part=await partial.submit_order(request);self.assertEqual(part.status,OrderStatus.PARTIALLY_FILLED);self.assertEqual(part.remaining_quantity,.5);await partial.disconnect();await partial.connect();self.assertEqual(partial.state.value,'CONNECTED')
  with self.assertRaises(RuntimeError):await broker.submit_order(request)
  with tempfile.TemporaryDirectory() as td:
   runtime=FelineRuntime(AppConfig(database_path=str(Path(td)/'x.db'),ai=AIConfig(enabled=False)),execution_broker=broker,recover=False,autonomous_trading_enabled=False);session={'session_id':'s','profile_id':broker.profile.profile_id,'adapter':'mock','environment':'practice','account_id':'','started_at':now.isoformat(),'ended_at':None,'status':'connected'};runtime.database.save_broker_session(session);runtime.broker_session=session;events=broker.drain_audit_events();runtime.database.save_broker_events('s',events+events);self.assertEqual(runtime.database.count('broker_events'),len(events));runtime.database.close()
  await broker.disconnect()
 async def test_start_stop_gate_and_risk_remain_authoritative(self):
  broker=MockBrokerAdapter();await broker.connect();now=datetime.now(UTC);broker.update_quote(PriceTick(timestamp=now,instrument='EURUSD',bid=1.1,ask=1.1002))
  with tempfile.TemporaryDirectory() as td:
   runtime=FelineRuntime(AppConfig(database_path=str(Path(td)/'x.db'),ai=AIConfig(enabled=False)),execution_broker=broker,recover=False,autonomous_trading_enabled=False);order=OrderRequest(timestamp=now,instrument='EURUSD',side=Side.BUY,quantity=1,expected_price=1.1001,stop_price=1.09);blocked=await runtime.request_order(order);self.assertEqual(blocked.rule,'autonomous_trading');runtime.arm_autonomous_trading();result=await runtime.request_order(order);self.assertEqual(result.status,OrderStatus.FILLED);runtime.risk.activate_kill_switch();blocked=await runtime.request_order(order);self.assertEqual(blocked.rule,'kill_switch');runtime.disarm_autonomous_trading();await runtime.bus.drain();runtime.database.close()

class BrokerManagerControllerTests(unittest.TestCase):
 def test_profile_selection_stream_and_ephemeral_credential_cleanup(self):
  with tempfile.TemporaryDirectory() as td:
   database=Path(td)/'gui.db';profiles=Path(td)/'profiles.json';controller=WorkstationController(AppConfig(database_path=str(database),ai=AIConfig(enabled=False)));controller.broker_profiles=BrokerProfileStore(profiles);controller.broker_registry=BrokerRegistry();controller.broker_registry.register('mock',MockBrokerAdapter);profile=BrokerProfile(adapter='mock',account_id='demo',credential_env='FELINE_TEST_TOKEN');controller.save_broker_profile(profile);controller.connect_broker(profile.profile_id,'temporary-secret');time.sleep(.03);self.assertEqual(controller.selected_broker_profile,profile.profile_id);self.assertEqual(os.environ.get('FELINE_TEST_TOKEN'),'temporary-secret');self.assertTrue(any(row.get('kind')=='tick' for row in controller.drain()));controller.disconnect_broker();controller.future.result(timeout=3);self.assertNotIn('FELINE_TEST_TOKEN',os.environ);self.assertGreater(controller.runtime.tick_count,0);controller.shutdown();self.assertNotIn(b'temporary-secret',profiles.read_bytes()+database.read_bytes())

class OandaAdapterTests(unittest.IsolatedAsyncioTestCase):
 async def test_practice_account_discovery_execution_and_audit(self):
  async def direct(function,*args,**kwargs):return function(*args,**kwargs)
  with patch('feline.brokers.oanda.asyncio.to_thread',side_effect=direct):
   http=HTTP();profile=BrokerProfile(name='OANDA Practice',account_id='acct');broker=OandaBrokerAdapter(profile,token='test-token',client=http);account=await broker.connect();self.assertEqual(account['balance'],10000);self.assertEqual(await broker.discover_instruments(),('EURUSD','XAUUSD'));self.assertFalse((await broker.reconcile())['disagreement']);now=datetime.now(UTC);broker.update_quote(PriceTick(timestamp=now,instrument='EURUSD',bid=1.1,ask=1.1002));update=await broker.submit_order(OrderRequest(timestamp=now,instrument='EURUSD',side=Side.BUY,quantity=1,expected_price=1.1001,stop_price=1.09));self.assertEqual(update.order_id,'o1');self.assertEqual(update.status,OrderStatus.FILLED);self.assertNotIn('test-token',json.dumps(broker.drain_audit_events()));self.assertEqual(http.requests[-1][2]['order']['instrument'],'EUR_USD');await broker.cancel_order('o1');await broker.disconnect()

if __name__=='__main__':unittest.main()
