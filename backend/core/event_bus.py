"""In-process asyncio event bus for hot/cold path communication.

The hot path emits events (e.g., UtteranceFinalized) that the cold path
consumes asynchronously. This is a simple pub/sub implementation using
asyncio queues - no external broker needed for a single-host deployment.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from backend.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Event:
    """Base event class."""

    event_type: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str | None = None


@dataclass
class UtteranceFinalized(Event):
    """Emitted when a learner turn completes on the hot path.

    The cold path worker subscribes to this to trigger evaluation.
    """

    event_type: str = "utterance_finalized"
    utterance_id: str = ""
    session_id: str = ""
    user_id: str = ""
    audio_path: str | None = None
    transcript: str = ""
    stt_confidence: float = 0.0
    start_ms: int = 0
    end_ms: int = 0


@dataclass
class AssessmentReady(Event):
    """Emitted after scoring completes on the cold path.

    Dashboard/report layers can subscribe to refresh progress displays.
    """

    event_type: str = "assessment_ready"
    user_id: str = ""
    session_id: str = ""
    assessment_id: str = ""


# Type alias for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """Simple asyncio-based pub/sub event bus.

    Subscribers are async functions that receive events. They must be
    non-blocking and idempotent (events may be retried after deferral).
    """

    def __init__(self, max_queue_size: int = 1000) -> None:
        """Initialize the event bus.

        Args:
            max_queue_size: Maximum pending events before backpressure
        """
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue_size)
        self._running = False
        self._dispatch_task: asyncio.Task[None] | None = None

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe a handler to an event type.

        Args:
            event_type: The event type to subscribe to (e.g., "utterance_finalized")
            handler: Async function to call when event is published
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
        logger.info("event_handler_subscribed", event_type=event_type, handler=handler.__name__)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Unsubscribe a handler from an event type."""
        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(handler)
                logger.info(
                    "event_handler_unsubscribed", event_type=event_type, handler=handler.__name__
                )
            except ValueError:
                pass

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers.

        Events are queued and dispatched asynchronously. This method
        returns immediately unless the queue is full.

        Args:
            event: The event to publish
        """
        try:
            self._queue.put_nowait(event)
            logger.debug(
                "event_published",
                event_type=event.event_type,
                correlation_id=event.correlation_id,
            )
        except asyncio.QueueFull:
            logger.warning(
                "event_queue_full",
                event_type=event.event_type,
                queue_size=self._queue.qsize(),
            )
            # Under backpressure, await with timeout
            await asyncio.wait_for(self._queue.put(event), timeout=5.0)

    async def start(self) -> None:
        """Start the event dispatch loop."""
        if self._running:
            return
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.info("event_bus_started")

    async def stop(self) -> None:
        """Stop the event dispatch loop gracefully."""
        self._running = False
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        logger.info("event_bus_stopped")

    async def _dispatch_loop(self) -> None:
        """Main dispatch loop - runs until stopped."""
        while self._running:
            try:
                # Wait for next event with timeout to allow clean shutdown
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(event)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("event_dispatch_error", error=str(e))

    async def _dispatch(self, event: Event) -> None:
        """Dispatch an event to all subscribers."""
        handlers = self._subscribers.get(event.event_type, [])
        if not handlers:
            logger.debug("event_no_handlers", event_type=event.event_type)
            return

        # Run all handlers concurrently
        tasks = [self._safe_call(handler, event) for handler in handlers]
        await asyncio.gather(*tasks)

    async def _safe_call(self, handler: EventHandler, event: Event) -> None:
        """Call a handler with error isolation."""
        try:
            await handler(event)
        except Exception as e:
            logger.exception(
                "event_handler_error",
                handler=handler.__name__,
                event_type=event.event_type,
                error=str(e),
            )

    @property
    def queue_size(self) -> int:
        """Current number of pending events."""
        return self._queue.qsize()

    @property
    def is_running(self) -> bool:
        """Whether the dispatch loop is running."""
        return self._running
