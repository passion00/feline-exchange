from __future__ import annotations

def run_gui():
 try:
  from PySide6.QtCore import QSettings,Qt,QTimer
  from PySide6.QtGui import QAction,QKeySequence
  from PySide6.QtWidgets import QApplication,QComboBox,QFileDialog,QHBoxLayout,QLabel,QMainWindow,QMessageBox,QPushButton,QSplitter,QTableWidget,QTabWidget,QTextEdit,QVBoxLayout,QWidget
  import pyqtgraph as pg
 except ImportError as exc:raise SystemExit("Qt GUI dependencies missing. Install with: python3 -m pip install -e .") from exc
 from .controller import ChartBuffer,EventProjection,ReplayController,RuntimeThread
 app=QApplication([]);app.setStyle("Fusion");app.setStyleSheet("QWidget{background:#11161d;color:#d8dee9;font-size:12px} QTableWidget,QTextEdit{background:#0b1016;border:1px solid #263241} QPushButton{padding:5px 10px;background:#202b38;border:1px solid #34465a} QPushButton:hover{background:#29394a} QTabBar::tab:selected{background:#26384b}")
 class Window(QMainWindow):
  def __init__(self):
   super().__init__();self.setWindowTitle("Feline Exchange v0.6 — Qt Trading Workstation");self.settings=QSettings("FelineExchange","Workstation");self.resize(self.settings.value("size",self.size()));self.chart_data=ChartBuffer();self.events=EventProjection();self.replay=ReplayController();self.core=RuntimeThread();self.core.start()
   root=QWidget();layout=QVBoxLayout(root);top=QHBoxLayout();top.addWidget(QLabel("<b>FELINE EXCHANGE</b>"));mode=QLabel("  PAPER / RESEARCH MODE  ");mode.setStyleSheet("background:#735c0f;color:#fff3bf;padding:5px;font-weight:bold");top.addWidget(mode);top.addStretch();self.status=QLabel("RUNTIME ● STOPPED   FEED ● OFF   DB ● OK   AI ● UNKNOWN   DANGER ● OFF   KILL ● OFF");top.addWidget(self.status);layout.addLayout(top)
   split=QSplitter(Qt.Horizontal);left=QTabWidget();left.addTab(QTableWidget(0,5),"Watchlist");left.addTab(QTableWidget(0,4),"Macro Watch");left.addTab(QTableWidget(0,2),"Providers");split.addWidget(left)
   center=QWidget();cl=QVBoxLayout(center);controls=QHBoxLayout();self.open=QPushButton("Open Dataset");self.speed=QComboBox();self.speed.addItems(["0.25","0.5","1","2","5","10","MAX"]);self.play=QPushButton("Start");self.pause=QPushButton("Pause");self.stop=QPushButton("Stop");[controls.addWidget(x) for x in (self.open,self.speed,self.play,self.pause,self.stop)];cl.addLayout(controls);self.plot=pg.PlotWidget();self.plot.showGrid(x=True,y=True,alpha=.2);self.curve=self.plot.plot(pen=pg.mkPen("#55b7ff",width=2));cl.addWidget(self.plot);split.addWidget(center)
   right=QTabWidget();[right.addTab(QTextEdit(),name) for name in ("Portfolio","Risk","Regime","AI Opinion","Strategy State","Horizons")];split.addWidget(right);split.setSizes([260,700,300]);layout.addWidget(split,4)
   bottom=QTabWidget();[bottom.addTab(QTableWidget(0,4),name) for name in ("Event Stream","Signals","Orders / Fills","Completed Trades","Diagnostics")];layout.addWidget(bottom,2);self.setCentralWidget(root)
   emergency=QPushButton("EMERGENCY STOP");emergency.setStyleSheet("background:#7f1d1d;font-weight:bold");top.addWidget(emergency);emergency.clicked.connect(self.emergency);self.open.clicked.connect(self.choose);self.play.clicked.connect(self.start_replay);self.pause.clicked.connect(self.toggle_pause);self.stop.clicked.connect(self.replay.stop)
   act=QAction(self);act.setShortcut(QKeySequence("Ctrl+O"));act.triggered.connect(self.choose);self.addAction(act);space=QAction(self);space.setShortcut(QKeySequence(Qt.Key_Space));space.triggered.connect(self.toggle_pause);self.addAction(space)
  def choose(self):
   path,_=QFileDialog.getOpenFileName(self,"Open replay dataset","","Replay (*.csv *.jsonl)");
   if path:self.replay.configure(path,self.speed.currentText())
  def start_replay(self):self.replay.start();self.status.setText("RUNTIME ● REPLAY   FEED ● SYNTHETIC   DB ● OK   AI ● ADVISORY   DANGER ● CORE   KILL ● OFF")
  def toggle_pause(self):self.replay.resume() if self.replay.state.value=="paused" else self.replay.pause();self.pause.setText("Resume" if self.replay.state.value=="paused" else "Pause")
  def emergency(self):
   if QMessageBox.question(self,"Confirm emergency stop","Activate deterministic PAPER kill switch?",QMessageBox.Yes|QMessageBox.No)==QMessageBox.Yes:
    from pathlib import Path;Path("data/EMERGENCY_STOP").write_text("Qt emergency stop\n");self.status.setText("KILL SWITCH ● ACTIVE — PAPER MODE")
  def closeEvent(self,event):self.settings.setValue("size",self.size());self.replay.stop();self.core.stop();event.accept()
 window=Window();window.show();app.exec()
