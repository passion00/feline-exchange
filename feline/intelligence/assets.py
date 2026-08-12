from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import tomllib
from typing import Callable
from urllib import request
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = Path(__file__).resolve().parents[1] / "resources" / "ai_catalog.toml"


class AIAssetError(RuntimeError):
    """An expected, operator-actionable local AI asset error."""


@dataclass(frozen=True)
class ModelSpec:
    id: str
    display_name: str
    family: str
    provider: str
    parameters: str
    quantization: str
    repository: str
    url: str
    filename: str
    sha256: str
    size_bytes: int
    recommended_ram_gb: float
    context_length: int
    api_alias: str
    default: bool = False
    capability_notes: str = ""


@dataclass(frozen=True)
class RuntimeSpec:
    version: str
    platform_id: str
    archive: str
    url: str
    sha256: str
    size_bytes: int
    executable: str


@dataclass(frozen=True)
class HardwareInfo:
    operating_system: str
    architecture: str
    total_ram_gb: float | None
    available_ram_gb: float | None
    nvidia_gpu: str | None = None
    nvidia_vram_gb: float | None = None
    apple_silicon: bool = False


class ModelCatalog:
    def __init__(self, path: Path = CATALOG_PATH) -> None:
        raw = tomllib.loads(path.read_text())
        self.schema_version = str(raw["schema_version"])
        self.default_model_id = str(raw["default_model"])
        self.runtime_version = str(raw["runtime"]["version"])
        self.runtime_platforms = dict(raw["runtime"].get("platforms", {}))
        self.models = {row["id"]: ModelSpec(**row) for row in raw.get("models", [])}
        if self.default_model_id not in self.models:
            raise ValueError("AI catalog default model is missing")

    @property
    def default(self) -> ModelSpec:
        return self.models[self.default_model_id]

    def get(self, model_id: str | None) -> ModelSpec:
        selected = model_id or self.default_model_id
        if selected not in self.models:
            raise AIAssetError(f"Unknown model ID: {selected}")
        return self.models[selected]

    @staticmethod
    def platform_id(system: str | None = None, machine: str | None = None) -> str:
        system = (system or platform.system()).lower()
        machine = (machine or platform.machine()).lower()
        aliases = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}
        return f"{system}-{aliases.get(machine, machine)}"

    def runtime_for(self, platform_id: str | None = None) -> RuntimeSpec:
        target = platform_id or self.platform_id()
        row = self.runtime_platforms.get(target)
        if not row:
            raise AIAssetError(
                f"No managed llama.cpp runtime is available for {target}. "
                "Use an external endpoint or configure a custom llama-server executable."
            )
        return RuntimeSpec(self.runtime_version, target, **row)


def resolve_install_path(value: str | Path | None, default_relative: str, root: Path = PROJECT_ROOT) -> Path:
    path = Path(value).expanduser() if value else Path(default_relative)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def detect_hardware() -> HardwareInfo:
    total = available = None
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            values[key] = float(value.strip().split()[0]) / 1024**2
        total, available = values.get("MemTotal"), values.get("MemAvailable")
    except (OSError, ValueError):
        pass
    if total is None and platform.system()=="Darwin":
        try:
            value=subprocess.run(["sysctl","-n","hw.memsize"],capture_output=True,text=True,timeout=2,check=True).stdout.strip();total=float(value)/1024**3
        except (OSError,ValueError,subprocess.SubprocessError):pass
    gpu = None
    vram = None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            first = result.stdout.splitlines()[0]
            gpu, memory = (part.strip() for part in first.rsplit(",", 1))
            vram = float(memory) / 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    machine = platform.machine()
    return HardwareInfo(platform.system(), machine, total, available, gpu, vram, platform.system() == "Darwin" and machine == "arm64")


def model_recommendation(model: ModelSpec, hardware: HardwareInfo) -> tuple[str, str | None]:
    if hardware.total_ram_gb is None:
        return "recommended", "System RAM could not be detected; verify capacity before installation."
    if hardware.total_ram_gb < model.recommended_ram_gb:
        return "demanding", f"{model.display_name} recommends about {model.recommended_ram_gb:g} GB RAM; detected {hardware.total_ram_gb:.1f} GB."
    if hardware.total_ram_gb < model.recommended_ram_gb * 1.5:
        return "recommended", "Expected to fit, with limited memory headroom."
    return "recommended", None


