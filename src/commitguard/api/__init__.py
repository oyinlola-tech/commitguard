"""The CommitGuard dashboard API (``/api/v1``) and dashboard hosting.

::

    WSGI request
      -> request ID, size limits, strict query/cookie/JSON parsing
      -> CORS (explicit allow-list) and security headers
      -> authentication (session cookie)            401 UNAUTHENTICATED / SESSION_EXPIRED
      -> CSRF (Origin + X-CSRF-Token) on writes     403 CSRF_FAILED
      -> rate limits (sign-in, writes, GitHub calls, search)
      -> route permission -> access scope           tenant resolution
      -> handler -> control plane service -> state store
      -> {"data": ..., "meta": ...} | {"error": {"code", "message", "request_id"}}

Like the webhook endpoint it has no web framework dependency. Routes are thin:
they parse input, call :mod:`commitguard.controlplane` services and serialise
the resource models from :mod:`commitguard.controlplane.views`.
"""
