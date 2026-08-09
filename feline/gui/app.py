from __future__ import annotations
import tkinter as tk
from tkinter import filedialog,messagebox,ttk
from .viewmodel import DashboardViewModel

class FelineGUI:
 def __init__(self,root):
  self.root=root;self.vm=DashboardViewModel();root.title("Feline Exchange v0.5 — PAPER / RESEARCH ONLY");root.geometry("1200x760");self.status=tk.StringVar(value="STOPPED • PAPER / RESEARCH ONLY")
  bar=ttk.Frame(root);bar.pack(fill="x");ttk.Label(bar,textvariable=self.status).pack(side="left",padx=8);ttk.Button(bar,text="Select replay",command=self.select_replay).pack(side="left");ttk.Button(bar,text="Reset filters",command=lambda:None).pack(side="left");ttk.Button(bar,text="EMERGENCY STOP",command=self.emergency).pack(side="right")
  tabs=ttk.Notebook(root);tabs.pack(fill="both",expand=True)
  for name in ("Dashboard","Market / Chart","Event Stream","Positions & Orders","Macro Events","AI Advisory"):
   frame=ttk.Frame(tabs);tabs.add(frame,text=name);text=tk.Text(frame,bg="#15191f",fg="#d8dee9");text.insert("end",f"{name}\n\nCore events and state appear here when an observer/replay controller is attached.\n");text.config(state="disabled");text.pack(fill="both",expand=True)
 def select_replay(self):filedialog.askopenfilename(title="Select synthetic/research replay dataset")
 def emergency(self):
  if messagebox.askyesno("Confirm emergency stop","Activate the PAPER emergency stop? This cannot disable any risk rule."):open("data/EMERGENCY_STOP","w",encoding="utf-8").write("GUI emergency stop\n");self.status.set("EMERGENCY STOP ACTIVE • PAPER")

def run_gui():
 try:root=tk.Tk()
 except tk.TclError as exc:raise SystemExit(f"GUI requires a local display server: {exc}") from exc
 FelineGUI(root);root.mainloop()
