from __future__ import annotations
import asyncio,os

class ProviderOrchestrator:
    """Runs read-only providers independently with bounded output queues."""
    def __init__(self,health_callback=None)->None:self.providers={};self.tasks={};self.queues={};self.health_callback=health_callback or (lambda *args:None)
    def add(self,name,provider,queue_size:int=128,credential_env:str|None=None)->None:
        if credential_env and not os.environ.get(credential_env):raise ValueError(f"required provider environment variable is unset: {credential_env}")
        self.providers[name]=provider;self.queues[name]=asyncio.Queue(maxsize=queue_size)
    def start(self)->None:
        for name,p in self.providers.items():self.tasks[name]=asyncio.create_task(self._run(name,p))
    async def _run(self,name,provider):
        try:
            self.health_callback(name,"connected")
            async for event in provider.stream():
                try:self.queues[name].put_nowait(event)
                except asyncio.QueueFull:self.health_callback(name,"pressure")
        except Exception as exc:self.health_callback(name,"failed",type(exc).__name__)
        finally:self.health_callback(name,"disconnected")
    async def stop(self)->None:
        for task in self.tasks.values():task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True);self.tasks.clear()
