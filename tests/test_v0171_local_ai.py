from __future__ import annotations

from contextlib import redirect_stderr
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from feline.cli import main
from feline.config import AIConfig,load_config
from feline.intelligence.assets import AIAssetError,AssetDownloader,HardwareInfo,LocalAIAssets,ModelCatalog,PROJECT_ROOT,model_recommendation,safe_extract_tar
from feline.intelligence.operations import LocalAIProcessManager,ai_health


class Response:
 def __init__(self,data,status=200,headers=None,fail_after=False):self.stream=io.BytesIO(data);self.status=status;self.headers=headers or {};self.fail_after=fail_after;self.reads=0
 def read(self,size=-1):
  self.reads+=1
  if self.fail_after and self.reads>1:raise ConnectionError("interrupted")
  return self.stream.read(size)
 def __enter__(self):return self
 def __exit__(self,*args):return False

def manifest(path,runtime_bytes=b"runtime",model_bytes=b"model"):
 path.write_text(f'''schema_version="1.0"
default_model="qwen3-4b-q4km"
[runtime]
version="b-test"
[runtime.platforms.linux-x86_64]
archive="runtime.tar.gz"
url="https://example.test/runtime.tar.gz"
sha256="{hashlib.sha256(runtime_bytes).hexdigest()}"
size_bytes={len(runtime_bytes)}
executable="llama-server"
[[models]]
id="qwen3-4b-q4km"
display_name="Qwen3 4B Q4_K_M"
family="Qwen3"
provider="Qwen"
parameters="4B"
quantization="Q4_K_M"
repository="Qwen/Qwen3-4B-GGUF"
url="https://example.test/model.gguf"
filename="Qwen3-4B-Q4_K_M.gguf"
sha256="{hashlib.sha256(model_bytes).hexdigest()}"
size_bytes={len(model_bytes)}
recommended_ram_gb=6.0
context_length=32768
api_alias="feline/qwen3-4b-q4km"
default=true
''')
 return ModelCatalog(path)

class CatalogAndPathsTests(unittest.TestCase):
 def test_default_is_official_qwen_quant(self):
  model=ModelCatalog().default;self.assertEqual(model.id,"qwen3-4b-q4km");self.assertEqual(model.filename,"Qwen3-4B-Q4_K_M.gguf");self.assertEqual(model.repository,"Qwen/Qwen3-4B-GGUF")
 def test_repository_relative_paths_do_not_depend_on_cwd(self):
  assets=LocalAIAssets(AIConfig());self.assertEqual(assets.models_dir,PROJECT_ROOT/"models");self.assertEqual(assets.runtime_dir,PROJECT_ROOT/"runtime/llama.cpp")
 def test_installed_missing_and_partial_states(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);custom=root/"custom.gguf";assets=LocalAIAssets(AIConfig(custom_model_path=str(custom)),root=root);self.assertEqual(assets.status()["model_state"],"MISSING");custom.write_bytes(b"x");self.assertEqual(assets.status()["model_state"],"INSTALLED");custom.unlink();custom.with_name(custom.name+".part").write_bytes(b"x");self.assertEqual(assets.status()["model_state"],"PARTIAL")
 def test_selection_persists_and_explicit_config_wins(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);catalog=manifest(root/"catalog.toml");assets=LocalAIAssets(AIConfig(),root=root,catalog=catalog);assets.select_model("qwen3-4b-q4km");self.assertEqual(json.loads(assets.preference_path.read_text())["model_id"],"qwen3-4b-q4km");self.assertEqual(assets.selected_model_id,"qwen3-4b-q4km")
 def test_provider_preference_persists_and_loads(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);preference=root/"preference.json";config_path=root/"config.toml";config_path.write_text(f'''[ai]\npreference_path="{preference}"\n''');assets=LocalAIAssets(AIConfig(preference_path=str(preference)),root=root);assets.select_provider("openai_compatible","https://ai.example.test","remote-model");loaded=load_config(config_path);self.assertEqual(loaded.ai.provider,"openai_compatible");self.assertEqual(loaded.ai.base_url,"https://ai.example.test");self.assertEqual(loaded.ai.model,"remote-model")
 def test_custom_gguf_and_symlink_import(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);source=root/"existing.gguf";source.write_bytes(b"GGUF");assets=LocalAIAssets(AIConfig(),root=root);result=assets.import_model(source);self.assertTrue(Path(result["destination"]).is_symlink());self.assertEqual(assets.model_path().resolve(),source)
 def test_explicit_model_is_not_overridden_by_hardware(self):
  model=ModelCatalog().default;rating,warning=model_recommendation(model,HardwareInfo("Linux","x86_64",2,1));self.assertEqual(rating,"demanding");self.assertIn("detected",warning);self.assertEqual(ModelCatalog().get(model.id),model)
 def test_unsupported_platform_is_actionable(self):
  with self.assertRaisesRegex(AIAssetError,"external endpoint"):
   ModelCatalog().runtime_for("plan9-mips")

