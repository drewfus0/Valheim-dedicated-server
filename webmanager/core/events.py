import asyncio
import collections
import json
import threading
import time
from typing import Any, AsyncGenerator, Callable, Deque, Dict, List, Optional, Set, Tuple

DISCRETE_REFRESH_EVENTS: Tuple[str, ...] = (
    "statusChanged",
    "playersUpdated",
    "sessionHistoryUpdated",
    "serverHistoryUpdated",
    "worldSagaUpdated",
    "raidAlertUpdated",
    "configUpdated",
    "refreshBackups",
    "accountsUpdated",
    "mapUpdated",
    "timelineUpdated",
)


def format_sse_frame(
    event_name: str,
    data: Any = None,
    event_id: Optional[int] = None,
) -> str:
    """Format a standards-compliant Server-Sent Events (SSE) frame."""
    if data is None or data == "":
        payload = json.dumps({"ts": round(time.time(), 3)})
    elif isinstance(data, (dict, list, bool, int, float)):
        payload = json.dumps(data)
    else:
        payload = str(data).replace("\r", "").replace("\n", " ")

    lines: List[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    lines.append(f"data: {payload}")
    return "\n".join(lines) + "\n\n"


class EventBus:
    """Thread-safe and asyncio-safe pub/sub broker for Server-Sent Events."""

    def __init__(self, history_maxlen: int = 256, queue_maxsize: int = 128):
        self._lock = threading.RLock()
        self._event_id: int = 0
        self._history_maxlen = history_maxlen
        self._queue_maxsize = queue_maxsize
        self._history: Deque[Tuple[int, str, Any]] = collections.deque(maxlen=history_maxlen)
        self._subscribers: Set[Tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = set()
        self._shutting_down: bool = False

    def reset_shutdown(self) -> None:
        """Clear shutdown state when FastAPI lifespan starts."""
        with self._lock:
            self._shutting_down = False

    def is_shutting_down(self) -> bool:
        """Return True if the server is shutting down or reloading."""
        with self._lock:
            return self._shutting_down

    def signal_shutdown(self) -> None:
        """Mark bus as shutting down and immediately wake all active SSE generators."""
        with self._lock:
            self._shutting_down = True
            subs = list(self._subscribers)

        for queue, loop in subs:
            if not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(self._wake_queue_for_shutdown, queue)
                except RuntimeError:
                    pass

    @staticmethod
    def _wake_queue_for_shutdown(queue: asyncio.Queue) -> None:
        while queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    @staticmethod
    def _enqueue_frame(queue: asyncio.Queue, frame: str) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:
            pass

    def has_subscribers(self) -> bool:
        """Return True when at least one SSE client is actively connected."""
        with self._lock:
            return len(self._subscribers) > 0

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def current_event_id(self) -> int:
        with self._lock:
            return self._event_id

    def subscribe(self) -> Tuple[asyncio.Queue, Tuple[asyncio.Queue, asyncio.AbstractEventLoop]]:
        """Register an asyncio queue for the currently running event loop."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        sub_handle = (queue, loop)
        with self._lock:
            self._subscribers.add(sub_handle)
        return queue, sub_handle

    def unsubscribe(self, sub_handle: Tuple[asyncio.Queue, asyncio.AbstractEventLoop]) -> None:
        """Unregister a subscriber handle."""
        with self._lock:
            self._subscribers.discard(sub_handle)

    def publish(self, event_name: str, data: Any = None) -> int:
        """
        Broadcast an SSE event from any thread or coroutine.
        Discrete events increment the monotonic event_id and are stored in the replay ring buffer;
        ephemeral 'telemetry' frames are streamed directly without polluting replay history.
        """
        is_ephemeral = (event_name == "telemetry")
        with self._lock:
            if is_ephemeral:
                eid: Optional[int] = None
            else:
                self._event_id += 1
                eid = self._event_id
                self._history.append((eid, event_name, data))
            subs = list(self._subscribers)
            curr_id = self._event_id

        if not subs:
            return curr_id

        frame = format_sse_frame(event_name=event_name, data=data, event_id=eid)
        for queue, loop in subs:
            if not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(self._enqueue_frame, queue, frame)
                except RuntimeError:
                    pass
        return curr_id

    def publish_many(self, event_names: List[str], data: Any = None) -> int:
        """Publish multiple distinct event names in order, deduplicated."""
        seen: Set[str] = set()
        last_id = self.current_event_id()
        for name in event_names:
            if name and name not in seen:
                seen.add(name)
                last_id = self.publish(name, data=data)
        return last_id

    def get_missed_frames(self, last_event_id: int) -> List[str]:
        """Return deduplicated SSE frames missed since `last_event_id`."""
        with self._lock:
            if last_event_id >= self._event_id:
                return []

            if not self._history:
                return []

            oldest_id = self._history[0][0]
            if last_event_id < oldest_id - 1:
                # Client fell behind ring buffer; replay full set of refresh triggers at current id
                eid = self._event_id
                return [format_sse_frame(name, {"replayed": True}, event_id=eid) for name in DISCRETE_REFRESH_EVENTS]

            # Coalesce duplicate event names while preserving latest event_id per name
            latest_by_name: Dict[str, Tuple[int, Any]] = {}
            for eid, name, payload in self._history:
                if eid > last_event_id:
                    latest_by_name[name] = (eid, payload)

        ordered = sorted(latest_by_name.items(), key=lambda kv: kv[1][0])
        return [format_sse_frame(name, payload, event_id=eid) for name, (eid, payload) in ordered]

    async def event_stream_generator(
        self,
        request: Optional[Any] = None,
        initial_telemetry_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        last_event_id: Optional[str] = None,
        keepalive_seconds: float = 20.0,
    ) -> AsyncGenerator[str, None]:
        """Async generator yielding live SSE events, initial telemetry, and keepalives."""
        queue, sub_handle = self.subscribe()
        try:
            # 1. Emit initial telemetry & current event_id anchor immediately on connect
            if initial_telemetry_fn is not None:
                try:
                    snapshot = initial_telemetry_fn()
                    yield format_sse_frame("telemetry", snapshot)
                except Exception as e:
                    print(f"[EventBus] Error generating initial telemetry: {e}")

            # 2. Replay missed discrete triggers if reconnecting with Last-Event-ID
            if last_event_id is not None and str(last_event_id).strip().isdigit():
                missed = self.get_missed_frames(int(str(last_event_id).strip()))
                for frame in missed:
                    yield frame
            else:
                # Seed client's lastEventId on initial connect with an inert sync comment/id frame
                curr_id = self.current_event_id()
                yield format_sse_frame("connected", {"event_id": curr_id}, event_id=curr_id)

            # 3. Stream live events until client disconnects or server shuts down
            while not self.is_shutting_down():
                if request is not None:
                    try:
                        if await request.is_disconnected():
                            break
                    except Exception:
                        break

                try:
                    item = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
                    if item is None or self.is_shutting_down():
                        break
                    yield item
                except asyncio.TimeoutError:
                    if self.is_shutting_down():
                        break
                    yield ": keepalive\n\n"
        finally:
            self.unsubscribe(sub_handle)


# Global singleton instance
EVENT_BUS = EventBus()
