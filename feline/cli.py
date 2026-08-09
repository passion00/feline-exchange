from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from feline.config import load_config
from feline.logging_setup import configure_logging
from feline.runtime import FelineRuntime
from feline.replay.engine import CSVReplayProvider
from feline.replay.report import calculate_report
from feline.research.experiments import create_experiment,parameter_grid,walk_forward_windows
import csv,tomllib


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="feline")
    result.add_argument("--config", type=Path, default=Path("config/feline.toml"))
    subs = result.add_subparsers(dest="command", required=True)
    subs.add_parser("status")
    run = subs.add_parser("run")
    run.add_argument("--duration", type=float)
    paper = subs.add_parser("paper")
    paper.add_argument("--duration", type=float)
    subs.add_parser("stop", help="activate the persistent emergency-stop marker")
    replay=subs.add_parser("replay")
    replay.add_argument("csv",type=Path); replay.add_argument("--speed",default="max"); replay.add_argument("--strategy",default="reference",choices=["reference"]); replay.add_argument("--seed",type=int,default=0); replay.add_argument("--report",type=Path)
    experiment=subs.add_parser("experiment");experiment.add_argument("dataset",type=Path);experiment.add_argument("--grid",type=Path,required=True);experiment.add_argument("--max-runs",type=int,default=16)
    walk=subs.add_parser("walk-forward");walk.add_argument("dataset",type=Path);walk.add_argument("--train",type=int,required=True);walk.add_argument("--test",type=int,required=True)
    subs.add_parser("doctor")
    subs.add_parser("gui")
    return result


def main() -> None:
    args = parser().parse_args()
    config = load_config(args.config)
    configure_logging(Path(config.log_directory))
    stop_file = Path("data/EMERGENCY_STOP")
    if args.command == "stop":
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text("Emergency stop requested. Remove deliberately before restarting.\n")
        print("Emergency stop activated; new runtime starts are blocked.")
        return
    if args.command == "status":
        from feline.storage.database import Database
        db=Database(Path(config.database_path)); health=db.health(); db.close()
        print(json.dumps({"mode":config.mode,"emergency_stop":stop_file.exists(),"database":config.database_path,"health":health},indent=2))
        return
    if args.command=="doctor":
        from feline.storage.database import Database
        db=Database(Path(config.database_path));report=db.integrity_report();db.close();print(json.dumps(report,indent=2));raise SystemExit(0 if report["ok"] else 1)
    if args.command=="gui":
        from feline.gui.app import run_gui
        run_gui();return
    if stop_file.exists():
        raise SystemExit("Emergency stop is active (data/EMERGENCY_STOP).")
    if args.command in {"experiment","walk-forward"}:
        from feline.storage.database import Database
        db=Database(Path(config.database_path))
        if args.command=="experiment":
            grid=tomllib.loads(args.grid.read_text()).get("grid",{});results=[]
            for parameters in parameter_grid(grid,args.max_runs):
                exp=create_experiment("reference",str(args.dataset),parameters);db.save_experiment(exp,"running")
                try:
                    strategy_values={**config.strategy.__dict__,**parameters};run_config=__import__('dataclasses').replace(config,strategy=type(config.strategy)(**strategy_values));run=FelineRuntime(run_config,provider=CSVReplayProvider(args.dataset,"max",exp.seed),recover=False)
                    async def research_run():
                        await run.run();await run.finalize_replay();await run.stop()
                    asyncio.run(research_run());costs=run.broker.portfolio_state();report=calculate_report(exp.initial_equity,run.equity_history,run.trade_pnls,run.exposure_samples,run.tick_count,costs,run_config.paper.replay_end_policy).to_dict();run.database.close();db.save_experiment(exp,"completed",report);results.append({"id":exp.experiment_id,"parameters":parameters,"net_pnl":report["net_pnl"]})
                except Exception as exc:db.save_experiment(exp,"failed",error=type(exc).__name__);results.append({"id":exp.experiment_id,"parameters":parameters,"error":type(exc).__name__})
            print(json.dumps(results,indent=2))
        else:
            with args.dataset.open() as handle:timestamps=[row["timestamp"] for row in csv.DictReader(handle)]
            exp=create_experiment("reference",str(args.dataset),{"walk_forward":True});db.save_experiment(exp)
            windows=walk_forward_windows(timestamps,args.train,args.test)
            for window in windows:db.save_walk_forward(exp.experiment_id,window,{"separated":True})
            db.save_experiment(exp,"completed",{"windows":len(windows)});print(json.dumps({"experiment_id":exp.experiment_id,"windows":windows},indent=2))
        db.close();return
    provider=CSVReplayProvider(args.csv,args.speed,args.seed) if args.command=="replay" else None
    runtime = FelineRuntime(config,provider=provider,recover=args.command!="replay")
    async def execute() -> None:
        try:
            await runtime.run(getattr(args,"duration",None))
            if args.command=="replay":await runtime.finalize_replay()
        finally:
            await runtime.stop()
            runtime.database.close()
    print("Feline Exchange v0.5 starting in PAPER/RESEARCH mode (no live broker exists).")
    asyncio.run(execute())
    if args.command=="replay":
        costs=runtime.broker.portfolio_state();costs["turnover"]=sum(f.gross_value for f in runtime.broker.fills)
        report=calculate_report(config.paper.initial_cash,runtime.equity_history,runtime.trade_pnls,runtime.exposure_samples,runtime.tick_count,costs,config.paper.replay_end_policy)
        encoded=json.dumps(report.to_dict(),indent=2)
        if args.report: args.report.write_text(encoded+"\n",encoding="utf-8")
        print(encoded)


if __name__ == "__main__":
    main()
