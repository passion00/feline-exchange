from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "channel": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event_id"):
            data["event_id"] = record.event_id
        return json.dumps(data, ensure_ascii=False)


def configure_logging(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter()
    for channel in ("application", "market", "ai", "strategy", "risk", "execution"):
        logger = logging.getLogger(f"feline.{channel}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            handler = logging.FileHandler(directory / f"{channel}.jsonl", encoding="utf-8")
            handler.setFormatter(formatter)
            logger.addHandler(handler)

