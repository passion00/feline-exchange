from __future__ import annotations
import json,os,signal,subprocess,time
from pathlib import Path
from urllib import request

def ai_health(config)->dict:
    result={"enabled":config.enabled,"provider":config.provider,"endpoint":config.base_url,"endpoint_state":"DISABLED" if not config.enabled else "OFFLINE","model_list_reachable":False,"configured_model":config.model,"model_found":False,"local_model_path":config.local_model_path,"local_model_exists":bool(config.local_model_path and Path(config.local_model_path).exists()),"llama_server_executable":config.llama_server_executable,"llama_server_exists":bool(config.llama_server_executable and Path(config.llama_server_executable).is_file())}
    if not config.enabled:return result
    try:
        with request.urlopen(config.base_url.rstrip("/")+"/v1/models",timeout=min(2.,config.request_timeout_seconds)) as response:data=json.loads(response.read())
        models=[str(x.get("id")) for x in data.get("data",[])];result.update({"endpoint_state":"AVAILABLE" if config.model in models else "MODEL_UNAVAILABLE","model_list_reachable":True,"model_found":config.model in models,"models":models})
    except Exception as exc:result["error"]=type(exc).__name__
    return result

class LocalAIProcessManager:
    def __init__(self,pid_file=Path("data/local_ai_process.json")):self.pid_file=Path(pid_file)
    def start(self,config):
        if not config.llama_server_executable or not config.local_model_path:raise ValueError("local model path and llama-server executable are required")
        executable=Path(config.llama_server_executable).resolve();model=Path(config.local_model_path).resolve()
        if not executable.is_file() or not model.exists():raise FileNotFoundError("configured local AI executable/model does not exist")
        if self.status().get("running"):raise RuntimeError("Feline local AI process is already running")
        from urllib.parse import urlparse
        parsed=urlparse(config.base_url);command=[str(executable),"-m",str(model),"--host",parsed.hostname or "127.0.0.1","--port",str(parsed.port or 8081),"--alias",config.model]
        if config.reasoning_mode=="disabled":command += ["--reasoning-format","none"]
        log=Path("logs/local_ai.log");log.parent.mkdir(parents=True,exist_ok=True);handle=log.open("ab");process=subprocess.Popen(command,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True);handle.close();record={"pid":process.pid,"executable":str(executable),"model":str(model),"command_hash":__import__('hashlib').sha256("\0".join(command).encode()).hexdigest()};self.pid_file.parent.mkdir(parents=True,exist_ok=True);temporary=self.pid_file.with_suffix(".tmp");temporary.write_text(json.dumps(record,sort_keys=True));temporary.replace(self.pid_file);return {**record,"state":"STARTING"}
    def status(self):
        if not self.pid_file.exists():return {"running":False,"state":"STOPPED"}
        try:record=json.loads(self.pid_file.read_text());os.kill(int(record["pid"]),0);cmdline=Path(f"/proc/{record['pid']}/cmdline").read_bytes().decode(errors="ignore");running=record["executable"] in cmdline and record["model"] in cmdline;return {**record,"running":running,"state":"RUNNING" if running else "STALE_PID_RECORD"}
        except Exception:return {"running":False,"state":"STOPPED"}
    def stop(self):
        status=self.status()
        if not status.get("running"):self.pid_file.unlink(missing_ok=True);return {"state":"STOPPED","stopped":False}
        pid=int(status["pid"]);os.kill(pid,signal.SIGTERM)
        for _ in range(30):
            time.sleep(.1)
            try:os.kill(pid,0)
            except ProcessLookupError:self.pid_file.unlink(missing_ok=True);return {"state":"STOPPED","stopped":True,"pid":pid}
        return {"state":"STOPPING","stopped":False,"pid":pid,"message":"process still exiting; PID record retained"}
