"""TC-6b.9 — the SSRF gate (the load-bearing security control of Phase 6b).

Pure + deterministic: the resolver is stubbed and the network is an
``httpx.MockTransport``, so no real DNS or sockets are touched. We prove the gate
refuses every internal/private/metadata target — directly, after DNS, and **on a
redirect hop** — while letting genuinely-public hosts through, and that the body is
capped. These run on host pytest (no DB/Redis).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import httpx
import pytest

from brain.effectors.safe_http import (
    Resolver,
    SafeFetcher,
    SsrfError,
    ip_is_blocked,
    validate_target,
)

# ── the IP classifier (the deny-list heart) ──────────────────────────────────


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "0.0.0.0",  # unspecified / "this host"
        "10.0.0.5",  # private (RFC1918)
        "172.16.0.1",  # private
        "192.168.1.1",  # private
        "169.254.169.254",  # link-local — the cloud metadata prize
        "169.254.0.1",  # link-local
        "100.64.0.1",  # carrier-grade NAT (shared address space)
        "::1",  # IPv6 loopback
        "fc00::1",  # IPv6 ULA
        "fe80::1",  # IPv6 link-local
        "::ffff:127.0.0.1",  # IPv4-mapped loopback — must unwrap + block
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "224.0.0.1",  # multicast
        "not-an-ip",  # unparseable → fail closed
    ],
)
def test_internal_and_bogus_ips_are_blocked(ip: str) -> None:
    assert ip_is_blocked(ip) is True


@pytest.mark.parametrize(
    "ip",
    [
        "8.8.8.8",  # Google DNS
        "1.1.1.1",  # Cloudflare DNS
        "93.184.216.34",  # example.com
        "2606:4700:4700::1111",  # public IPv6 (Cloudflare)
        "::ffff:8.8.8.8",  # IPv4-mapped public — unwraps to a global v4
    ],
)
def test_public_ips_are_allowed(ip: str) -> None:
    assert ip_is_blocked(ip) is False


# ── scheme allowlist + post-DNS validation ───────────────────────────────────


def _resolver(mapping: dict[str, list[str]]) -> Resolver:
    async def resolve(host: str) -> Sequence[str]:
        return mapping.get(host, [])

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://evil/",
        "ftp://host/file",
        "data:text/html,hi",
    ],
)
async def test_non_http_schemes_are_refused(url: str) -> None:
    with pytest.raises(SsrfError, match="scheme"):
        await validate_target(url, resolver=_resolver({}))


async def test_url_with_embedded_credentials_is_refused() -> None:
    with pytest.raises(SsrfError, match="credential"):
        await validate_target("http://user:pass@example.com/", resolver=_resolver({}))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
async def test_internal_ip_literals_are_refused_without_dns(url: str) -> None:
    with pytest.raises(SsrfError, match="blocked"):
        await validate_target(url, resolver=_resolver({}))


async def test_hostname_resolving_to_internal_is_refused() -> None:
    # inference.lan-style: a name that points at the private network.
    resolver = _resolver({"inference.lan": ["10.0.0.9"]})
    with pytest.raises(SsrfError, match="blocked"):
        await validate_target("http://inference.lan:8000/", resolver=resolver)


async def test_split_record_with_any_internal_ip_is_refused() -> None:
    # One public + one private A record → fail closed (the connection could land on
    # the private one), so we refuse rather than gamble on which IP is used.
    resolver = _resolver({"sneaky.example": ["93.184.216.34", "10.1.2.3"]})
    with pytest.raises(SsrfError, match="blocked"):
        await validate_target("http://sneaky.example/", resolver=resolver)


async def test_public_host_validates_and_pins_to_its_ip() -> None:
    resolver = _resolver({"example.com": ["93.184.216.34"]})
    target = await validate_target("https://example.com/page", resolver=resolver)
    assert target.connect_ip == "93.184.216.34"
    assert target.host == "example.com"
    assert target.host_header == "example.com"  # default https port → no :port suffix


async def test_non_default_port_is_kept_in_the_host_header() -> None:
    resolver = _resolver({"example.com": ["93.184.216.34"]})
    target = await validate_target("http://example.com:8080/x", resolver=resolver)
    assert target.host_header == "example.com:8080"
    assert target.port == 8080


# ── the fetcher: redirect re-validation + caps (httpx.MockTransport) ──────────


def _fetcher(
    handler: Callable[[httpx.Request], httpx.Response],
    resolver: Resolver,
    **kwargs: object,
) -> SafeFetcher:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        )

    return SafeFetcher(resolver=resolver, client_factory=factory, **kwargs)  # type: ignore[arg-type]


async def test_public_url_redirecting_to_internal_is_blocked_at_the_hop() -> None:
    # The canonical SSRF bypass: a public URL whose response 302s to an internal host.
    # Auto-redirects are OFF and every hop is re-validated, so this dies at hop 2.
    resolver = _resolver({"public.example": ["93.184.216.34"], "internal.example": ["127.0.0.1"]})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "public.example":
            return httpx.Response(302, headers={"location": "http://internal.example/secret"})
        raise AssertionError("must never connect to the internal host")

    with pytest.raises(SsrfError, match="blocked"):
        await _fetcher(handler, resolver).fetch("http://public.example/")


async def test_redirect_to_metadata_endpoint_is_blocked() -> None:
    resolver = _resolver({"public.example": ["93.184.216.34"]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            301, headers={"location": "http://169.254.169.254/latest/meta-data/iam/"}
        )

    with pytest.raises(SsrfError, match="blocked"):
        await _fetcher(handler, resolver).fetch("http://public.example/")


async def test_happy_fetch_returns_body_and_pins_to_validated_ip() -> None:
    resolver = _resolver({"example.com": ["93.184.216.34"]})
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["connect_host"] = request.url.host  # pinned IP, not the hostname
        seen["host_header"] = request.headers["host"]
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>hi</p>")

    outcome = await _fetcher(handler, resolver).fetch("http://example.com/page")

    assert outcome.status_code == 200
    assert outcome.body == b"<p>hi</p>"
    assert seen["connect_host"] == "93.184.216.34"  # connection pinned to the cleared IP
    assert seen["host_header"] == "example.com"  # vhost routing preserved


async def test_oversize_body_is_truncated_at_the_byte_cap() -> None:
    resolver = _resolver({"example.com": ["93.184.216.34"]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1000)

    outcome = await _fetcher(handler, resolver, max_bytes=100).fetch("http://example.com/")

    assert outcome.truncated is True
    assert len(outcome.body) == 100


async def test_redirect_loop_is_capped() -> None:
    resolver = _resolver({"example.com": ["93.184.216.34"]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://example.com/next"})

    with pytest.raises(SsrfError, match="too many redirects"):
        await _fetcher(handler, resolver, max_redirects=2).fetch("http://example.com/")
