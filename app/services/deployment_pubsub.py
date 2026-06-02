"""In-process pub/sub bridge between the Celery event listener and the SSE endpoint.

The Celery event listener runs in a daemon thread (started in
``main.py``'s lifespan) and ingests RabbitMQ events synchronously. Each
SSE request produces an asyncio coroutine in the FastAPI event loop that
needs to ``await`` for fresh events scoped to one ``deployment_id``.

This module is the bridge: each SSE coroutine subscribes and gets back an
``asyncio.Queue``; the listener thread looks up all queues for a given
``deployment_id`` and pushes the event into each. Because the push
happens from a non-asyncio thread we use
``loop.call_soon_threadsafe(queue.put_nowait, …)`` rather than
``await queue.put(...)``.

Beyond the live fan-out, the pubsub also keeps a small **per-deployment
ring buffer of recent events** so a client that connects mid-stream
(opening the detail page while the worker is in the middle of phase 8
of 11) can be backfilled with what's already happened. Without this the
SSE endpoint would only show events that occur *after* the connection
was opened, leaving an empty live-tail until the next worker line lands.

Trade-offs compared to a Redis pub/sub:

* Subscribers are confined to **this process** — fine for one backend
  replica, would fan out incorrectly if you scaled to N replicas where
  the listener thread on replica A holds the only RabbitMQ subscription
  while the SSE request landed on replica B.
* The queue is bounded; on overflow the **oldest** entry is discarded
  and a synthetic ``{"type": "task-overflow", ...}`` is pushed in its
  place so the frontend can render a "you missed entries" banner rather
  than silently believing the stream is complete.
* The recent-buffer is also bounded (per deployment, capped lines).
  Memory cost is bounded at ``MAX_DEPLOYMENTS × _RECENT_MAX × ~500B``;
  in practice the buffer is GC'd seconds after the deployment hits a
  terminal state (no active subscribers, no new events).

Replacing this implementation with a Redis-backed adapter (same public
methods) is the upgrade path when the deployment grows.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict, deque
from typing import Any

logger = logging.getLogger(__name__)

# Maximum number of buffered events per subscriber. ~200 is generous for
# a one-screen log tail; if a slow consumer stalls past that, we drop
# the oldest events and tell the frontend.
_QUEUE_MAXSIZE = 200

# Per-deployment recent-events buffer, used to backfill clients that
# connect mid-stream. Sized for a "what's been happening lately" view,
# not a full transcript — the final transcript still lives in the task
# row's ``logs`` column when the worker completes. 500 entries covers
# multiple minutes of typical worker output without unbounded growth.
_RECENT_MAX = 500


class DeploymentPubSub:
    """One-process, in-memory fan-out keyed by ``deployment_id``.

    Construct once at app start and reuse — there's only ever one
    instance per backend process. ``set_loop`` is called from the
    FastAPI lifespan handler so cross-thread pushes go to the right
    event loop.
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        # Per-deployment ring buffer of recent events. Snapshot for
        # mid-stream subscribers. Reset whenever a deployment hits a
        # terminal lifecycle event (succeeded/failed/revoked) so a
        # subsequent run starts with a clean buffer.
        self._recent: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=_RECENT_MAX)
        )
        # Protects both maps. Both the listener thread (publish) and
        # the asyncio loop (subscribe/unsubscribe) touch them.
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Loop binding
    # ------------------------------------------------------------------

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the FastAPI event loop.

        Called once during lifespan startup. ``publish`` cannot do
        anything meaningful until this is set — the listener thread
        would have no loop to schedule the put onto.
        """
        self._loop = loop

    # ------------------------------------------------------------------
    # Subscriber API (called from the asyncio loop)
    # ------------------------------------------------------------------

    def subscribe(self, deployment_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Register a new queue for ``deployment_id`` and return it.

        The caller is responsible for matching every ``subscribe`` with
        an ``unsubscribe`` (use ``try/finally`` around the consumer
        loop) — leaked queues sit in memory until the process restarts.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        with self._lock:
            self._subs[deployment_id].append(queue)
        logger.debug("pubsub: subscribed to %s (now %d subs)", deployment_id, len(self._subs[deployment_id]))
        return queue

    def recent(self, deployment_id: str) -> list[dict[str, Any]]:
        """Snapshot the recent-events ring buffer for backfill.

        Used by the SSE endpoint right after subscribing so a client
        connecting mid-stream sees what happened in the last few
        minutes instead of an empty live tail until the worker emits
        its next line.
        """
        with self._lock:
            buf = self._recent.get(deployment_id)
            return list(buf) if buf else []

    def unsubscribe(self, deployment_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            subs = self._subs.get(deployment_id)
            if not subs:
                return
            try:
                subs.remove(queue)
            except ValueError:
                pass
            if not subs:
                self._subs.pop(deployment_id, None)
        logger.debug("pubsub: unsubscribed from %s", deployment_id)

    # ------------------------------------------------------------------
    # Publisher API (called from the listener thread)
    # ------------------------------------------------------------------

    def publish(self, deployment_id: str, event: dict[str, Any]) -> None:
        """Push ``event`` to every subscriber for ``deployment_id``.

        Threadsafe. If the loop hasn't been bound yet, the event is
        dropped silently — at startup that's fine because no SSE
        endpoint can be open before the loop is up either.

        Also appends to the recent-events ring buffer so future
        subscribers can be backfilled. Terminal lifecycle events
        (``task-succeeded``/``task-failed``/``task-revoked``) clear
        the buffer after the fan-out — by then the task row in the DB
        has the canonical transcript and a new run for the same
        deployment shouldn't inherit the previous run's tail.
        """
        loop = self._loop
        # Always append to the recent buffer, even if the loop isn't up
        # yet. Subscribers that arrive later still benefit, and the
        # listener thread shouldn't depend on FastAPI being ready.
        with self._lock:
            self._recent[deployment_id].append(event)
            queues = list(self._subs.get(deployment_id, ()))

        if loop is not None:
            for queue in queues:
                loop.call_soon_threadsafe(self._enqueue_or_drop, queue, event)

        # Reset the buffer on terminal events so a follow-up run (e.g.
        # destroy after deploy) starts clean. Done after the live
        # fan-out so currently-connected subscribers still see the
        # terminal frame.
        if event.get("type") in ("task-succeeded", "task-failed", "task-revoked"):
            with self._lock:
                self._recent.pop(deployment_id, None)

    @staticmethod
    def _enqueue_or_drop(queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        """Push onto the queue; on full queues drop oldest + signal overflow.

        Runs inside the asyncio loop thread (scheduled via
        ``call_soon_threadsafe``), so manipulating the queue is safe
        without an extra lock.
        """
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            queue.put_nowait(
                {
                    "type": "task-overflow",
                    "message": "live stream backpressure: dropped older events",
                }
            )


# Singleton — imported directly by listener and SSE endpoint.
pubsub = DeploymentPubSub()
