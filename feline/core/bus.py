from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .events import Event

E = TypeVar("E", bound=Event)
Handler = Callable[[E], Awaitable[None]]


class EventBus:
    """Typed in-process pub/sub. Slow handlers are isolated in their own tasks."""

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[Handler]] = defaultdict(list)
        self._tasks: set[asyncio.Task] = set()

    def subscribe(self, event_type: type[E], handler: Handler[E]) -> None:
        self._handlers[event_type].append(handler)

    async def publish(self, event: Event) -> None:
        for event_type, handlers in tuple(self._handlers.items()):
            if isinstance(event, event_type):
                for handler in tuple(handlers):
                    task = asyncio.create_task(handler(event))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        while self._tasks:
            tasks = tuple(self._tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks.difference_update(task for task in tasks if task.done())
