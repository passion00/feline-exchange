from __future__ import annotations

def run_gui():
 from datetime import datetime,timezone
 try:
  from PySide6.QtCore import QSettings,Qt,QTimer,QRectF,QLineF
  from PySide6.QtGui import QAction,QKeySequence,QPainter,QPicture
  from PySide6.QtWidgets import QApplication,QCheckBox,QComboBox,QDialog,QFileDialog,QFormLayout,QHBoxLayout,QLabel,QLineEdit,QMainWindow,QMessageBox,QProgressBar,QPushButton,QSplitter,QTableWidget,QTableWidgetItem,QTabWidget,QTextEdit,QToolTip,QVBoxLayout,QWidget
  import pyqtgraph as pg
 except ImportError as exc:raise SystemExit("Qt GUI dependencies missing. Install with: python3 -m pip install -e .") from exc
 from .controller import ChartBuffer,EventProjection,WorkstationController,projection_sort_key,shifted_x_range,should_follow_candle,visible_candle_y_range
 app=QApplication([]);app.setStyle("Fusion");app.setStyleSheet("QWidget{background:#11161d;color:#d8dee9;font-size:12px} QTableWidget,QTextEdit{background:#0b1016;border:1px solid #263241} QPushButton{padding:5px 10px;background:#202b38;border:1px solid #34465a} QPushButton:hover{background:#29394a} QTabBar::tab:selected{background:#26384b}")
 class CandlestickItem(pg.GraphicsObject):
  def __init__(self):super().__init__();self.picture=QPicture();self.data=[]
  def setData(self,data):
   self.prepareGeometryChange();self.data=list(data);self.picture=QPicture();painter=QPainter(self.picture);spacing=min((self.data[i]["open_timestamp"]-self.data[i-1]["open_timestamp"] for i in range(1,len(self.data)) if self.data[i]["open_timestamp"]>self.data[i-1]["open_timestamp"]),default=60);width=spacing*.72
   for candle in self.data:
    x=candle["open_timestamp"]+spacing/2;up=candle["close"]>=candle["open"];color="#26a69a" if up else "#ef5350";painter.setPen(pg.mkPen(color,width=1));painter.drawLine(QLineF(x,candle["low"],x,candle["high"]));painter.setBrush(pg.mkBrush(color));bottom=min(candle["open"],candle["close"]);height=max(abs(candle["close"]-candle["open"]),1e-8);painter.drawRect(QRectF(x-width/2,bottom,width,height))
   painter.end();self.setToolTip("Completed OHLC candles; source values are retained for inspection");self.update()
  def paint(self,painter,option,widget):painter.drawPicture(0,0,self.picture)
  def mouseClickEvent(self,event):
   if not self.data:return
   candle=min(self.data,key=lambda x:abs(x["open_timestamp"]-event.pos().x()));QToolTip.showText(event.screenPos().toPoint(),f"{candle['timestamp']}\nO {candle['open']:.5f}  H {candle['high']:.5f}\nL {candle['low']:.5f}  C {candle['close']:.5f}\nVolume {candle['volume']}")
  def boundingRect(self):
   if not self.data:return QRectF()
   spacing=min((self.data[i]["open_timestamp"]-self.data[i-1]["open_timestamp"] for i in range(1,len(self.data)) if self.data[i]["open_timestamp"]>self.data[i-1]["open_timestamp"]),default=60);return QRectF(self.data[0]["open_timestamp"],min(x["low"] for x in self.data),self.data[-1]["open_timestamp"]-self.data[0]["open_timestamp"]+spacing,max(x["high"] for x in self.data)-min(x["low"] for x in self.data))
 class Window(QMainWindow):
  def __init__(self):
   super().__init__();self.setWindowTitle("Feline Exchange v0.17.2 — News Intelligence Experiments");self.settings=QSettings("FelineExchange","Workstation");self.resize(self.settings.value("size",self.size()));self.chart_data={};self.events=EventProjection();self.controller=WorkstationController();self.dataset=None;self.instrument=None;self.chart_marker_items=[];self.displayed_latest={};self.pending_follow_steps=0;self.chart_initialized=False;self.news_items=[];self.thesis_items=[];self.thesis_lifecycle=[]
   root=QWidget();layout=QVBoxLayout(root);top=QHBoxLayout();top.addWidget(QLabel("<b>FELINE EXCHANGE</b>"));self.mode_label=QLabel("  PAPER / PRACTICE DEFAULT  ");self.mode_label.setStyleSheet("background:#735c0f;color:#fff3bf;padding:5px;font-weight:bold");top.addWidget(self.mode_label);top.addStretch();self.status=QLabel("RUNTIME ● STOPPED   FEED ● OFF   DB ● OK   AI ● UNKNOWN   DANGER ● OFF   KILL ● OFF");top.addWidget(self.status);layout.addLayout(top)
   split=QSplitter(Qt.Horizontal);left=QTabWidget();self.watch=QTableWidget(0,5);self.watch.setHorizontalHeaderLabels(["Symbol","Last","Change","Spread","Regime"]);left.addTab(self.watch,"Watchlist");self.macro_watch=QTableWidget(0,4);self.macro_watch.setHorizontalHeaderLabels(["Event","Source","Time","Phase"]);left.addTab(self.macro_watch,"Macro Watch");left.addTab(QTableWidget(0,2),"Providers");split.addWidget(left)
   center=QWidget();cl=QVBoxLayout(center);controls=QHBoxLayout();self.open=QPushButton("Open Dataset");self.live=QPushButton("Broker Manager");self.ai_manager=QPushButton("AI Manager");self.arm=QPushButton("Start Trading");self.disarm=QPushButton("Stop Trading");self.speed=QComboBox();self.speed.addItems(["0.25","0.5","1","2","5","10","MAX"]);self.chart_mode=QComboBox();self.chart_mode.addItems(["Candles","Line"]);self.timeframe=QComboBox();self.timeframe.addItems(["1m","5m","15m","1h"]);self.auto_follow=QCheckBox("Auto-fit && Follow");self.auto_follow.setChecked(True);self.play=QPushButton("Start Replay");self.pause=QPushButton("Pause");self.stop=QPushButton("Stop Replay/Feed");self.fit=QPushButton("Fit");self.export=QPushButton("Export Replay Report");self.export.setEnabled(False);[controls.addWidget(x) for x in (self.open,self.live,self.ai_manager,self.arm,self.disarm,self.speed,self.chart_mode,self.timeframe,self.auto_follow,self.play,self.pause,self.stop,self.fit,self.export)];cl.addLayout(controls);self.plot=pg.PlotWidget(axisItems={"bottom":pg.DateAxisItem()});self.plot.showGrid(x=True,y=True,alpha=.2);self.curve=self.plot.plot(pen=pg.mkPen("#55b7ff",width=2));self.candle_item=CandlestickItem();self.plot.addItem(self.candle_item);cl.addWidget(self.plot);split.addWidget(center)
   right=QTabWidget();self.right={};
   for name in ("Provider","Portfolio","Risk","Regime","Continuous","AI Opinion","News Intelligence","Strategy State","Horizons","Research"):self.right[name]=QTextEdit();self.right[name].setReadOnly(True);right.addTab(self.right[name],name)
   split.addWidget(right);split.setSizes([260,700,300]);layout.addWidget(split,4)
   bottom=QTabWidget();self.bottom={};
   columns={"Event Stream":["Time","Category","Instrument","Summary"],"Signals":["Time","Instrument","Strategy","Outcome","Direction","Confidence","Risk","Reason"],"Orders / Fills":["Time","ID","Instrument","Side","Type","State","Quantity","Filled","Remaining","Fill","Spread","Slippage","Commission","Latency"],"Completed Trades":["Instrument","Direction","Entry","Exit","Quantity","Net P/L","Costs","Holding","MAE","MFE","Exit reason","Strategy"],"Diagnostics":["Time","Severity","Component","Summary"]}
   for name,headers in columns.items():self.bottom[name]=QTableWidget(0,len(headers));self.bottom[name].setHorizontalHeaderLabels(headers);bottom.addTab(self.bottom[name],name)
   layout.addWidget(bottom,2);self.setCentralWidget(root)
   self.research_open=QPushButton("Run Research");self.research_cancel=QPushButton("Cancel Batch");controls.addWidget(self.research_open);controls.addWidget(self.research_cancel);emergency=QPushButton("EMERGENCY STOP");emergency.setStyleSheet("background:#7f1d1d;font-weight:bold");top.addWidget(emergency);emergency.clicked.connect(self.emergency);self.open.clicked.connect(self.choose);self.live.clicked.connect(self.open_broker_manager);self.ai_manager.clicked.connect(self.open_ai_manager);self.arm.clicked.connect(self.start_trading);self.disarm.clicked.connect(self.stop_trading);self.play.clicked.connect(self.start_replay);self.pause.clicked.connect(self.toggle_pause);self.stop.clicked.connect(self.controller.stop);self.fit.clicked.connect(self.fit_chart);self.export.clicked.connect(self.export_report);self.research_open.clicked.connect(self.start_research);self.research_cancel.clicked.connect(self.controller.cancel_research);self.watch.cellClicked.connect(self.select_instrument);self.timeframe.currentTextChanged.connect(self.timeframe_changed);self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(100)
   act=QAction(self);act.setShortcut(QKeySequence("Ctrl+O"));act.triggered.connect(self.choose);self.addAction(act);space=QAction(self);space.setShortcut(QKeySequence(Qt.Key_Space));space.triggered.connect(self.toggle_pause);self.addAction(space)
  def choose(self):
   path,_=QFileDialog.getOpenFileName(self,"Open replay dataset","","Replay (*.csv *.jsonl)");
   if path:self.dataset=path
  def start_replay(self):
   if self.dataset:
    self.chart_data.clear();self.chart_marker_items.clear();self.displayed_latest.clear();self.pending_follow_steps=0;self.chart_initialized=False;self.events=EventProjection();self.plot.clear();self.curve=self.plot.plot(pen=pg.mkPen("#55b7ff",width=2));self.candle_item=CandlestickItem();self.plot.addItem(self.candle_item);self.macro_watch.setRowCount(0);self.export.setEnabled(False)
    for table in self.bottom.values():table.setRowCount(0)
    self.controller.start_replay(self.dataset,self.speed.currentText())
  def open_broker_manager(self):
   from feline.brokers import BrokerProfile
   dialog=QDialog(self);dialog.setWindowTitle("Broker Manager — credentials are memory-only");layout=QVBoxLayout(dialog);profiles=QComboBox();name=QLineEdit();adapter=QComboBox();adapter.addItems(self.controller.available_broker_adapters());environment=QComboBox();environment.addItems(["practice","demo","live"]);account=QLineEdit();instrument=QLineEdit("EURUSD");credential=QLineEdit();credential.setEchoMode(QLineEdit.Password);credential.setPlaceholderText("Token (never persisted)");form=QFormLayout();form.addRow("Saved profile",profiles);form.addRow("Name",name);form.addRow("Broker adapter",adapter);form.addRow("Environment",environment);form.addRow("Account ID",account);form.addRow("Default instrument",instrument);form.addRow("Credential",credential);layout.addLayout(form);buttons=QHBoxLayout();save=QPushButton("Save");remove=QPushButton("Remove");connect=QPushButton("Connect");disconnect=QPushButton("Disconnect");[buttons.addWidget(x) for x in (save,remove,connect,disconnect)];layout.addLayout(buttons);status=QLabel("Connecting does not start trading.");layout.addWidget(status)
   def reload_profiles(selected=None):
    profiles.clear();profiles.addItem("New profile",None)
    for p in self.controller.list_broker_profiles():profiles.addItem(f"{p.name} [{p.environment}]",p.profile_id)
    if selected:
     index=profiles.findData(selected)
     if index>=0:profiles.setCurrentIndex(index)
   def selected_profile():
    pid=profiles.currentData();return self.controller.broker_profiles.get(pid) if pid else None
   def populate(*_):
    p=selected_profile()
    if not p:return
    name.setText(p.name);adapter.setCurrentText(p.adapter);environment.setCurrentText(p.environment);account.setText(p.account_id);instrument.setText(p.default_instrument);credential.clear()
   def save_profile():
    old=selected_profile()
    try:p=BrokerProfile(profile_id=old.profile_id if old else __import__('uuid').uuid4().hex,name=name.text() or "Broker",adapter=adapter.currentText(),environment=environment.currentText(),account_id=account.text().strip(),credential_env="FELINE_OANDA_API_TOKEN",default_instrument=instrument.text().replace("/","").upper() or "EURUSD",live_execution_enabled=False);self.controller.save_broker_profile(p);reload_profiles(p.profile_id);status.setText("Profile saved without credential.")
    except Exception as exc:QMessageBox.warning(dialog,"Profile not saved",str(exc))
   def remove_profile():
    p=selected_profile()
    if p:
     try:self.controller.remove_broker_profile(p.profile_id);reload_profiles();status.setText("Profile removed.")
     except Exception as exc:QMessageBox.warning(dialog,"Cannot remove",str(exc))
   def connect_profile():
    p=selected_profile()
    if not p:return QMessageBox.warning(dialog,"No profile","Save or select a profile first.")
    try:self.controller.connect_broker(p.profile_id,credential.text() or None);credential.clear();status.setText("Connecting… Trading remains STOPPED.")
    except Exception as exc:credential.clear();QMessageBox.warning(dialog,"Connection failed",str(exc))
   profiles.currentIndexChanged.connect(populate);save.clicked.connect(save_profile);remove.clicked.connect(remove_profile);connect.clicked.connect(connect_profile);disconnect.clicked.connect(lambda:self.controller.disconnect_broker());reload_profiles();dialog.exec()
  def open_ai_manager(self):
   from feline.intelligence.assets import LocalAIAssets
   from feline.intelligence.operations import LocalAIProcessManager,ai_health
   dialog=QDialog(self);dialog.setWindowTitle("AI Manager — repository-local assets");layout=QVBoxLayout(dialog);assets=LocalAIAssets(self.controller.config.ai);provider=QComboBox();provider.addItem("Managed Local llama.cpp","managed_local");provider.addItem("External OpenAI-Compatible","openai_compatible");provider.setCurrentIndex(0 if self.controller.config.ai.provider in {"managed_local","local_llama_cpp","llama_cpp"} else 1);endpoint_edit=QLineEdit(self.controller.config.ai.base_url);alias_edit=QLineEdit(self.controller.config.ai.model);models=QComboBox()
   for row in assets.catalog_rows():models.addItem(f"{row['display_name']} — {row['status']} — {row['recommendation']}",row['id'])
   runtime=QLabel();model_state=QLabel();server=QLabel();endpoint=QLabel();apply_provider=QPushButton("Apply Provider Preference");form=QFormLayout();form.addRow("AI Provider",provider);form.addRow("Endpoint",endpoint_edit);form.addRow("API model alias",alias_edit);form.addRow("Model",models);form.addRow("Runtime",runtime);form.addRow("Model state",model_state);form.addRow("Server",server);form.addRow("Endpoint health",endpoint);layout.addLayout(form);layout.addWidget(apply_provider);custom=QPushButton("Custom GGUF…");layout.addWidget(custom);progress=QProgressBar();progress.setRange(0,100);progress.hide();layout.addWidget(progress);buttons=QHBoxLayout();install=QPushButton("Install Local AI");select=QPushButton("Change Model");start=QPushButton("Start AI");stop=QPushButton("Stop AI");[buttons.addWidget(x) for x in (install,select,start,stop)];layout.addLayout(buttons);message=QLabel("Downloads and process work run outside the Qt thread.");message.setWordWrap(True);layout.addWidget(message);pending={"future":None,"progress":{}}
   def refresh_status():
    status=assets.status();health=ai_health(self.controller.config.ai);process=LocalAIProcessManager().status();size=assets.model_path().stat().st_size if assets.model_path().is_file() else assets.selected_model().size_bytes;runtime.setText(f"{status['runtime_state']} ({status.get('runtime_version') or '—'})");model_state.setText(f"{status['model_state']} — {status['recommendation']} — approximately {size/1024**3:.2f} GiB");server.setText(process['state']);endpoint.setText(health['endpoint_state'])
   def run_background(function):
    if pending['future'] and not pending['future'].done():return
    pending['progress']={};progress.setValue(0);progress.show();message.setText("Working… market/news ingestion remains independent.");pending['future']=self.controller.research_executor.submit(function)
    def poll():
     row=pending['progress']
     if row:
      if row.get('percent') is None:progress.setRange(0,0)
      else:progress.setRange(0,100);progress.setValue(int(row['percent']));progress.setFormat(f"{Path(row['destination']).name} — %p%")
     if not pending['future'].done():QTimer.singleShot(200,poll);return
     progress.hide()
     try:message.setText(str(pending['future'].result()))
     except Exception as exc:message.setText(f"Error: {exc}")
     refresh_status()
    QTimer.singleShot(200,poll)
   def select_model():
    try:message.setText(str(assets.select_model(models.currentData()))+" Restart AI explicitly to apply.");refresh_status()
    except Exception as exc:message.setText(f"Error: {exc}")
   def choose_custom():
    path,_=QFileDialog.getOpenFileName(dialog,"Select custom GGUF","","GGUF models (*.gguf)")
    if path:
     try:message.setText(str(assets.select_custom_model(Path(path)))+" Restart AI explicitly to apply.");refresh_status()
     except Exception as exc:message.setText(f"Error: {exc}")
   def save_provider():
    try:
     result=assets.select_provider(provider.currentData(),endpoint_edit.text(),alias_edit.text());new_ai=__import__('dataclasses').replace(self.controller.config.ai,provider=result['provider'],base_url=result.get('base_url',self.controller.config.ai.base_url),model=result.get('model',self.controller.config.ai.model));self.controller.config=__import__('dataclasses').replace(self.controller.config,ai=new_ai);assets.config=new_ai;message.setText(str(result)+" Restart active AI work explicitly; broker/trading state was not changed.");refresh_status()
    except Exception as exc:message.setText(f"Error: {exc}")
   def start_server():
    manager=LocalAIProcessManager();started=manager.start(self.controller.config.ai);return {"process":started,"readiness":manager.wait_until_ready(self.controller.config.ai)} if started.get("state")=="STARTING" else started
   def install_assets():return assets.install(lambda row:pending.__setitem__('progress',row))
   apply_provider.clicked.connect(save_provider);install.clicked.connect(lambda:run_background(install_assets));select.clicked.connect(select_model);custom.clicked.connect(choose_custom);start.clicked.connect(lambda:run_background(start_server));stop.clicked.connect(lambda:run_background(LocalAIProcessManager().stop));refresh_status();dialog.exec()
  def start_trading(self):
   try:self.controller.start_autonomous_trading()
   except Exception as exc:QMessageBox.warning(self,"Cannot start trading",str(exc))
  def stop_trading(self):self.controller.stop_autonomous_trading()
  def start_research(self):
   path,_=QFileDialog.getOpenFileName(self,"Open research manifest","","Research manifest (*.json)")
   if path:
    try:self.controller.start_research(path);self.right["Research"].setPlainText("Starting batch research…")
    except Exception as exc:QMessageBox.warning(self,"Research",str(exc))
  def export_report(self):
   default=f"data/reports/replay_{self.controller.session['replay_session_id'][:8]}.json";path,_=QFileDialog.getSaveFileName(self,"Export replay report",default,"JSON (*.json)")
   if not path:return
   try:
    created=self.controller.export_report(path);QMessageBox.information(self,"Replay report exported",f"Created:\n{created[0]}\n{created[1]}")
   except Exception as exc:self._append(self.bottom["Diagnostics"],["","error","report",str(exc)]);QMessageBox.warning(self,"Report export failed",str(exc))
  def fit_chart(self):
   if self.instrument in self.chart_data:self.chart_data[self.instrument].request_fit();self.plot.enableAutoRange()
  def timeframe_changed(self,value):
   self.pending_follow_steps=0;self.chart_initialized=False
   if self.instrument in self.chart_data:
    self.chart_data[self.instrument].request_fit();series=self.chart_data[self.instrument].candles[value]
    if series:self.displayed_latest[(self.instrument,value)]=series[-1]["open_timestamp"]
  def fit_visible_candles_y_range(self,candles):
   target=visible_candle_y_range(candles,self.plot.getViewBox().viewRange()[0])
   if target:self.plot.setYRange(*target,padding=0)
  def follow_new_candle(self,candles,steps=1):
   if not self.auto_follow.isChecked() or not self.chart_initialized or steps<=0:return
   current=self.plot.getViewBox().viewRange()[0];shifted=shifted_x_range(current,self.timeframe.currentText());interval=shifted[0]-current[0];shifted=(current[0]+interval*steps,current[1]+interval*steps);self.plot.setXRange(*shifted,padding=0);self.fit_visible_candles_y_range(candles)
  def select_instrument(self,row,column):
   item=self.watch.item(row,0)
   if item:self.instrument=item.text();self.controller.selected_instrument=self.instrument;self.pending_follow_steps=0;self.chart_initialized=False;self.chart_data.setdefault(self.instrument,ChartBuffer()).request_fit();self._render_markers()
  def toggle_pause(self):self.controller.resume() if self.controller.replay.state.value=="paused" else self.controller.pause();self.pause.setText("Resume" if self.controller.replay.state.value=="paused" else "Pause")
  def emergency(self):
   if QMessageBox.question(self,"Confirm emergency stop","Activate deterministic PAPER kill switch?",QMessageBox.Yes|QMessageBox.No)==QMessageBox.Yes:
    self.controller.emergency_stop();self.status.setText("KILL SWITCH ● ACTIVE — PAPER MODE")
  def refresh(self):
   for item in self.controller.drain():
    if item["kind"]=="tick":
     self.instrument=self.instrument or item["instrument"];buf=self.chart_data.setdefault(item["instrument"],ChartBuffer());buf.add(item["timestamp"],item["price"])
    elif item["kind"]=="candle":
     self.instrument=self.instrument or item["instrument"];is_new=self.chart_data.setdefault(item["instrument"],ChartBuffer()).add_candle(item);key=(item["instrument"],item["timeframe"]);previous=self.displayed_latest.get(key);self.displayed_latest[key]=item["open_timestamp"]
     if should_follow_candle(self.auto_follow.isChecked(),is_new,previous,item["instrument"],item["timeframe"],self.instrument,self.timeframe.currentText()):self.pending_follow_steps+=1
    elif item["kind"]=="event":self.events.add(item["timestamp"],item["category"],item["summary"],item["instrument"],item.get("payload"))
    elif item["kind"]=="signal":self._append(self.bottom["Signals"],[item.get(k,"") for k in ("timestamp","instrument","strategy","outcome","direction","confidence","risk","reason")])
    elif item["kind"]=="marker":
     self.events.add(item["timestamp"],item["category"],item["label"],item.get("instrument"),item);self.chart_data.setdefault(item["instrument"],ChartBuffer()).markers.append(item)
     if item["instrument"]==self.instrument:self._add_marker(item)
    elif item["kind"]=="diagnostic":self._append(self.bottom["Diagnostics"],["",item.get("severity","error"),"replay",item.get("summary","")])
    elif item["kind"]=="research":
     post=item.get("post_stabilization",{});horizons=item.get("post_stabilization_horizons",{});horizon_text="\n".join(f"Post-stable {h}m: {v.get('mean')}" for h,v in horizons.items() if h in {"5","15","30"});self.right["Research"].setPlainText(f"Experiment: {item.get('experiment_id','—')}\nState: {item.get('state','running')}\nCurrent: {item.get('current_event','—')}\nCompleted: {item.get('completed',0)} / {item.get('total',0)}\nFailed/excluded: {item.get('failed',0)}\nStabilization median: {item.get('median_stabilization_seconds','—')} s\nPost-stable outcomes: {post or '—'}\nRetracement median: {item.get('median_retracement','—')}\nImpulse retained median: {item.get('median_impulse_retention','—')}\n{horizon_text}")
    elif item["kind"]=="continuous":
     from feline.market.profiles import get_execution_profile,get_market_profile
     symbol=item.get("instrument") or self.instrument or "EURUSD"
     try:market=get_market_profile(symbol);execution=get_execution_profile(symbol);profile_text=f"{market.asset_class} / {market.trading_calendar}\nExecution: {execution.profile_name} (calibrated={execution.calibrated})"
     except ValueError:profile_text="unregistered replay instrument"
     self.right["Continuous"].setPlainText(f"Instrument: {symbol}\nMarket Profile: {profile_text}\nCurrent Regime: {item['regime']}\nRegime Strength: {item['strength']:.3f}\nActive/Eligible Strategy: {item['strategy']}\nStatus: {item['signal']} — {item['reason']}\nEvent Risk: {'ACTIVE' if item['event_risk'] else 'OFF'}")
    elif item["kind"]=="feed":self.right["Provider"].setPlainText(f"Provider: {item.get('provider')}\nState: {item.get('state')}\nLast source: {item.get('last_source_timestamp')}\nLast ingestion: {item.get('last_ingestion_timestamp')}")
    elif item["kind"]=="news":self.news_items.append(item);self.news_items=self.news_items[-100:]
    elif item["kind"]=="thesis":self.thesis_items.append(item["thesis"]);self.thesis_items=self.thesis_items[-50:]
    elif item["kind"]=="thesis_state":self.thesis_lifecycle.append(item["lifecycle"]);self.thesis_lifecycle=self.thesis_lifecycle[-100:]
    elif item["kind"]=="state" and item.get("state")=="completed":self.export.setEnabled(True)
   if self.instrument and self.instrument in self.chart_data:
    buf=self.chart_data[self.instrument];points=list(buf.points);candles=list(buf.candles[self.timeframe.currentText()]);line_mode=self.chart_mode.currentText()=="Line";self.curve.setVisible(line_mode or not candles);self.candle_item.setVisible(not line_mode and bool(candles));self.curve.setData([x for x,_ in points],[y for _,y in points]);self.candle_item.setData(candles);
    if points and buf.consume_fit():self.plot.autoRange();self.chart_initialized=True;self.pending_follow_steps=0
    elif candles and self.pending_follow_steps:self.follow_new_candle(candles,self.pending_follow_steps);self.pending_follow_steps=0
   snap=self.controller.snapshot()
   if not snap:return
   p=snap["portfolio"];r=snap["risk"];a=snap["ai"];broker=snap.get("broker",{});sid=(snap.get("replay_session_id") or "--------")[:8];feed=snap.get("feed",{});live=broker.get("environment")=="live";latest_ai=a.get("latest") or {};error=latest_ai.get("error");a["status"]="DISABLED" if a.get("status")=="disabled" else "BUSY" if a.get("queue") else "ENDPOINT OFFLINE" if error in {"URLError","ConnectionError","ConnectionRefusedError","TimeoutError"} else "DEGRADED" if error else "AVAILABLE" if a.get("available") else "MODEL UNAVAILABLE" if a.get("available") is False else "ENDPOINT OFFLINE";self.mode_label.setText("  EXTERNAL LIVE-MONEY  " if live else "  PAPER / PRACTICE  ");self.mode_label.setStyleSheet(f"background:{'#7f1d1d' if live else '#735c0f'};color:#fff3bf;padding:5px;font-weight:bold");self.status.setText(f"SESSION {sid}   BROKER ● {broker.get('connection_state')}   TRADING ● {'ARMED' if broker.get('autonomous_trading') else 'STOPPED'}   FEED ● {feed.get('state','SYNTHETIC')}   AI ● {a['status']}   KILL ● {'ON' if r['kill_switch'] else 'OFF'}")
   if feed:self.right["Provider"].setPlainText(f"Broker: {broker.get('adapter')}\nProfile: {broker.get('profile')}\nEnvironment: {broker.get('environment')}\nAccount: {broker.get('account_id')}\nConnection: {broker.get('connection_state')}\nTrading: {'ARMED' if broker.get('autonomous_trading') else 'STOPPED'}\nFeed: {feed.get('state')}\nLast source: {feed.get('last_source_timestamp')}\nLast ingestion: {feed.get('last_ingestion_timestamp')}\nInstruments: {', '.join(broker.get('instruments',[]))}\nActive instrument: {self.instrument or '—'}")
   self.right["Portfolio"].setPlainText("\n".join(f"{k}: {v:,.4f}" for k,v in p.items() if isinstance(v,(int,float))));self.right["Risk"].setPlainText("\n".join(f"{k}: {v}" for k,v in r.items()));latest=a.get("latest") or {};vm=a.get("validation_metrics") or {};self.right["AI Opinion"].setPlainText(f"AI REASONING — {a.get('decision_mode')}\nValidation: {a.get('validation_mode')}\nProvider: {a.get('provider')}\nModel: {a['model']}\nStatus: {a.get('status')}\nQueue: {a['queue']}\nRequests: {vm.get('requests','—')}  Approval/Veto: {vm.get('approval_rate','—')} / {vm.get('veto_rate','—')}\nErrors/Stale: {vm.get('timeout_or_error_rate','—')} / {vm.get('stale_response_rate','—')}\nAssessment: {latest.get('suggested_action','—')}\nConfidence: {latest.get('confidence','—')}\nLatency: {latest.get('latency_ms','—')} ms\nReasoning: {latest.get('reasoning_summary') or latest.get('summary','—')}\nDownstream: {latest.get('downstream_decision','—')}\nVetoed: {latest.get('vetoed','—')}")
   intelligence=snap.get("news_intelligence",{});thesis=intelligence.get("latest_thesis") or (self.thesis_items[-1] if self.thesis_items else {});focus=intelligence.get("focus",[]);assets=thesis.get("affected_assets",[]) if thesis else [];self.right["News Intelligence"].setPlainText("NEWS FEED\n"+"\n".join(f"{x.get('timestamp')} | {x.get('source')} | {x.get('state')} | {x.get('headline')}" for x in self.news_items[-8:])+"\n\nACTIVE MARKET THESES\n"+"\n".join(f"{x.get('thesis_id')} | {x.get('instrument')} | {x.get('bias')} | {x.get('confidence'):.2f} | {x.get('state')} | {x.get('confirmation_state')}" for x in focus)+f"\n\nTHESIS DETAILS\nID: {thesis.get('thesis_id','—')}\nSummary: {thesis.get('event_summary','—')}\nReasoning: {thesis.get('reasoning_summary','—')}\nWarnings: {thesis.get('risk_warnings','—')}\nInvalidation: {thesis.get('invalidation_conditions','—')}\nProvider/model: {thesis.get('provider','—')} / {thesis.get('model_identifier','—')}\nLatency: {thesis.get('latency_ms','—')} ms\nSource: {thesis.get('source','—')}\n\nFOCUSED INSTRUMENTS\n"+"\n".join(f"{x.get('instrument')} {x.get('directional_bias')} relevance={x.get('relevance')} tradable={x.get('tradable')}" for x in assets))
   self.right["Strategy State"].setPlainText("\n".join(f"{k}: {v}" for k,v in snap["strategy"].items())+f"\n\nNO_TRADE: {snap['abstentions']['no_trade']} / {snap['abstentions']['evaluated']}");self.right["Regime"].setPlainText(f"Phase: {snap['phase'] or '—'}\nShock: {snap['shock']}");self.right["Horizons"].setPlainText("\n".join(f"{m}m: {snap['horizons'].get(m, 'not reached')}" for m in (1,5,15,30,60)))
   if snap["macro"]:
    m=snap["macro"];self.macro_watch.setRowCount(1)
    for column,value in enumerate((m["title"],m["source"],m["scheduled_at"],snap["phase"])):self.macro_watch.setItem(0,column,QTableWidgetItem(str(value)))
   self._replace(self.bottom["Orders / Fills"],[[x.get("timestamp",x.get("created_at","")),x.get("fill_id",x.get("order_id","")),x.get("instrument",""),x.get("side",""),x.get("order_type","FILL"),x.get("state","FILLED"),x.get("quantity",""),x.get("quantity",x.get("filled_quantity","")),x.get("remaining_quantity",""),x.get("price",x.get("fill_price","")),x.get("spread_cost",""),x.get("slippage",""),x.get("commission",""),x.get("latency_ms","")] for x in snap["orders"]+snap["fills"]])
   self._replace(self.bottom["Completed Trades"],[[x.get(k,"") for k in ("instrument","direction","entry_time","exit_time","quantity","net_pnl","total_costs","holding_seconds","mae","mfe","exit_reason","strategy_id")] for x in snap["trades"]])
   self.watch.setRowCount(len(snap["prices"]));
   for row,(symbol,value) in enumerate(sorted(snap["prices"].items())):
    for col,text in enumerate((symbol,f"{value['mid']:.5f}","—",f"{value['spread']*100:.3f}%",value['regime'])):self.watch.setItem(row,col,QTableWidgetItem(text))
   table=self.bottom["Event Stream"];rows=sorted(list(self.events.rows),key=projection_sort_key)[-300:];table.setRowCount(len(rows))
   for row,value in enumerate(rows):
    raw=value.get("timestamp");display=datetime.fromtimestamp(raw,timezone.utc).isoformat() if isinstance(raw,(int,float)) else str(raw or "");values=(display,value.get("category"),value.get("instrument"),value.get("description"))
    for col,text in enumerate(values):table.setItem(row,col,QTableWidgetItem(str(text or "")))
  def _append(self,table,values):
   row=table.rowCount();table.insertRow(row)
   for column,value in enumerate(values):table.setItem(row,column,QTableWidgetItem(str(value)))
  def _replace(self,table,rows):
   table.setRowCount(len(rows))
   for row,values in enumerate(rows):
    for column,value in enumerate(values):table.setItem(row,column,QTableWidgetItem(str(value)))
  def _add_marker(self,item):
   concise={"initial_shock":"SHOCK","stabilization":"STABLE","announcement":"EVENT","post_event":"POST"}.get(item["label"],item["label"][:12]);position=.12+(len(self.chart_marker_items)%5)*.14;line=pg.InfiniteLine(pos=item["timestamp"],angle=90,movable=False,label=concise,labelOpts={"position":position},pen=pg.mkPen("#f59e0b" if item["category"]=="MACRO" else "#a78bfa"));line.setToolTip(item["label"]);self.plot.addItem(line);self.chart_marker_items.append(line)
  def _render_markers(self):
   for marker in self.chart_marker_items:self.plot.removeItem(marker)
   self.chart_marker_items=[]
   for marker in self.chart_data[self.instrument].markers:self._add_marker(marker)
  def closeEvent(self,event):self.settings.setValue("size",self.size());self.timer.stop();self.controller.shutdown();event.accept()
 window=Window();window.show();app.exec()
