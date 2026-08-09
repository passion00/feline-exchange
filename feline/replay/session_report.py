from __future__ import annotations
from datetime import datetime,timezone
from hashlib import sha256
import json,subprocess
from pathlib import Path
from feline import __version__

SCHEMA_VERSION="1.0"

def file_checksum(path:Path)->str:
 digest=sha256()
 with path.open("rb") as handle:
  for block in iter(lambda:handle.read(1024*1024),b""):digest.update(block)
 return digest.hexdigest()

def git_commit()->str|None:
 try:return subprocess.run(["git","rev-parse","HEAD"],capture_output=True,text=True,timeout=2,check=True).stdout.strip()
 except Exception:return None

def build_replay_report(session:dict,snapshot:dict,records:dict)->dict:
 portfolio=snapshot.get("portfolio",{})
 macro=dict(snapshot.get("macro") or {})
 if macro:macro["actual_replay_timestamp"]=macro.get("scheduled_at")
 return {"schema_version":SCHEMA_VERSION,"metadata":{**session,"feline_version":__version__,"git_commit":git_commit(),"export_timestamp":datetime.now(timezone.utc).isoformat()},"macro_event":macro or None,"macro_analysis":{"phase_transitions":records.get("macro",[]),"shock":snapshot.get("shock"),"strategy":snapshot.get("strategy"),"abstentions":snapshot.get("abstentions",{})},"horizons":snapshot.get("horizons",{}),"signals":records.get("signals",[]),"risk":records.get("risk",[]),"orders":snapshot.get("orders",[]),"fills":snapshot.get("fills",[]),"trades":snapshot.get("trades",[]),"portfolio":{"starting_equity":session.get("starting_equity"),"ending_equity":portfolio.get("equity"),"realized_pnl":portfolio.get("realized_pnl"),"unrealized_pnl":portfolio.get("unrealized_pnl"),"exposure":portfolio.get("exposure"),"maximum_drawdown":portfolio.get("drawdown",0),"execution_costs":sum(float(x.get("commission",0) or 0)+float(x.get("spread_cost",0) or 0)+float(x.get("slippage_amount",0) or 0) for x in snapshot.get("fills",[]))},"ai":{"available":bool(snapshot.get("ai",{}).get("available")),"model":snapshot.get("ai",{}).get("model"),"analyses":records.get("ai",[])},"diagnostics":records.get("diagnostics",[]),"human_summary":summary_text(session,snapshot)}

def summary_text(session:dict,snapshot:dict)->str:
 strategy=snapshot.get("strategy",{});p=snapshot.get("portfolio",{});macro=snapshot.get("macro") or {}
 return f"Replay {session['replay_session_id'][:8]} analyzed {macro.get('title','market data')} using {strategy.get('state','no macro outcome')}. Ending equity: {p.get('equity','n/a')}."

def export_replay_report(report:dict,path:Path)->tuple[Path,Path]:
 if path.exists():raise FileExistsError(path)
 md=path.with_suffix(".md")
 if md.exists():raise FileExistsError(md)
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2,default=str)+"\n",encoding="utf-8")
 meta=report["metadata"];macro=report.get("macro_event") or {};strategy=report["macro_analysis"].get("strategy") or {}
 md.write_text(f"# Feline Replay Report\n\n- Session: `{meta['replay_session_id']}`\n- Dataset: `{meta['dataset_path']}`\n- Dataset SHA-256: `{meta['dataset_checksum']}`\n- Replay range: {meta.get('replay_start_timestamp')} — {meta.get('replay_end_timestamp')}\n- Macro event: {macro.get('title','None')}\n- Strategy outcome: **{strategy.get('state','None')}**\n- Ending equity: {report['portfolio'].get('ending_equity')}\n\nPaper/research simulation only; results do not imply live profitability.\n",encoding="utf-8")
 return path,md