class DownloadTests(unittest.TestCase):
 def test_completed_model_download_is_atomic(self):
  with tempfile.TemporaryDirectory() as td:
   data=b"abcdef";target=Path(td)/"model.gguf";AssetDownloader(lambda req,timeout:Response(data,headers={"Content-Length":str(len(data))}),chunk_size=2).download("https://example.test/model",target,hashlib.sha256(data).hexdigest(),len(data));self.assertEqual(target.read_bytes(),data);self.assertFalse(Path(str(target)+".part").exists())
 def test_interrupted_download_retains_partial_only(self):
  with tempfile.TemporaryDirectory() as td:
   target=Path(td)/"model.gguf";downloader=AssetDownloader(lambda req,timeout:Response(b"abcdef",fail_after=True),chunk_size=2,retries=0)
   with self.assertRaises(ConnectionError):downloader.download("https://example.test/model",target,"0"*64)
   self.assertFalse(target.exists());self.assertTrue(Path(str(target)+".part").exists())
 def test_resume_uses_range_and_appends(self):
  with tempfile.TemporaryDirectory() as td:
   data=b"abcdef";target=Path(td)/"model.gguf";partial=Path(str(target)+".part");partial.write_bytes(data[:3]);seen=[]
   def open_(req,timeout):seen.append(req.headers.get("Range"));return Response(data[3:],206,{"Content-Range":"bytes 3-5/6"})
   AssetDownloader(open_,chunk_size=2).download("https://example.test/model",target,hashlib.sha256(data).hexdigest(),len(data));self.assertEqual(seen,["bytes=3-"]);self.assertEqual(target.read_bytes(),data)
 def test_server_without_range_restarts_cleanly(self):
  with tempfile.TemporaryDirectory() as td:
   data=b"fresh";target=Path(td)/"model.gguf";Path(str(target)+".part").write_bytes(b"old");AssetDownloader(lambda req,timeout:Response(data,200),chunk_size=2).download("https://example.test/model",target,hashlib.sha256(data).hexdigest(),len(data));self.assertEqual(target.read_bytes(),data)
 def test_transient_connection_error_retries(self):
  with tempfile.TemporaryDirectory() as td:
   calls=[];data=b"ok"
   def open_(req,timeout):
    calls.append(req)
    if len(calls)==1:raise ConnectionError("temporary")
    return Response(data)
   with patch("feline.intelligence.assets.time.sleep"):
    AssetDownloader(open_,retries=1).download("https://example.test/model",Path(td)/"model.gguf",hashlib.sha256(data).hexdigest())
   self.assertEqual(len(calls),2)
 def test_bad_checksum_rejected_and_never_promoted(self):
  with tempfile.TemporaryDirectory() as td:
   target=Path(td)/"model.gguf"
   with self.assertRaisesRegex(AIAssetError,"SHA-256"):
    AssetDownloader(lambda req,timeout:Response(b"bad")).download("https://example.test/model",target,"0"*64)
   self.assertFalse(target.exists());self.assertTrue(Path(str(target)+".part").exists())

class RuntimeInstallTests(unittest.TestCase):
 def make_archive(self,path,unsafe=False):
  with tarfile.open(path,"w:gz") as bundle:
   body=b"#!/bin/sh\n";info=tarfile.TarInfo("../escape" if unsafe else "llama-test/llama-server");info.size=len(body);info.mode=0o755;bundle.addfile(info,io.BytesIO(body))
  return path.read_bytes()
 def test_path_traversal_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);archive=root/"bad.tar.gz";self.make_archive(archive,True)
   with self.assertRaisesRegex(AIAssetError,"unsafe path"):safe_extract_tar(archive,root/"out")
 def test_runtime_install_is_atomic_and_manifested(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);archive=root/"source.tar.gz";runtime_data=self.make_archive(archive);catalog=manifest(root/"catalog.toml",runtime_data);model=catalog.default;model_data=b"model"
   class Downloader:
    def download(self,url,destination,sha,expected_size=None,progress=None):
     destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(runtime_data if "runtime" in url else model_data);return destination
   assets=LocalAIAssets(AIConfig(),root=root,catalog=catalog,downloader=Downloader());executable=assets.install_runtime(platform_id="linux-x86_64");self.assertTrue(executable.is_file());self.assertTrue(executable.stat().st_mode&0o111);self.assertFalse((assets.runtime_dir/".install-staging").exists());self.assertEqual(json.loads((assets.runtime_dir/"install/feline-runtime.json").read_text())["version"],"b-test")

