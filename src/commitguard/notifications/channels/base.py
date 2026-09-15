"""What every external delivery channel returns and raises."""

from dataclasses import dataclass


class DeliveryError(Exception):
    """A delivery attempt failed.

    ``code`` is a short, non-sensitive label stored with the delivery record
    (never a provider response body). ``permanent`` failures are not retried.
    """

    def __init__(self, code: str, *, permanent: bool = False) -> None:
        super().__init__(code)
        self.code = code[:64]
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    provider: str
    provider_message_id: str | None
