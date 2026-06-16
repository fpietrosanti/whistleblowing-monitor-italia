"""Anti-anti-scraping HTTP client: Chrome impersonation via curl_cffi.

PA-site WAFs increasingly fingerprint the *client* (TLS/JA3, HTTP/2 settings,
header order), not just the User-Agent. curl_cffi impersonates a real Chrome at
that level, defeating fingerprint-based blocks (a chunk of our 403/406s).

This wraps curl_cffi's AsyncSession behind an httpx-compatible ``.get()`` so the
rest of the pipeline (discovery/probe/policy/rpct/scanner) is unchanged, and it
re-raises failures as the matching ``httpx`` exception classes (with messages
carrying the same tokens) so error classification, the connection funnel and
the retry queue keep working untouched.

IP-based blocks (datacenter IP rejected) are NOT solved here — those need a
residential/VPN egress (see scanner/retry --egress).
"""

from __future__ import annotations

import os

import httpx

try:
    from curl_cffi.requests import AsyncSession

    _HAVE_CURL = True
except Exception:  # pragma: no cover - fallback if not installed
    AsyncSession = None
    _HAVE_CURL = False

IMPERSONATE = os.environ.get("WB_IMPERSONATE", "chrome")


def have_impersonation() -> bool:
    return _HAVE_CURL


def _translate(exc: Exception, url: str):
    """Map a curl_cffi error to the closest httpx exception (same tokens)."""
    code = getattr(exc, "code", None)
    msg = str(exc)
    low = msg.lower()
    # DNS resolution failure -> ConnectError with the token the funnel matches
    if code == 6 or "could not resolve" in low or "couldn't resolve" in low:
        return httpx.ConnectError("Name or service not known", request=None)
    # Timeouts
    if code == 28 or "timed out" in low or "timeout" in low:
        return httpx.ConnectTimeout("", request=None)
    # Connection refused / failed
    if code == 7 or "failed to connect" in low or "connection refused" in low:
        return httpx.ConnectError("All connection attempts failed", request=None)
    # TLS/SSL
    if code in (35, 60) or "ssl" in low or "certificate" in low:
        return httpx.ConnectError(f"SSL error: {msg}"[:200], request=None)
    return httpx.ConnectError(msg[:200] or type(exc).__name__, request=None)


class _Resp:
    """Minimal httpx.Response-compatible view over a curl_cffi response."""

    __slots__ = ("status_code", "text", "content", "url", "headers")

    def __init__(self, r):
        self.status_code = r.status_code
        self.text = r.text
        self.content = r.content
        self.url = str(r.url)
        self.headers = r.headers


class ImpersonatingClient:
    """httpx-compatible async client backed by curl_cffi Chrome impersonation."""

    def __init__(
        self,
        timeout: float = 15.0,
        impersonate: str = IMPERSONATE,
        proxy: str | None = None,
    ):
        self._timeout = timeout
        self._impersonate = impersonate
        self._proxy = proxy
        self._session = AsyncSession()

    async def get(
        self,
        url: str,
        timeout: float | None = None,
        headers: dict | None = None,
        follow_redirects: bool = True,
    ):
        # Let impersonation own the realistic header set (incl. User-Agent);
        # strip a caller-supplied UA so we don't break Chrome's header order.
        extra = None
        if headers:
            extra = {k: v for k, v in headers.items() if k.lower() != "user-agent"}
            extra = extra or None
        kwargs = dict(
            impersonate=self._impersonate,
            timeout=timeout or self._timeout,
            allow_redirects=follow_redirects,
            headers=extra,
        )
        if self._proxy:
            kwargs["proxies"] = {"http": self._proxy, "https": self._proxy}
        try:
            r = await self._session.get(url, **kwargs)
        except Exception as exc:
            raise _translate(exc, url) from exc
        return _Resp(r)

    async def aclose(self):
        try:
            await self._session.close()
        except Exception:
            pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()


def make_client(
    timeout: float = 15.0, headers: dict | None = None, proxy: str | None = None
):
    """Return an impersonating client, or an httpx.AsyncClient fallback.

    The httpx fallback keeps the system working if curl_cffi is unavailable.
    """
    if _HAVE_CURL:
        return ImpersonatingClient(timeout=timeout, proxy=proxy)
    kw = dict(
        timeout=httpx.Timeout(timeout),
        headers=headers or {},
        follow_redirects=True,
        max_redirects=5,
    )
    if proxy:
        kw["proxy"] = proxy
    return httpx.AsyncClient(**kw)
