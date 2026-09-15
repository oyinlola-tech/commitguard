"""Test mode: channels that record deliveries instead of sending them.

With ``COMMITGUARD_NOTIFICATIONS_MODE=test`` (and always when
``COMMITGUARD_ENV=test``) e-mail and webhook deliveries go through the full
pipeline - outbox, preferences, delivery records, retries, audit - but the
final hop is replaced by these in-memory sinks, so a test or staging
environment can never reach a real mailbox or endpoint.
"""

import json
import threading
from dataclasses import dataclass

from commitguard.notifications.channels.base import DeliveryError, DeliveryReceipt
from commitguard.notifications.channels.email import OutgoingEmail
from commitguard.notifications.channels.webhook import WebhookRequest

MAX_RECORDED = 1000


@dataclass(frozen=True, slots=True)
class RecordedEmail:
    email: OutgoingEmail
    idempotency_key: str


class RecordingEmailProvider:
    name = "test-sink"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sent: list[RecordedEmail] = []
        self.failure: DeliveryError | None = None  # tests simulate an outage

    def send(self, email: OutgoingEmail, *, idempotency_key: str) -> DeliveryReceipt:
        with self._lock:
            if self.failure is not None:
                raise self.failure
            self.sent.append(RecordedEmail(email, idempotency_key))
            del self.sent[:-MAX_RECORDED]
        return DeliveryReceipt(self.name, f"test-{idempotency_key[:24]}")


class RecordingWebhookTransport:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: list[WebhookRequest] = []
        self.status = 200

    def post(self, request: WebhookRequest, *, timeout: float) -> int:
        with self._lock:
            self.requests.append(request)
            del self.requests[:-MAX_RECORDED]
            return self.status

    def payloads(self) -> list[dict[str, object]]:
        with self._lock:
            return [json.loads(r.body) for r in self.requests]
