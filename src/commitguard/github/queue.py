"""Event queue abstraction between the webhook receiver and scan workers.

The webhook handler persists a :class:`~commitguard.github.storage.ScanJob`
first and then puts its ID on the queue, so the queue is only a wake-up
signal: a full queue, a crashed worker or a restart loses nothing, because
:meth:`~commitguard.github.storage.SqliteStateStore.recoverable_jobs` returns
every queued or abandoned job again. That keeps the in-process implementation
honest and lets Redis, RabbitMQ, SQS or Kafka implement the same protocol later.
"""

import queue
from typing import Protocol

DEFAULT_QUEUE_SIZE = 10_000


class EventQueue(Protocol):
    def put(self, job_id: str) -> bool:
        """Enqueue without blocking. False if the queue is full (the job stays stored)."""
        ...

    def get(self, timeout: float) -> str | None:
        """The next job ID, or None after ``timeout`` seconds."""
        ...

    def size(self) -> int: ...


class InProcessEventQueue:
    def __init__(self, maxsize: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue: queue.Queue[str] = queue.Queue(maxsize=maxsize)

    def put(self, job_id: str) -> bool:
        try:
            self._queue.put_nowait(job_id)
        except queue.Full:
            return False
        return True

    def get(self, timeout: float) -> str | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def size(self) -> int:
        return self._queue.qsize()
