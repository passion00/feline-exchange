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
    if stop_file.exists():
        raise SystemExit("Emergency stop is active (data/EMERGENCY_STOP).")
    provider=CSVReplayProvider(args.csv,args.speed,args.seed) if args.command=="replay" else None
    runtime = FelineRuntime(config,provider=provider,recover=args.command!="replay")
    async def execute() -> None:
        try:
            await runtime.run(getattr(args,"duration",None))
        finally:
            await runtime.stop()
            runtime.database.close()
    print("Feline Exchange v0.2 starting in PAPER mode (no live broker exists).")
    asyncio.run(execute())
    if args.command=="replay":
        report=calculate_report(config.paper.initial_cash,runtime.equity_history,runtime.trade_pnls,runtime.exposure_samples,runtime.tick_count)
        encoded=json.dumps(report.to_dict(),indent=2)
        if args.report: args.report.write_text(encoded+"\n",encoding="utf-8")
        print(encoded)


if __name__ == "__main__":
    main()
