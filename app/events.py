import asyncio
import json
from typing import Any, Dict, List


class EventBroadcaster:
    """In-memory pub/sub so the dashboard gets pushed updates over SSE."""

    def __init__(self) -> None:
        self._subscribers: List[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers.append(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    async def publish(self, event: str, data: Dict[str, Any]) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        async with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            queue.put_nowait(payload)


broadcaster = EventBroadcaster()
