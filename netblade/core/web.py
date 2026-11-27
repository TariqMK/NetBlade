"""Opens HTTPS URLs with certificates checked by the operating system (truststore), as a browser does.

Python's own check on Windows only trusts root certificates Windows has already downloaded. Windows
downloads some roots only when a program first needs them, so without truststore a request can fail
with CERTIFICATE_VERIFY_FAILED on a PC that hasn't fetched that root yet.
"""

from __future__ import annotations

import ssl
import urllib.request
from functools import cache

import truststore


@cache
def _context() -> ssl.SSLContext:
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def urlopen(request: urllib.request.Request, timeout: float):
    """urllib.request.urlopen with the operating system's certificate check."""
    return urllib.request.urlopen(request, timeout=timeout, context=_context())
