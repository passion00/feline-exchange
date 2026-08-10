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
    importer=subs.add_parser("import-twelvedata",help="convert a local Twelve Data time_series JSON file to native OHLC JSONL")
    importer.add_argument("input",type=Path);importer.add_argument("output",type=Path);importer.add_argument("--instrument",required=True);importer.add_argument("--interval",default="1min",choices=["1min","5min","15min","1h"]);importer.add_argument("--timezone",default="UTC")
    macro_merge=subs.add_parser("add-macro-event",help="merge a scheduled economic event into replay JSONL")
    macro_merge.add_argument("input",type=Path);macro_merge.add_argument("output",type=Path);macro_merge.add_argument("--timestamp",required=True);macro_merge.add_argument("--event-id",required=True);macro_merge.add_argument("--title",required=True);macro_merge.add_argument("--source",default="federal_reserve");macro_merge.add_argument("--region",default="US");macro_merge.add_argument("--instrument",default="EURUSD")
    research=subs.add_parser("research",help="historical macro and continuous research tools");research.add_argument("action",choices=["validate","inspect","run","summarize","import-directory","corpus","compare","features","continuous"]);research.add_argument("paths",type=Path,nargs="*");research.add_argument("--instrument",default="EURUSD");research.add_argument("--interval",default="1min");research.add_argument("--timezone",default="UTC");research.add_argument("--output-root",type=Path);research.add_argument("--fail-fast",action="store_true");research.add_argument("--central-bank",default="FOMC",choices=["FOMC"]);research.add_argument("--years",type=int,nargs="+");research.add_argument("--provider",default="twelvedata",choices=["twelvedata"]);research.add_argument("--run",action="store_true");research.add_argument("--dry-run",action="store_true");research.add_argument("--force-download",action="store_true");research.add_argument("--skip-download",action="store_true");research.add_argument("--strategy",default="all",choices=["all","trend_pullback","range_mean_reversion","volatility_breakout"]);research.add_argument("--seed",type=int,default=0);research.add_argument("--no-trades",action="store_true")
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
    if args.command=="import-twelvedata":
        from feline.replay.twelvedata import convert_twelvedata_file
        count=convert_twelvedata_file(args.input,args.output,args.instrument,args.interval,args.timezone);print(json.dumps({"candles":count,"output":str(args.output),"timestamp_semantics":"provider datetime=open_time; replay timestamp=close_time"},indent=2));return
    if args.command=="add-macro-event":
        from feline.replay.twelvedata import add_economic_event
        count=add_economic_event(args.input,args.output,args.timestamp,args.event_id,args.title,args.source,args.region,args.instrument);print(json.dumps({"events":count,"output":str(args.output)},indent=2));return
    if args.command=="research":
        if args.action=="continuous":
            if len(args.paths)!=2 or str(args.paths[0])!="run":raise SystemExit("research continuous requires run DATASET")
            from feline.research.continuous import ContinuousConfig,run_continuous_experiment
            result=run_continuous_experiment(args.paths[1],args.instrument,args.strategy,args.seed,args.no_trades,args.output_root or Path("data/reports/continuous"),ContinuousConfig(**config.continuous))
        elif args.action=="features":
            if len(args.paths)<2 or str(args.paths[0]) not in {"build","analyze"}:raise SystemExit("research features requires build EXPERIMENT... or analyze FEATURES.csv")
            if str(args.paths[0])=="build":
                from feline.research.features import build_feature_set
                result=build_feature_set(args.paths[1:],Path("data/reports/features"))
            else:
                if len(args.paths)!=2:raise SystemExit("research features analyze requires one FEATURES.csv")
                from feline.research.features import analyze_features
                result=analyze_features(args.paths[1])
        elif args.action=="corpus":
            if not args.paths or str(args.paths[0]) not in {"build","doctor"}:raise SystemExit("research corpus requires build or doctor")
            if not args.years:raise SystemExit("research corpus requires --years")
            if str(args.paths[0])=="doctor":
                from feline.research.corpus import corpus_doctor
                result=corpus_doctor(args.years,args.instrument);print(json.dumps(result,indent=2));raise SystemExit(0 if result["ok"] else 1)
            from feline.research.corpus import build_corpus
            result=build_corpus(args.years,args.instrument,args.provider,args.run,args.dry_run,args.force_download,args.skip_download,config=config)
        elif args.action=="compare":
            if len(args.paths)<2:raise SystemExit("research compare requires at least two experiment paths")
            from feline.research.compare import compare_experiments
            result=compare_experiments(args.paths)
        elif args.action=="validate":
            from feline.research.engine import validate_manifest
            result=validate_manifest(args.paths[0])
        elif args.action=="inspect":
            from dataclasses import asdict
            from feline.research.registry import inspect_dataset
            result=asdict(inspect_dataset(args.paths[0],args.instrument))
        elif args.action=="import-directory":
            if len(args.paths)!=2:raise SystemExit("research import-directory requires INPUT_DIR OUTPUT_DIR")
            from feline.replay.twelvedata import import_directory
            result=import_directory(args.paths[0],args.paths[1],args.instrument,args.interval,args.timezone)
        elif args.action=="summarize":
            candidate=args.paths[0]/"experiment.json" if args.paths[0].is_dir() else args.paths[0];result=json.loads(candidate.read_text())
        else:
            from feline.research.engine import run_experiment
            result=run_experiment(args.paths[0],config,args.output_root or Path("data/reports/research"),args.fail_fast);result={"experiment":result["experiment"],"aggregate":result["aggregate"],"output_directory":result["output_directory"]}
        print(json.dumps(result,indent=2,default=str));return
    if stop_file.exists():
        raise SystemExit("Emergency stop is active (data/EMERGENCY_STOP).")
    if args.command=="replay":
        from feline.gui.controller import WorkstationController
        controller=WorkstationController(config);controller.start_replay(str(args.csv),"MAX" if str(args.speed).lower()=="max" else args.speed,args.seed);controller.future.result();report=controller.build_report()
        if args.report:
            created=controller.export_report(args.report);report["exported_files"]=[str(x) for x in created]
        print(json.dumps(report,indent=2,default=str));controller.shutdown();return
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
    provider=None
    runtime = FelineRuntime(config,provider=provider,recover=args.command!="replay")
    async def execute() -> None:
        try:
            await runtime.run(getattr(args,"duration",None))
        finally:
            await runtime.stop()
            runtime.database.close()
    print("Feline Exchange v0.11.1 starting in PAPER/RESEARCH mode (no live broker exists).")
    asyncio.run(execute())


if __name__ == "__main__":
    main()
