"""E-mail channel.

:class:`EmailProvider` is the interface the notification domain depends on;
:class:`SmtpEmailProvider` is the implementation shipped in this phase (any
SMTP relay, including those of SES, SendGrid or Resend). Provider credentials
come from the environment or a secret file (:mod:`commitguard.notifications.settings`),
never from repository or organization configuration.

Messages are **plain text** only: repository names, rule titles and other
repository-controlled values are never interpreted as HTML. Header values are
set through :class:`email.message.EmailMessage`, which rejects line breaks, so
untrusted text cannot inject headers. ``Message-ID`` is derived from the
delivery's idempotency key, so a retried message can be recognised as the same
message by the relay and by recipients' mail systems.
"""

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate
from typing import Literal, Protocol

from commitguard.notifications.channels.base import DeliveryError, DeliveryReceipt
from commitguard.security.secrets import Secret

SMTP_TIMEOUT_SECONDS = 15.0

type SmtpSecurity = Literal["starttls", "tls", "none"]


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str


def build_message(sender: str, email: OutgoingEmail, idempotency_key: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = email.to
    message["Subject"] = " ".join(email.subject.split())[:200]
    message["Date"] = formatdate(usegmt=True)
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else "commitguard.invalid"
    message["Message-ID"] = f"<{idempotency_key[:64]}@{domain}>"
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(email.text, subtype="plain", charset="utf-8")
    return message


class EmailProvider(Protocol):
    name: str

    def send(self, email: OutgoingEmail, *, idempotency_key: str) -> DeliveryReceipt: ...


class SmtpEmailProvider:
    name = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        security: SmtpSecurity = "starttls",
        username: str | None = None,
        password: Secret | None = None,
        timeout: float = SMTP_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._security = security
        self._username = username
        self._password = password
        self._timeout = timeout

    def send(self, email: OutgoingEmail, *, idempotency_key: str) -> DeliveryReceipt:
        message = build_message(self._sender, email, idempotency_key)
        context = ssl.create_default_context()
        try:
            client: smtplib.SMTP
            if self._security == "tls":
                client = smtplib.SMTP_SSL(
                    self._host, self._port, timeout=self._timeout, context=context
                )
            else:
                client = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
            with client:
                if self._security == "starttls":
                    client.starttls(context=context)
                if self._username and self._password:
                    client.login(self._username, self._password.reveal())
                refused = client.send_message(message)
        except smtplib.SMTPRecipientsRefused:
            raise DeliveryError("recipient_refused", permanent=True) from None
        except smtplib.SMTPAuthenticationError:
            raise DeliveryError("smtp_authentication_failed") from None
        except smtplib.SMTPResponseException as exc:
            permanent = 500 <= exc.smtp_code < 600
            raise DeliveryError(f"smtp_{exc.smtp_code}", permanent=permanent) from None
        except TimeoutError:
            raise DeliveryError("smtp_timeout") from None
        except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
            raise DeliveryError(f"smtp_unavailable_{type(exc).__name__}"[:64]) from None
        if refused:
            raise DeliveryError("recipient_refused", permanent=True)
        return DeliveryReceipt(self.name, str(message["Message-ID"]))
