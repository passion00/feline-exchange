from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

def metrics_response(snapshot,path:str,method:str="GET"):
    if method!="GET":return 405,{"error":"read_only"}
    if path not in {"/health","/metrics"}:return 404,{"error":"not_found"}
    return 200,snapshot()

def create_metrics_server(snapshot,host="127.0.0.1",port=0):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status,payload=metrics_response(snapshot,self.path,"GET");body=json.dumps(payload,default=str).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):self.send_error(405)
        def log_message(self,*args):pass
    return ThreadingHTTPServer((host,port),Handler)
