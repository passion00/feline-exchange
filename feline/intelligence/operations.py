from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from urllib import request
from urllib.parse import urlparse

from .assets import AIAssetError, LocalAIAssets


LOCAL_PROVIDERS = {"managed_local", "local_llama_cpp", "llama_cpp"}


def endpoint_models(config) -> dict:
    result = {"endpoint_state": "DISABLED" if not config.enabled else "OFFLINE", "model_list_reachable": False, "model_found": False}
    if not config.enabled:
        return result
    try:
        with request.urlopen(config.base_url.rstrip("/") + "/v1/models", timeout=min(2.0, config.request_timeout_seconds)) as response:
            data = json.loads(response.read())
        models = [str(item.get("id")) for item in data.get("data", [])]
        result.update({"endpoint_state": "AVAILABLE" if config.model in models else "MODEL_UNAVAILABLE", "model_list_reachable": True, "model_found": config.model in models, "models": models})
    except Exception as exc:
        result["error"] = type(exc).__name__
    return result


def ai_health(config, root: Path | None = None) -> dict:
    result = {"enabled": config.enabled, "provider": config.provider, "endpoint": config.base_url, "configured_model": config.model}
    endpoint=endpoint_models(config)
    if config.provider in LOCAL_PROVIDERS:
        assets = LocalAIAssets(config, root=root) if root else LocalAIAssets(config)
        result.update(assets.status())
        process = LocalAIProcessManager(root=root).status()
        listener=LocalAIProcessManager.port_in_use(config.base_url) and not process.get("running",False)
        result.update({"process_state": process["state"], "process_running": process.get("running", False), "compatible_external_listener":bool(listener and endpoint.get("model_found")),"port_conflict":bool(listener and not endpoint.get("model_found"))})
    else:
        result.update({"mode": "external", "runtime_state": "NOT_REQUIRED", "model_state": "NOT_REQUIRED", "process_state": "EXTERNAL"})
    result.update(endpoint)
    return result


class LocalAIProcessManager:
    def __init__(self, pid_file: Path | None = None, root: Path | None = None, popen=subprocess.Popen, run=subprocess.run) -> None:
        self.root = (root or Path(__file__).resolve().parents[2]).resolve()
        self.pid_file = Path(pid_file) if pid_file else self.root / "data/local_ai_process.json"
        self._popen, self._run = popen, run

    @staticmethod
    def port_in_use(base_url: str) -> bool:
        parsed = urlparse(base_url)
        host, port = parsed.hostname or "127.0.0.1", parsed.port or 8081
        try:
            with socket.create_connection((host, port), timeout=0.15):
                return True
        except OSError:
            return False

    def runtime_supports(self, executable: Path, flag: str) -> bool:
        try:
            result = self._run([str(executable), "--help"], capture_output=True, text=True, timeout=5, check=False)
            return flag in (result.stdout + result.stderr)
        except (OSError, subprocess.SubprocessError):
            return False

    def build_argv(self, config, assets: LocalAIAssets | None = None) -> tuple[list[str], list[str]]:
        assets = assets or LocalAIAssets(config, root=self.root)
        executable, model = assets.runtime_executable(), assets.model_path()
        if not executable.is_file() or not model.is_file():
            missing = []
            if not executable.is_file():
                missing.append("llama.cpp runtime")
            if not model.is_file():
                missing.append("selected model")
            raise AIAssetError("Local AI is not installed. Missing: " + ", ".join(missing) + ". Run: python3 -m feline ai install")
        parsed = urlparse(config.base_url)
        argv = [str(executable), "--model", str(model), "--host", parsed.hostname or "127.0.0.1", "--port", str(parsed.port or 8081), "--ctx-size", str(config.context_size), "--alias", config.model]
        if config.threads is not None:
            argv += ["--threads", str(config.threads)]
        if config.gpu_layers is not None:
            argv += ["--n-gpu-layers", str(config.gpu_layers)]
        warnings = []
        if config.reasoning_mode == "disabled":
            if self.runtime_supports(executable, "--reasoning-format"):
                argv += ["--reasoning-format", "none"]
            else:
                warnings.append("Installed runtime does not advertise --reasoning-format; starting without that option.")
        return argv, warnings

    def start(self, config) -> dict:
        if config.provider not in LOCAL_PROVIDERS:
            raise AIAssetError("AI provider is externally managed; Feline will not start a local server")
        existing = self.status()
        if existing.get("running"):
            raise AIAssetError("Feline local AI process is already running")
        if self.port_in_use(config.base_url):
            health = endpoint_models(config)
            if health.get("model_found"):
                return {"state": "EXTERNAL_COMPATIBLE", "message": "A compatible endpoint already occupies the configured port; it was not modified.", "health": health}
            raise AIAssetError("Configured AI port is already occupied by another process; Feline did not stop or modify it")
        assets = LocalAIAssets(config, root=self.root)
        command, warnings = self.build_argv(config, assets)
        log = self.root / "logs/local_ai.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as handle:
            process = self._popen(command, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        record = {
            "pid": process.pid,
            "executable": command[0],
            "model": str(assets.model_path()),
            "model_id": assets.selected_model_id,
            "port": urlparse(config.base_url).port or 8081,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "command_hash": hashlib.sha256("\0".join(command).encode()).hexdigest(),
        }
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.pid_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, sort_keys=True))
        temporary.replace(self.pid_file)
        return {**record, "state": "STARTING", "warnings": warnings}

    def wait_until_ready(self, config) -> dict:
        deadline = time.monotonic() + config.startup_timeout_seconds
        last = {"endpoint_state": "OFFLINE"}
        while time.monotonic() < deadline:
            last = endpoint_models(config)
            if last.get("model_found"):
                return {"state": "AVAILABLE", "health": last}
            if self.status().get("state") == "STOPPED":
                return {"state": "ERROR", "message": "llama-server exited before becoming ready; inspect logs/local_ai.log", "health": last}
            time.sleep(0.25)
        return {"state": "DEGRADED", "message": "llama-server did not become ready before startup timeout", "health": last}

    def status(self) -> dict:
        if not self.pid_file.exists():
            return {"running": False, "state": "STOPPED"}
        try:
            record = json.loads(self.pid_file.read_text())
            pid = int(record["pid"])
            os.kill(pid, 0)
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore")
            running = record["executable"] in cmdline and record["model"] in cmdline
            return {**record, "running": running, "state": "RUNNING" if running else "STALE_PID_RECORD"}
        except ProcessLookupError:
            self.pid_file.unlink(missing_ok=True)
            return {"running": False, "state": "STOPPED"}
        except Exception:
            return {"running": False, "state": "STALE_PID_RECORD"}

    def stop(self) -> dict:
        status = self.status()
        if not status.get("running"):
            self.pid_file.unlink(missing_ok=True)
            return {"state": "STOPPED", "stopped": False}
        pid = int(status["pid"])
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                self.pid_file.unlink(missing_ok=True)
                return {"state": "STOPPED", "stopped": True, "pid": pid}
        return {"state": "STOPPING", "stopped": False, "pid": pid, "message": "process still exiting; PID record retained"}
