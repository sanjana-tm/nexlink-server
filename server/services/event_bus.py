"""
NexLink Server — In-Process Event Bus
=======================================
Pub/Sub message bus using asyncio queues.

Architecture:
  Producer  → EventBus.publish(event_type, payload)
              adds to asyncio.Queue
  Processor → background task, reads from queue
              fans out to all subscribers for that event_type
  Subscriber → registered callback coroutines

Why in-process instead of Redis/Kafka?
  Phase 2 targets a single-server deployment. In-process is:
    - Zero external dependencies
    - Zero network latency
    - Zero serialization overhead
    - Easier to debug and test
  Phase 3 can swap the EventBus for an async Redis pub/sub or Kafka
  producer by implementing the same interface.

Backpressure:
  The queue has a max size (default 10,000). If the queue is full,
  publish() raises EventBusFullError. This prevents memory exhaustion
  if subscribers are slow. Producers should handle this gracefully.

Wildcard subscriptions:
  Subscribe to "*" to receive all event types.
  Useful for logging, metrics, and debugging.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from typing import Any

from server.core.exceptions import EventBusFullError

logger = logging.getLogger(__name__)

# Type alias: async callable that receives an event dict
EventCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EventBus:
    """
    Singleton in-process event bus.

    Usage:
        bus = EventBus()
        await bus.start()

        # Subscribe
        async def on_heartbeat(event: dict):
            print(event["payload"])
        bus.subscribe("heartbeat.received", on_heartbeat)
        bus.subscribe("*", log_everything)  # wildcard

        # Publish
        await bus.publish("heartbeat.received", {"device_id": "..."})

        # Stop
        await bus.stop()
    """

    def __init__(self, max_queue_size: int = 10_000) -> None:
        self._subscribers: dict[str, list[EventCallback]] = defaultdict(list)
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_queue_size)
        self._processor_task: asyncio.Task | None = None
        self._running = False
        self._processed_count = 0
        self._error_count = 0

    async def start(self) -> None:
        """Start the background event processor task."""
        self._running = True
        self._processor_task = asyncio.create_task(
            self._process_events(),
            name="event_bus_processor",
        )
        logger.info("EventBus started (queue_size=%d)", self._queue.maxsize)

    async def stop(self) -> None:
        """Drain remaining events and stop the processor."""
        self._running = False
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        logger.info(
            "EventBus stopped. Processed=%d errors=%d",
            self._processed_count,
            self._error_count,
        )

    def subscribe(self, event_type: str, callback: EventCallback) -> None:
        """
        Register a callback for an event type.

        Args:
            event_type: exact type like "heartbeat.received", or "*" for all events.
            callback:   async coroutine function receiving the event dict.
        """
        self._subscribers[event_type].append(callback)
        logger.debug("EventBus: subscribed %s → %s", event_type, callback.__name__)

    def unsubscribe(self, event_type: str, callback: EventCallback) -> None:
        """Remove a previously registered callback."""
        try:
            self._subscribers[event_type].remove(callback)
        except ValueError:
            pass

    async def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        source_device_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """
        Publish an event to the bus.

        Args:
            event_type:       Dot-separated type: "device.connected", etc.
            payload:          Event data dict.
            source_device_id: Optional device_id of the event source.
            session_id:       Optional session_id context.

        Raises:
            EventBusFullError: Queue is at capacity.
        """
        event = {
            "type": event_type,
            "payload": payload,
            "source_device_id": source_device_id,
            "session_id": session_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.error(
                "EventBus queue full (maxsize=%d). Dropping event: %s",
                self._queue.maxsize,
                event_type,
            )
            raise EventBusFullError(
                f"Event bus queue is full ({self._queue.maxsize} items). "
                "The system is under heavy load — retry later."
            )

    async def _process_events(self) -> None:
        """
        Background processor: reads events from queue, fans out to subscribers.

        Runs until cancelled. Each event is dispatched to all matching
        subscribers concurrently (asyncio.gather). Subscriber errors are
        caught and logged — one bad subscriber doesn't break the others.
        """
        logger.debug("EventBus processor task started")
        while True:
            try:
                event = await self._queue.get()
                await self._dispatch(event)
                self._queue.task_done()
                self._processed_count += 1
            except asyncio.CancelledError:
                logger.debug("EventBus processor cancelled")
                break
            except Exception as e:
                self._error_count += 1
                logger.exception("EventBus processor unexpected error: %s", e)

    async def _dispatch(self, event: dict) -> None:
        """Fan out a single event to all matching subscribers."""
        event_type = event["type"]

        # Gather exact-match + wildcard subscribers
        callbacks = (
            self._subscribers.get(event_type, [])
            + self._subscribers.get("*", [])
        )

        if not callbacks:
            return  # No subscribers for this event type

        # Run all callbacks concurrently
        # return_exceptions=True prevents one failure from cancelling others
        results = await asyncio.gather(
            *(cb(event) for cb in callbacks),
            return_exceptions=True,
        )

        for cb, result in zip(callbacks, results):
            if isinstance(result, Exception):
                self._error_count += 1
                logger.error(
                    "EventBus subscriber %s raised %s: %s",
                    cb.__name__,
                    type(result).__name__,
                    result,
                )

    @property
    def stats(self) -> dict[str, int]:
        """Return current bus statistics."""
        return {
            "queue_size": self._queue.qsize(),
            "queue_max": self._queue.maxsize,
            "processed": self._processed_count,
            "errors": self._error_count,
            "subscriber_count": sum(len(v) for v in self._subscribers.values()),
        }


# ── Global singleton ──────────────────────────────────────────────────────────
# Created once at import time; started in lifecycle.py on app startup.
event_bus = EventBus()
