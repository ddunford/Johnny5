"""SSRF-hardened HTTP fetching — the load-bearing security control of the tool belt.

Johnny chooses URLs to read **autonomously**, so ``web_fetch`` is the phase's #1
SSRF surface: a malicious or redirected URL could try to reach ``localhost``, the
Docker network, ``inference.lan``, or the cloud metadata endpoint
(``169.254.169.254``). This module is the gate that stops that, and it is
deliberately separate from the tool so it can be reviewed + tested in isolation.

The defence, fail-closed at every layer:

1. **Scheme allowlist** — only ``http``/``https``. ``file://``, ``gopher://``,
   ``data:`` … are refused (and re-checked on every redirect hop).
2. **Post-DNS IP deny-list** — the hostname is resolved to its IPs and *every*
   resolved address is classified; the fetch is refused if **any** is non-global
   (private / loopback / link-local / ULA / reserved / multicast / unspecified) or
   the cloud-metadata IP. Allowlist semantics: only a globally-routable unicast
   address passes. Refusing when *any* record is internal closes the split-record
   trick (one public + one internal A record).
3. **Connection pinned to the validated IP** — we don't connect by hostname (which
   would let the OS re-resolve to a *different*, unchecked IP — DNS rebinding).
   We pin httpx's TCP connection to the exact IP we validated, while preserving the
   real hostname for the ``Host`` header and the TLS SNI / certificate check
   (httpcore's ``sni_hostname`` request extension). So TLS stays correct *and* the
   socket can only land on the address we cleared.
4. **Redirects handled manually, re-validated per hop** — auto-follow is OFF; each
   ``Location`` is resolved + re-validated from scratch before the next connect, so
   a public URL that ``302``s to an internal IP is **blocked at the hop**, not after
   the connection is already open. Hops are capped.
5. **Size + time caps** — the body is streamed and counted, aborted past the byte
   cap; the whole request is under a wall-clock timeout.

Everything that varies (resolver, the httpx client, the caps) is injectable, so the
deny-list logic is unit-testable with a stub resolver + ``httpx.MockTransport`` and
the real socket path is exercised only by the ``@live`` leg.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from foundation.observability import get_logger

_log = get_logger("brain.effectors.safe_http")

# The only schemes Johnny may fetch. Anything else (file/gopher/data/ftp…) is refused.
ALLOWED_SCHEMES = frozenset({"http", "https"})

# The cloud metadata endpoints — already caught by the link-local/ULA rules, but
# named explicitly because they are the canonical SSRF prize (IAM creds, user-data).
_METADATA_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})

# HTTP status codes that carry a redirect we re-validate before following.
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

# A conservatively broad Accept; identity encoding so the byte cap counts real bytes
# (a gzip bomb can't smuggle a huge decompressed body under the cap).
_ACCEPT = "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"


class SsrfError(Exception):
    """A fetch (or one of its redirect hops) targets a host that isn't safe to reach.

    Raised by the guard and caught by the ``web_fetch`` tool, which turns it into a
    graceful failed ``ToolResult`` (the action is audited, the heartbeat lives on) —
    it is never allowed to silently fall through to an open connection.
    """


# A resolver maps a hostname to its IP strings; injectable so tests are deterministic.
Resolver = Callable[[str], Awaitable[Sequence[str]]]


async def system_resolver(host: str) -> list[str]:
    """Resolve ``host`` to its A/AAAA addresses via the event loop's getaddrinfo."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    out: list[str] = []
    for info in infos:
        ip = str(info[4][0]).split("%", 1)[0]  # drop any IPv6 zone id
        if ip not in out:
            out.append(ip)
    return out


