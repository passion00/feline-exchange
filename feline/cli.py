from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from feline.config import load_config
from feline.logging_setup import configure_logging
from feline.runtime import FelineRuntime


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
        print(f"mode={config.mode} emergency_stop={stop_file.exists()} database={config.database_path}")
        return
    if stop_file.exists():
        raise SystemExit("Emergency stop is active (data/EMERGENCY_STOP).")
    runtime = FelineRuntime(config)
    async def execute() -> None:
        try:
            await runtime.run(args.duration)
        finally:
            await runtime.stop()
            runtime.database.close()
    print("Feline Exchange v0.1 starting in PAPER mode (no live broker exists).")
    asyncio.run(execute())


if __name__ == "__main__":
    main()

