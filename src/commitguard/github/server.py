"""A small threaded HTTP server for development and single-host deployments.

It serves the WSGI application from :mod:`commitguard.github.app` on a local
address (default ``127.0.0.1``). It does not terminate TLS: expose it to
GitHub only through a TLS-terminating reverse proxy or a development tunnel.
Access logs are structured, contain no query strings or headers, and every
connection has a socket timeout.
"""

from collections.abc import Callable, Iterable
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
from wsgiref.types import StartResponse, WSGIEnvironment

from commitguard.observability.logging import get_logger

log = get_logger(__name__)

SOCKET_TIMEOUT_SECONDS = 30


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    allow_reuse_address = True


class _QuietHandler(WSGIRequestHandler):
    timeout = SOCKET_TIMEOUT_SECONDS

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        log.info(
            "http_request",
            method=self.command,
            path=(self.path or "").split("?", 1)[0][:200],
            status=str(code),
        )

    def log_message(self, format: str, *args: object) -> None:
        log.debug("http_server_message")


def serve(
    application: Callable[[WSGIEnvironment, StartResponse], Iterable[bytes]],
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> None:
    with make_server(
        host, port, application, server_class=_ThreadingWSGIServer, handler_class=_QuietHandler
    ) as httpd:
        log.info("http_server_listening", host=host, port=port)
        httpd.serve_forever()