class AssetDownloader:
    """HTTPS downloader with resumable partials and atomic verified completion."""

    def __init__(self, opener=request.urlopen, timeout: float = 60.0, chunk_size: int = 1024 * 1024, retries: int = 3) -> None:
        self.opener, self.timeout, self.chunk_size, self.retries = opener, timeout, chunk_size, retries

    def download(
        self,
        url: str,
        destination: Path,
        expected_sha256: str,
        expected_size: int | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> Path:
        if urlparse(url).scheme != "https":
            raise AIAssetError("AI assets may only be downloaded over HTTPS")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        for attempt in range(self.retries + 1):
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {"User-Agent": "FelineExchange/0.17.6"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            try:
                response = self.opener(request.Request(url, headers=headers), timeout=self.timeout)
                status = getattr(response, "status", None) or getattr(response, "getcode", lambda: 200)()
                if offset and status != 206:
                    offset = 0
                mode = "ab" if offset and status == 206 else "wb"
                total_header = response.headers.get("Content-Range") or response.headers.get("Content-Length")
                total = expected_size
                if total is None and total_header:
                    total = int(total_header.rsplit("/", 1)[-1]) if "/" in total_header else offset + int(total_header)
                with response, partial.open(mode) as handle:
                    downloaded = offset
                    while True:
                        chunk = response.read(self.chunk_size)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if progress:
                            progress({"downloaded": downloaded, "total": total, "percent": downloaded / total * 100 if total else None, "source": url, "destination": str(destination)})
                    handle.flush()
                    os.fsync(handle.fileno())
                break
            except Exception:
                if attempt >= self.retries:
                    raise
                time.sleep(min(4.0, 0.25 * 2**attempt))
        if expected_size is not None and partial.stat().st_size != expected_size:
            raise AIAssetError(f"Downloaded size mismatch for {destination.name}")
        digest = hashlib.sha256()
        with partial.open("rb") as handle:
            for chunk in iter(lambda: handle.read(self.chunk_size), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != expected_sha256.lower():
            raise AIAssetError(f"SHA-256 verification failed for {destination.name}")
        partial.replace(destination)
        return destination


def safe_extract_tar(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle.getmembers():
            target = (root / member.name).resolve()
            if target != root and root not in target.parents:
                raise AIAssetError("Runtime archive contains an unsafe path")
            if member.issym() or member.islnk():
                link_target = (target.parent / member.linkname).resolve()
                if link_target != root and root not in link_target.parents:
                    raise AIAssetError("Runtime archive contains an unsafe link")
        bundle.extractall(destination, filter="data")


class LocalAIAssets:
    def __init__(self, config, root: Path = PROJECT_ROOT, catalog: ModelCatalog | None = None, downloader: AssetDownloader | None = None) -> None:
        self.config, self.root = config, Path(root).resolve()
        self.catalog = catalog or ModelCatalog()
        self.downloader = downloader or AssetDownloader()
        self.models_dir = resolve_install_path(config.models_directory, "models", self.root)
        self.runtime_dir = resolve_install_path(config.runtime_directory, "runtime/llama.cpp", self.root)
        self.preference_path = resolve_install_path(config.preference_path, "data/ai_preferences.json", self.root)

    def preference(self) -> dict:
        try:
            return json.loads(self.preference_path.read_text())
        except (OSError, ValueError):
            return {}

    @property
    def selected_model_id(self) -> str:
        configured=self.config.model_id or self.preference().get("model_id")
        if configured:return configured
        if self.config.custom_model_path or self.preference().get("custom_model_path") or self.config.local_model_path:return "custom"
        return self.catalog.default_model_id

    def selected_model(self) -> ModelSpec:
        return self.catalog.default if self.selected_model_id=="custom" else self.catalog.get(self.selected_model_id)

    def model_path(self) -> Path:
        custom = self.config.custom_model_path or self.preference().get("custom_model_path") or self.config.local_model_path
        if self.selected_model_id == "custom" and custom:
            return resolve_install_path(custom, "", self.root)
        return self.models_dir / self.selected_model().filename

    def runtime_executable(self) -> Path:
        if self.config.llama_server_executable:
            return resolve_install_path(self.config.llama_server_executable, "", self.root)
        manifest = self.runtime_dir / "install" / "feline-runtime.json"
        if manifest.exists():
            try:
                return self.runtime_dir / "install" / json.loads(manifest.read_text())["executable_relative"]
            except (OSError, ValueError, KeyError):
                pass
        matches = list((self.runtime_dir / "install").rglob("llama-server")) if (self.runtime_dir / "install").exists() else []
        return matches[0] if matches else self.runtime_dir / "install" / "llama-server"

    def select_model(self, model_id: str) -> dict:
        if model_id=="custom" and self.selected_model_id=="custom":return {"selected_model":"custom","installed":self.model_path().is_file(),"restart_required":True}
        model = self.catalog.get(model_id)
        self._save_preference({"model_id": model.id})
        return {"selected_model": model.id, "installed": (self.models_dir / model.filename).is_file(), "restart_required": True}

    def select_custom_model(self, path: Path) -> dict:
        target = Path(path).expanduser().resolve()
        if target.suffix.lower() != ".gguf" or not target.is_file():
            raise AIAssetError("Custom model must be an existing .gguf file")
        self._save_preference({"custom_model_path": str(target), "model_id": None})
        return {"custom_model_path": str(target), "installed": True, "restart_required": True}

    def select_provider(self,provider:str,base_url:str|None=None,model_alias:str|None=None)->dict:
        if provider not in {"managed_local","openai_compatible"}:raise AIAssetError("Provider preference must be managed_local or openai_compatible")
        if base_url and urlparse(base_url).scheme not in {"http","https"}:raise AIAssetError("AI endpoint must be an HTTP(S) URL")
        update={"provider":provider}
        if base_url:update["base_url"]=base_url.rstrip("/")
        if model_alias:update["model"]=model_alias
        self._save_preference(update);return {**update,"restart_required":True}

    def import_model(self, source: Path, copy: bool = False) -> dict:
        source = Path(source).expanduser().resolve()
        if source.suffix.lower() != ".gguf" or not source.is_file():
            raise AIAssetError("Imported model must be an existing .gguf file")
        self.models_dir.mkdir(parents=True, exist_ok=True)
        destination = self.models_dir / source.name
        if destination.exists() or destination.is_symlink():
            raise AIAssetError(f"Destination already exists: {destination}")
        if copy:
            temporary = destination.with_name(destination.name + ".part")
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
        else:
            destination.symlink_to(source)
        self._save_preference({"custom_model_path": str(destination), "model_id": None})
        return {"source": str(source), "destination": str(destination), "mode": "copy" if copy else "symlink"}

    def _save_preference(self, update: dict) -> None:
        state = self.preference()
        for key, value in update.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        self.preference_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.preference_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True, indent=2))
        temporary.replace(self.preference_path)

    def install_model(self, progress=None) -> Path:
        if self.selected_model_id == "custom":
            path = self.model_path()
            if not path.is_file():
                raise AIAssetError(f"Custom model does not exist: {path}")
            return path
        model = self.selected_model()
        destination = self.models_dir / model.filename
        if destination.is_file():
            if destination.stat().st_size != model.size_bytes or self._sha256(destination) != model.sha256:
                raise AIAssetError(f"Existing catalog model failed integrity verification: {destination}")
            self._write_model_verification(destination,model)
            return destination
        completed=self.downloader.download(model.url, destination, model.sha256, model.size_bytes, progress);self._write_model_verification(completed,model);return completed

    @staticmethod
    def _sha256(path:Path)->str:
        digest=hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda:handle.read(1024*1024),b""):digest.update(chunk)
        return digest.hexdigest()

    def _write_model_verification(self,path:Path,model:ModelSpec)->None:
        marker=path.with_name(path.name+".verified.json");temporary=marker.with_suffix(".tmp");temporary.write_text(json.dumps({"model_id":model.id,"sha256":model.sha256,"size_bytes":model.size_bytes},sort_keys=True));temporary.replace(marker)

    def install_runtime(self, progress=None, platform_id: str | None = None) -> Path:
        existing = self.runtime_executable()
        if existing.is_file():
            return existing
        spec = self.catalog.runtime_for(platform_id)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        archive = self.runtime_dir / spec.archive
        self.downloader.download(spec.url, archive, spec.sha256, spec.size_bytes, progress)
        staging = self.runtime_dir / ".install-staging"
        install = self.runtime_dir / "install"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        try:
            safe_extract_tar(archive, staging)
            candidates = [path for path in staging.rglob(spec.executable) if path.is_file()]
            if len(candidates) != 1:
                raise AIAssetError("Runtime archive does not contain exactly one llama-server")
            candidates[0].chmod(candidates[0].stat().st_mode | 0o111)
            relative = candidates[0].relative_to(staging)
            (staging / "feline-runtime.json").write_text(json.dumps({"version": spec.version, "platform": spec.platform_id, "archive_sha256": spec.sha256, "executable_relative": str(relative)}, sort_keys=True, indent=2))
            if install.exists():
                raise AIAssetError("Runtime install destination already exists but is incomplete")
            staging.replace(install)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            archive.unlink(missing_ok=True)
        return install / relative

    def install(self, progress=None) -> dict:
        runtime = self.install_runtime(progress)
        model = self.install_model(progress)
        return {"runtime": str(runtime), "model": str(model), "complete": True}

    def status(self, hardware: HardwareInfo | None = None) -> dict:
        model = self.selected_model()
        model_path = self.model_path()
        executable = self.runtime_executable()
        runtime_manifest = self.runtime_dir / "install" / "feline-runtime.json"
        runtime_version = None
        try:
            runtime_version = json.loads(runtime_manifest.read_text()).get("version")
        except (OSError, ValueError):
            pass
        recommendation, warning = model_recommendation(model, hardware or detect_hardware())
        custom=bool(self.config.custom_model_path or self.preference().get("custom_model_path") or self.config.local_model_path)
        if custom:recommendation,warning="user_selected",None
        if model_path.is_file():
            if custom:model_state="INSTALLED"
            elif model_path.stat().st_size!=model.size_bytes:model_state="CORRUPT"
            elif model_path.with_name(model_path.name+".verified.json").is_file():model_state="INSTALLED"
            else:model_state="UNVERIFIED"
        else:model_state="PARTIAL" if model_path.with_name(model_path.name+".part").exists() else "MISSING"
        return {
            "mode": self.config.provider,
            "selected_model": self.selected_model_id,
            "model_display_name": model_path.stem if custom else model.display_name,
            "model_path": str(model_path),
            "model_state": model_state,
            "models_directory": str(self.models_dir),
            "runtime_directory": str(self.runtime_dir),
            "runtime_executable": str(executable),
            "runtime_state": "INSTALLED" if executable.is_file() else "MISSING",
            "runtime_version": runtime_version,
            "runtime_compatible": (None if self.config.llama_server_executable else runtime_version == self.catalog.runtime_version) if executable.is_file() else False,
            "download_incomplete": model_path.with_name(model_path.name + ".part").exists() or (self.runtime_dir / ".install-staging").exists(),
            "recommendation": recommendation,
            "hardware_warning": warning,
        }

    def catalog_rows(self, hardware: HardwareInfo | None = None) -> list[dict]:
        hardware = hardware or detect_hardware()
        rows = []
        for model in self.catalog.models.values():
            recommendation, warning = model_recommendation(model, hardware)
            path=self.models_dir/model.filename
            state="missing" if not path.is_file() else "corrupt" if path.stat().st_size!=model.size_bytes else "installed" if path.with_name(path.name+".verified.json").is_file() else "unverified"
            rows.append({**asdict(model), "status":state, "selected": model.id == self.selected_model_id, "recommendation": recommendation, "warning": warning})
        if self.selected_model_id=="custom":rows.append({"id":"custom","display_name":self.model_path().name,"status":"installed" if self.model_path().is_file() else "missing","selected":True,"recommendation":"user_selected","warning":None,"parameters":"unknown","quantization":"unknown","size_bytes":self.model_path().stat().st_size if self.model_path().is_file() else None})
        return rows
