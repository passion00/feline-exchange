from __future__ import annotations

def run_gui():
 try:
  from PySide6.QtCore import QSettings,Qt,QTimer
  from PySide6.QtGui import QAction,QKeySequence
  from PySide6.QtWidgets import QApplication,QComboBox,QFileDialog,QHBoxLayout,QLabel,QMainWindow,QMessageBox,QPushButton,QSplitter,QTableWidget,QTableWidgetItem,QTabWidget,QTextEdit,QVBoxLayout,QWidget
  import pyqtgraph as pg
 except ImportError as exc:raise SystemExit("Qt GUI dependencies missing. Install with: python3 -m pip install -e .") from exc
 from .controller import ChartBuffer,EventProjection,WorkstationController
 app=QApplication([]);app.setStyle("Fusion");app.setStyleSheet("QWidget{background:#11161d;color:#d8dee9;font-size:12px} QTableWidget,QTextEdit{background:#0b1016;border:1px solid #263241} QPushButton{padding:5px 10px;background:#202b38;border:1px solid #34465a} QPushButton:hover{background:#29394a} QTabBar::tab:selected{background:#26384b}")
 class Window(QMainWindow):
  def __init__(self):
   super().__init__();self.setWindowTitle("Feline Exchange v0.7 — Live Workstation Integration");self.settings=QSettings("FelineExchange","Workstation");self.resize(self.settings.value("size",self.size()));self.chart_data={};self.events=EventProjection();self.controller=WorkstationController();self.dataset=None;self.instrument=None
   root=QWidget();layout=QVBoxLayout(root);top=QHBoxLayout();top.addWidget(QLabel("<b>FELINE EXCHANGE</b>"));mode=QLabel("  PAPER / RESEARCH MODE  ");mode.setStyleSheet("background:#735c0f;color:#fff3bf;padding:5px;font-weight:bold");top.addWidget(mode);top.addStretch();self.status=QLabel("RUNTIME ● STOPPED   FEED ● OFF   DB ● OK   AI ● UNKNOWN   DANGER ● OFF   KILL ● OFF");top.addWidget(self.status);layout.addLayout(top)
   split=QSplitter(Qt.Horizontal);left=QTabWidget();self.watch=QTableWidget(0,5);self.watch.setHorizontalHeaderLabels(["Symbol","Last","Change","Spread","Regime"]);left.addTab(self.watch,"Watchlist");left.addTab(QTableWidget(0,4),"Macro Watch");left.addTab(QTableWidget(0,2),"Providers");split.addWidget(left)
   center=QWidget();cl=QVBoxLayout(center);controls=QHBoxLayout();self.open=QPushButton("Open Dataset");self.speed=QComboBox();self.speed.addItems(["0.25","0.5","1","2","5","10","MAX"]);self.play=QPushButton("Start");self.pause=QPushButton("Pause");self.stop=QPushButton("Stop");[controls.addWidget(x) for x in (self.open,self.speed,self.play,self.pause,self.stop)];cl.addLayout(controls);self.plot=pg.PlotWidget();self.plot.showGrid(x=True,y=True,alpha=.2);self.curve=self.plot.plot(pen=pg.mkPen("#55b7ff",width=2));cl.addWidget(self.plot);split.addWidget(center)
   right=QTabWidget();self.right={};
   for name in ("Portfolio","Risk","Regime","AI Opinion","Strategy State","Horizons"):self.right[name]=QTextEdit();self.right[name].setReadOnly(True);right.addTab(self.right[name],name)
   split.addWidget(right);split.setSizes([260,700,300]);layout.addWidget(split,4)
   bottom=QTabWidget();self.bottom={};
   for name in ("Event Stream","Signals","Orders / Fills","Completed Trades","Diagnostics"):self.bottom[name]=QTableWidget(0,4);self.bottom[name].setHorizontalHeaderLabels(["Time","Category","Instrument","Summary"]);bottom.addTab(self.bottom[name],name)
   layout.addWidget(bottom,2);self.setCentralWidget(root)
   emergency=QPushButton("EMERGENCY STOP");emergency.setStyleSheet("background:#7f1d1d;font-weight:bold");top.addWidget(emergency);emergency.clicked.connect(self.emergency);self.open.clicked.connect(self.choose);self.play.clicked.connect(self.start_replay);self.pause.clicked.connect(self.toggle_pause);self.stop.clicked.connect(self.controller.stop);self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(100)
   act=QAction(self);act.setShortcut(QKeySequence("Ctrl+O"));act.triggered.connect(self.choose);self.addAction(act);space=QAction(self);space.setShortcut(QKeySequence(Qt.Key_Space));space.triggered.connect(self.toggle_pause);self.addAction(space)
  def choose(self):
   path,_=QFileDialog.getOpenFileName(self,"Open replay dataset","","Replay (*.csv *.jsonl)");
   if path:self.dataset=path
  def start_replay(self):
   if self.dataset:self.controller.start_replay(self.dataset,self.speed.currentText())
  def toggle_pause(self):self.controller.resume() if self.controller.replay.state.value=="paused" else self.controller.pause();self.pause.setText("Resume" if self.controller.replay.state.value=="paused" else "Pause")
  def emergency(self):
   if QMessageBox.question(self,"Confirm emergency stop","Activate deterministic PAPER kill switch?",QMessageBox.Yes|QMessageBox.No)==QMessageBox.Yes:
    self.controller.emergency_stop();self.status.setText("KILL SWITCH ● ACTIVE — PAPER MODE")
  def refresh(self):
   for item in self.controller.drain():
    if item["kind"]=="tick":
     self.instrument=self.instrument or item["instrument"];buf=self.chart_data.setdefault(item["instrument"],ChartBuffer());buf.add(item["timestamp"],item["price"])
    elif item["kind"]=="event":self.events.add(item["timestamp"],item["category"],item["summary"],item["instrument"],item.get("payload"))
   if self.instrument and self.instrument in self.chart_data:
    points=list(self.chart_data[self.instrument].points);self.curve.setData([x for x,_ in points],[y for _,y in points])
   snap=self.controller.snapshot()
   if not snap:return
   p=snap["portfolio"];r=snap["risk"];a=snap["ai"];self.status.setText(f"RUNTIME ● {self.controller.replay.state.value.upper()}   FEED ● SYNTHETIC   DB ● OK   AI ● {'OK' if a['available'] else 'UNAVAILABLE'}   DANGER ● {'ON' if r['danger'] else 'OFF'}   KILL ● {'ON' if r['kill_switch'] else 'OFF'}")
   self.right["Portfolio"].setPlainText("\n".join(f"{k}: {v:,.4f}" for k,v in p.items() if isinstance(v,(int,float))));self.right["Risk"].setPlainText("\n".join(f"{k}: {v}" for k,v in r.items()));self.right["AI Opinion"].setPlainText(f"AI OPINION — advisory only\nModel: {a['model']}\nAvailable: {a['available']}\nQueue: {a['queue']}")
   self.watch.setRowCount(len(snap["prices"]));
   for row,(symbol,value) in enumerate(sorted(snap["prices"].items())):
    for col,text in enumerate((symbol,f"{value['mid']:.5f}","—",f"{value['spread']*100:.3f}%",value['regime'])):self.watch.setItem(row,col,QTableWidgetItem(text))
   table=self.bottom["Event Stream"];rows=list(self.events.rows)[-300:];table.setRowCount(len(rows))
   for row,value in enumerate(rows):
    for col,key in enumerate(("timestamp","category","instrument","description")):table.setItem(row,col,QTableWidgetItem(str(value.get(key) or "")))
  def closeEvent(self,event):self.settings.setValue("size",self.size());self.timer.stop();self.controller.shutdown();event.accept()
 window=Window();window.show();app.exec()
