from __future__ import annotations

def run_gui():
 try:
  from PySide6.QtCore import QSettings,Qt,QTimer,QRectF,QLineF
  from PySide6.QtGui import QAction,QKeySequence,QPainter,QPicture
  from PySide6.QtWidgets import QApplication,QComboBox,QFileDialog,QHBoxLayout,QLabel,QMainWindow,QMessageBox,QPushButton,QSplitter,QTableWidget,QTableWidgetItem,QTabWidget,QTextEdit,QToolTip,QVBoxLayout,QWidget
  import pyqtgraph as pg
 except ImportError as exc:raise SystemExit("Qt GUI dependencies missing. Install with: python3 -m pip install -e .") from exc
 from .controller import ChartBuffer,EventProjection,WorkstationController
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
   super().__init__();self.setWindowTitle("Feline Exchange v0.8.2 — Native OHLC Workstation");self.settings=QSettings("FelineExchange","Workstation");self.resize(self.settings.value("size",self.size()));self.chart_data={};self.events=EventProjection();self.controller=WorkstationController();self.dataset=None;self.instrument=None;self.chart_marker_items=[]
   root=QWidget();layout=QVBoxLayout(root);top=QHBoxLayout();top.addWidget(QLabel("<b>FELINE EXCHANGE</b>"));mode=QLabel("  PAPER / RESEARCH MODE  ");mode.setStyleSheet("background:#735c0f;color:#fff3bf;padding:5px;font-weight:bold");top.addWidget(mode);top.addStretch();self.status=QLabel("RUNTIME ● STOPPED   FEED ● OFF   DB ● OK   AI ● UNKNOWN   DANGER ● OFF   KILL ● OFF");top.addWidget(self.status);layout.addLayout(top)
   split=QSplitter(Qt.Horizontal);left=QTabWidget();self.watch=QTableWidget(0,5);self.watch.setHorizontalHeaderLabels(["Symbol","Last","Change","Spread","Regime"]);left.addTab(self.watch,"Watchlist");self.macro_watch=QTableWidget(0,4);self.macro_watch.setHorizontalHeaderLabels(["Event","Source","Time","Phase"]);left.addTab(self.macro_watch,"Macro Watch");left.addTab(QTableWidget(0,2),"Providers");split.addWidget(left)
   center=QWidget();cl=QVBoxLayout(center);controls=QHBoxLayout();self.open=QPushButton("Open Dataset");self.speed=QComboBox();self.speed.addItems(["0.25","0.5","1","2","5","10","MAX"]);self.chart_mode=QComboBox();self.chart_mode.addItems(["Candles","Line"]);self.timeframe=QComboBox();self.timeframe.addItems(["1m","5m","15m","1h"]);self.play=QPushButton("Start");self.pause=QPushButton("Pause");self.stop=QPushButton("Stop");self.fit=QPushButton("Fit");self.export=QPushButton("Export Replay Report");self.export.setEnabled(False);[controls.addWidget(x) for x in (self.open,self.speed,self.chart_mode,self.timeframe,self.play,self.pause,self.stop,self.fit,self.export)];cl.addLayout(controls);self.plot=pg.PlotWidget(axisItems={"bottom":pg.DateAxisItem()});self.plot.showGrid(x=True,y=True,alpha=.2);self.curve=self.plot.plot(pen=pg.mkPen("#55b7ff",width=2));self.candle_item=CandlestickItem();self.plot.addItem(self.candle_item);cl.addWidget(self.plot);split.addWidget(center)
   right=QTabWidget();self.right={};
   for name in ("Portfolio","Risk","Regime","AI Opinion","Strategy State","Horizons"):self.right[name]=QTextEdit();self.right[name].setReadOnly(True);right.addTab(self.right[name],name)
   split.addWidget(right);split.setSizes([260,700,300]);layout.addWidget(split,4)
   bottom=QTabWidget();self.bottom={};
   columns={"Event Stream":["Time","Category","Instrument","Summary"],"Signals":["Time","Instrument","Strategy","Outcome","Direction","Confidence","Risk","Reason"],"Orders / Fills":["Time","ID","Instrument","Side","Type","State","Quantity","Filled","Remaining","Fill","Spread","Slippage","Commission","Latency"],"Completed Trades":["Instrument","Direction","Entry","Exit","Quantity","Net P/L","Costs","Holding","MAE","MFE","Exit reason","Strategy"],"Diagnostics":["Time","Severity","Component","Summary"]}
   for name,headers in columns.items():self.bottom[name]=QTableWidget(0,len(headers));self.bottom[name].setHorizontalHeaderLabels(headers);bottom.addTab(self.bottom[name],name)
   layout.addWidget(bottom,2);self.setCentralWidget(root)
   emergency=QPushButton("EMERGENCY STOP");emergency.setStyleSheet("background:#7f1d1d;font-weight:bold");top.addWidget(emergency);emergency.clicked.connect(self.emergency);self.open.clicked.connect(self.choose);self.play.clicked.connect(self.start_replay);self.pause.clicked.connect(self.toggle_pause);self.stop.clicked.connect(self.controller.stop);self.fit.clicked.connect(self.fit_chart);self.export.clicked.connect(self.export_report);self.watch.cellClicked.connect(self.select_instrument);self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(100)
   act=QAction(self);act.setShortcut(QKeySequence("Ctrl+O"));act.triggered.connect(self.choose);self.addAction(act);space=QAction(self);space.setShortcut(QKeySequence(Qt.Key_Space));space.triggered.connect(self.toggle_pause);self.addAction(space)
  def choose(self):
   path,_=QFileDialog.getOpenFileName(self,"Open replay dataset","","Replay (*.csv *.jsonl)");
   if path:self.dataset=path
  def start_replay(self):
   if self.dataset:
    self.chart_data.clear();self.chart_marker_items.clear();self.events=EventProjection();self.plot.clear();self.curve=self.plot.plot(pen=pg.mkPen("#55b7ff",width=2));self.candle_item=CandlestickItem();self.plot.addItem(self.candle_item);self.macro_watch.setRowCount(0);self.export.setEnabled(False)
    for table in self.bottom.values():table.setRowCount(0)
    self.controller.start_replay(self.dataset,self.speed.currentText())
  def export_report(self):
   default=f"data/reports/replay_{self.controller.session['replay_session_id'][:8]}.json";path,_=QFileDialog.getSaveFileName(self,"Export replay report",default,"JSON (*.json)")
   if not path:return
   try:
    created=self.controller.export_report(path);QMessageBox.information(self,"Replay report exported",f"Created:\n{created[0]}\n{created[1]}")
   except Exception as exc:self._append(self.bottom["Diagnostics"],["","error","report",str(exc)]);QMessageBox.warning(self,"Report export failed",str(exc))
  def fit_chart(self):
   if self.instrument in self.chart_data:self.chart_data[self.instrument].request_fit();self.plot.enableAutoRange()
  def select_instrument(self,row,column):
   item=self.watch.item(row,0)
   if item:self.instrument=item.text();self.controller.selected_instrument=self.instrument;self.chart_data.setdefault(self.instrument,ChartBuffer()).request_fit();self._render_markers()
  def toggle_pause(self):self.controller.resume() if self.controller.replay.state.value=="paused" else self.controller.pause();self.pause.setText("Resume" if self.controller.replay.state.value=="paused" else "Pause")
  def emergency(self):
   if QMessageBox.question(self,"Confirm emergency stop","Activate deterministic PAPER kill switch?",QMessageBox.Yes|QMessageBox.No)==QMessageBox.Yes:
    self.controller.emergency_stop();self.status.setText("KILL SWITCH ● ACTIVE — PAPER MODE")
  def refresh(self):
   for item in self.controller.drain():
    if item["kind"]=="tick":
     self.instrument=self.instrument or item["instrument"];buf=self.chart_data.setdefault(item["instrument"],ChartBuffer());buf.add(item["timestamp"],item["price"])
    elif item["kind"]=="candle":self.instrument=self.instrument or item["instrument"];self.chart_data.setdefault(item["instrument"],ChartBuffer()).add_candle(item)
    elif item["kind"]=="event":self.events.add(item["timestamp"],item["category"],item["summary"],item["instrument"],item.get("payload"))
    elif item["kind"]=="signal":self._append(self.bottom["Signals"],[item.get(k,"") for k in ("timestamp","instrument","strategy","outcome","direction","confidence","risk","reason")])
    elif item["kind"]=="marker":
     self.events.add(item["timestamp"],item["category"],item["label"],item.get("instrument"),item);self.chart_data.setdefault(item["instrument"],ChartBuffer()).markers.append(item)
     if item["instrument"]==self.instrument:self._add_marker(item)
    elif item["kind"]=="diagnostic":self._append(self.bottom["Diagnostics"],["",item.get("severity","error"),"replay",item.get("summary","")])
    elif item["kind"]=="state" and item.get("state")=="completed":self.export.setEnabled(True)
   if self.instrument and self.instrument in self.chart_data:
    buf=self.chart_data[self.instrument];points=list(buf.points);candles=list(buf.candles[self.timeframe.currentText()]);line_mode=self.chart_mode.currentText()=="Line";self.curve.setVisible(line_mode or not candles);self.candle_item.setVisible(not line_mode and bool(candles));self.curve.setData([x for x,_ in points],[y for _,y in points]);self.candle_item.setData(candles);
    if points and buf.consume_fit():self.plot.autoRange()
   snap=self.controller.snapshot()
   if not snap:return
   p=snap["portfolio"];r=snap["risk"];a=snap["ai"];sid=(snap.get("replay_session_id") or "--------")[:8];self.status.setText(f"SESSION {sid}   RUNTIME ● {self.controller.replay.state.value.upper()}   FEED ● SYNTHETIC   DB ● OK   AI ● {'OK' if a['available'] else 'UNAVAILABLE'}   DANGER ● {'ON' if r['danger'] else 'OFF'}   KILL ● {'ON' if r['kill_switch'] else 'OFF'}")
   self.right["Portfolio"].setPlainText("\n".join(f"{k}: {v:,.4f}" for k,v in p.items() if isinstance(v,(int,float))));self.right["Risk"].setPlainText("\n".join(f"{k}: {v}" for k,v in r.items()));self.right["AI Opinion"].setPlainText(f"AI OPINION — advisory only\nModel: {a['model']}\nAvailable: {a['available']}\nQueue: {a['queue']}")
   self.right["Strategy State"].setPlainText("\n".join(f"{k}: {v}" for k,v in snap["strategy"].items())+f"\n\nNO_TRADE: {snap['abstentions']['no_trade']} / {snap['abstentions']['evaluated']}");self.right["Regime"].setPlainText(f"Phase: {snap['phase'] or '—'}\nShock: {snap['shock']}");self.right["Horizons"].setPlainText("\n".join(f"{m}m: {snap['horizons'].get(m, 'not reached')}" for m in (1,5,15,30,60)))
   if snap["macro"]:
    m=snap["macro"];self.macro_watch.setRowCount(1)
    for column,value in enumerate((m["title"],m["source"],m["scheduled_at"],snap["phase"])):self.macro_watch.setItem(0,column,QTableWidgetItem(str(value)))
   self._replace(self.bottom["Orders / Fills"],[[x.get("timestamp",x.get("created_at","")),x.get("fill_id",x.get("order_id","")),x.get("instrument",""),x.get("side",""),x.get("order_type","FILL"),x.get("state","FILLED"),x.get("quantity",""),x.get("quantity",x.get("filled_quantity","")),x.get("remaining_quantity",""),x.get("price",x.get("fill_price","")),x.get("spread_cost",""),x.get("slippage",""),x.get("commission",""),x.get("latency_ms","")] for x in snap["orders"]+snap["fills"]])
   self._replace(self.bottom["Completed Trades"],[[x.get(k,"") for k in ("instrument","direction","entry_time","exit_time","quantity","net_pnl","total_costs","holding_seconds","mae","mfe","exit_reason","strategy_id")] for x in snap["trades"]])
   self.watch.setRowCount(len(snap["prices"]));
   for row,(symbol,value) in enumerate(sorted(snap["prices"].items())):
    for col,text in enumerate((symbol,f"{value['mid']:.5f}","—",f"{value['spread']*100:.3f}%",value['regime'])):self.watch.setItem(row,col,QTableWidgetItem(text))
   table=self.bottom["Event Stream"];rows=list(self.events.rows)[-300:];table.setRowCount(len(rows))
   for row,value in enumerate(rows):
    for col,key in enumerate(("timestamp","category","instrument","description")):table.setItem(row,col,QTableWidgetItem(str(value.get(key) or "")))
  def _append(self,table,values):
   row=table.rowCount();table.insertRow(row)
   for column,value in enumerate(values):table.setItem(row,column,QTableWidgetItem(str(value)))
  def _replace(self,table,rows):
   table.setRowCount(len(rows))
   for row,values in enumerate(rows):
    for column,value in enumerate(values):table.setItem(row,column,QTableWidgetItem(str(value)))
  def _add_marker(self,item):
   concise={"initial_shock":"SHOCK","stabilization":"STABLE","announcement":"EVENT","post_event":"POST"}.get(item["label"],item["label"][:12]);line=pg.InfiniteLine(pos=item["timestamp"],angle=90,movable=False,label=concise,pen=pg.mkPen("#f59e0b" if item["category"]=="MACRO" else "#a78bfa"));line.setToolTip(item["label"]);self.plot.addItem(line);self.chart_marker_items.append(line)
  def _render_markers(self):
   for marker in self.chart_marker_items:self.plot.removeItem(marker)
   self.chart_marker_items=[]
   for marker in self.chart_data[self.instrument].markers:self._add_marker(marker)
  def closeEvent(self,event):self.settings.setValue("size",self.size());self.timer.stop();self.controller.shutdown();event.accept()
 window=Window();window.show();app.exec()