class ProcessAndHealthTests(unittest.TestCase):
 def installed(self,root):
  config=AIConfig(reasoning_mode="enabled");assets=LocalAIAssets(config,root=root);assets.models_dir.mkdir(parents=True);assets.model_path().write_bytes(b"model");exe=assets.runtime_dir/"install/llama-server";exe.parent.mkdir(parents=True);exe.write_text("#!/bin/sh\n");exe.chmod(0o755);return config,assets
 def test_managed_argv_is_safe_and_complete(self):
  with tempfile.TemporaryDirectory() as td:
   config,assets=self.installed(Path(td));argv,warnings=LocalAIProcessManager(root=Path(td)).build_argv(replace(config,threads=4,gpu_layers=2),assets);self.assertIn("--model",argv);self.assertIn("--ctx-size",argv);self.assertIn("--threads",argv);self.assertIn("--n-gpu-layers",argv);self.assertEqual(warnings,[])
 def test_port_conflict_never_starts_or_kills(self):
  with tempfile.TemporaryDirectory() as td:
   config,_=self.installed(Path(td));manager=LocalAIProcessManager(root=Path(td),popen=lambda *a,**k:self.fail("must not spawn"))
   with patch.object(manager,"port_in_use",return_value=True),patch("feline.intelligence.operations.endpoint_models",return_value={"model_found":False}):
    with self.assertRaisesRegex(AIAssetError,"occupied"):manager.start(config)
 def test_stale_pid_is_safe(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/"pid.json";path.write_text(json.dumps({"pid":99999999,"executable":"x","model":"y"}));self.assertEqual(LocalAIProcessManager(path).status()["state"],"STOPPED");self.assertFalse(path.exists())
 def test_external_endpoint_does_not_require_local_assets(self):
  class Result:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self):return json.dumps({"data":[{"id":"remote"}]}).encode()
  with patch("feline.intelligence.operations.request.urlopen",return_value=Result()):
   result=ai_health(AIConfig(provider="openai_compatible",model="remote"));self.assertEqual(result["runtime_state"],"NOT_REQUIRED");self.assertEqual(result["endpoint_state"],"AVAILABLE")
 def test_doctor_states_local_missing_and_model_mismatch(self):
  with tempfile.TemporaryDirectory() as td:
   config=AIConfig();result=ai_health(config,Path(td));self.assertEqual(result["runtime_state"],"MISSING");self.assertEqual(result["model_state"],"MISSING")
   class Result:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps({"data":[{"id":"different-model"}]}).encode()
   with patch("feline.intelligence.operations.request.urlopen",return_value=Result()):self.assertEqual(ai_health(config,Path(td))["endpoint_state"],"MODEL_UNAVAILABLE")
 def test_stop_targets_only_recorded_verified_process(self):
  with tempfile.TemporaryDirectory() as td:
   manager=LocalAIProcessManager(Path(td)/"pid.json");record={"running":True,"state":"RUNNING","pid":4321}
   with patch.object(manager,"status",return_value=record),patch("feline.intelligence.operations.time.sleep"),patch("feline.intelligence.operations.os.kill",side_effect=[None,ProcessLookupError]) as kill:
    result=manager.stop()
   self.assertTrue(result["stopped"]);self.assertEqual(kill.call_args_list[0].args,(4321,15));self.assertEqual(kill.call_args_list[1].args,(4321,0))
 def test_catalog_rows_are_gui_ready(self):
  with tempfile.TemporaryDirectory() as td:
   row=LocalAIAssets(AIConfig(),root=Path(td)).catalog_rows(HardwareInfo("Linux","x86_64",16,8))[0];self.assertEqual(row["id"],"qwen3-4b-q4km");self.assertIn("status",row);self.assertIn("recommendation",row);self.assertTrue(row["selected"])
 def test_cli_operator_error_has_no_traceback(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);config=root/"config.toml";config.write_text(f'''[ai]\nprovider="managed_local"\nmodels_directory="{root/'missing-models'}"\nruntime_directory="{root/'missing-runtime'}"\npreference_path="{root/'preferences.json'}"\n''');error=io.StringIO()
   with patch.object(sys,"argv",["feline","--config",str(config),"ai","start-local"]),redirect_stderr(error):
    with self.assertRaises(SystemExit) as raised:main()
   self.assertEqual(raised.exception.code,2);self.assertNotIn("Traceback",error.getvalue());self.assertIn("not installed",error.getvalue())

if __name__=="__main__":unittest.main()