def _normalise(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse an IP, unwrapping IPv4-mapped IPv6 (``::ffff:10.0.0.1``) to the v4 rules."""
    ip = ipaddress.ip_address(raw)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def ip_is_blocked(raw: str) -> bool:
    """True if connecting to ``raw`` must be refused (fail-closed on anything unsure).

    Allowlist at heart: a globally-routable unicast address passes; everything else
    — unparseable, private, loopback, link-local, ULA, reserved, multicast,
    unspecified, or the metadata IP — is blocked. The explicit flag checks are
    defence-in-depth behind ``is_global`` so a future Python registry change can
    only make us *more* restrictive, never accidentally permissive.
    """
    try:
        ip = _normalise(raw)
    except ValueError:
        return True
    if str(ip) in _METADATA_IPS:
        return True
    if not ip.is_global:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


@dataclass(frozen=True)
class ValidatedTarget:
    """A URL that has passed the SSRF gate — ready to connect, pinned to ``connect_ip``."""

    url: str
    scheme: str
    host: str
    port: int
    connect_ip: str
    host_header: str


async def validate_target(url: str, *, resolver: Resolver) -> ValidatedTarget:
    """Validate one URL (scheme + post-DNS IP deny-list); raise ``SsrfError`` if unsafe.

    Resolves the hostname and refuses if *any* resolved IP is non-global. Returns the
    validated IP to pin the connection to, plus the original host for the ``Host``
    header + TLS SNI. Called for the initial URL **and** for every redirect hop.
    """
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SsrfError(f"scheme {scheme or '(none)'!r} is not allowed (only http/https)")
    if parsed.username or parsed.password:
        raise SsrfError("embedded URL credentials are not allowed")
    host = parsed.hostname
    if not host:
        raise SsrfError("URL has no host")

    try:
        port = parsed.port
    except ValueError as exc:  # malformed port in the netloc
        raise SsrfError("URL has an invalid port") from exc
    port = port or (443 if scheme == "https" else 80)

    # An IP literal needs no DNS; a name is resolved and every record checked.
    try:
        ipaddress.ip_address(host)
        candidates: list[str] = [host]
    except ValueError:
        candidates = list(await resolver(host))
    if not candidates:
        raise SsrfError(f"could not resolve host {host!r}")
    for ip in candidates:
        if ip_is_blocked(ip):
            raise SsrfError(f"host {host!r} resolves to a blocked address ({ip})")
    connect_ip = candidates[0]

    default_port = 443 if scheme == "https" else 80
    bracket = ":" in host  # bare IPv6 literal host needs bracketing in the header
    host_label = f"[{host}]" if bracket else host
    host_header = host_label if port == default_port else f"{host_label}:{port}"
    return ValidatedTarget(
        url=url,
        scheme=scheme,
        host=host,
        port=port,
        connect_ip=connect_ip,
        host_header=host_header,
    )


@dataclass(frozen=True)
class FetchOutcome:
    """The raw result of a safe fetch — extraction/decoding is the caller's job."""

    final_url: str
    status_code: int
    content_type: str
    body: bytes
    truncated: bool


# A client factory lets tests inject an ``httpx.AsyncClient(transport=MockTransport(...))``.
ClientFactory = Callable[[], httpx.AsyncClient]


class SafeFetcher:
    """Fetch a public URL through the SSRF gate (pinned IP, per-hop re-validation, caps).

    Stateless per call: each ``fetch`` opens a client, walks the redirect chain
    validating every hop, pins each connection to the cleared IP, and returns the
    first non-redirect response (body capped to ``max_bytes``). Operational failures
    surface as exceptions (``SsrfError`` / ``httpx`` errors) for the tool to render as
    a graceful ``ToolResult``; nothing here ever connects to an unvalidated address.
    """

    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        client_factory: ClientFactory | None = None,
        max_redirects: int = 5,
        max_bytes: int = 2_000_000,
        timeout_seconds: float = 10.0,
        user_agent: str = "Johnny5/1.0",
    ) -> None:
        self._resolver = resolver
        self._client_factory = client_factory
        self._max_redirects = max_redirects
        self._max_bytes = max_bytes
        self._timeout = timeout_seconds
        self._user_agent = user_agent

    def _new_client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        # follow_redirects stays OFF — we re-validate every hop ourselves.
        return httpx.AsyncClient(timeout=self._timeout, follow_redirects=False)

    async def fetch(self, url: str) -> FetchOutcome:
        """Walk the (validated, pinned) redirect chain and return the final response."""
        current = url
        async with self._new_client() as client:
            for _ in range(self._max_redirects + 1):
                target = await validate_target(current, resolver=self._resolver)
                request = self._build_request(client, target)
                response = await client.send(request, stream=True)
                try:
                    location = response.headers.get("location")
                    if response.status_code in _REDIRECT_CODES and location:
                        current = urljoin(current, location)
                        _log.info("safe_http.redirect", to_host=urlsplit(current).hostname)
                        continue
                    body, truncated = await self._read_capped(response)
                    return FetchOutcome(
                        final_url=str(response.url),
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type", ""),
                        body=body,
                        truncated=truncated,
                    )
                finally:
                    await response.aclose()
        raise SsrfError(f"too many redirects (cap {self._max_redirects})")

    def _build_request(self, client: httpx.AsyncClient, target: ValidatedTarget) -> httpx.Request:
        """Build a request pinned to the validated IP, with correct Host + TLS SNI.

        The connection origin is the *IP* (so the socket can't be re-resolved
        elsewhere), while the ``Host`` header and ``sni_hostname`` carry the real
        hostname so vhost routing + TLS certificate verification stay correct.
        """
        connect_url = httpx.URL(target.url).copy_with(host=target.connect_ip, port=target.port)
        headers = {
            "Host": target.host_header,
            "User-Agent": self._user_agent,
            "Accept": _ACCEPT,
            "Accept-Encoding": "identity",
        }
        return client.build_request(
            "GET",
            connect_url,
            headers=headers,
            extensions={"sni_hostname": target.host},
        )

    async def _read_capped(self, response: httpx.Response) -> tuple[bytes, bool]:
        """Stream the body, stopping at the byte cap (returns ``(body, truncated)``)."""
        chunks: list[bytes] = []
        total = 0
        truncated = False
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            if total >= self._max_bytes:
                truncated = True
                break
        body = b"".join(chunks)[: self._max_bytes]
        return body, truncated
