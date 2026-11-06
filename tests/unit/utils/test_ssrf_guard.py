"""Unit tests for SSRF guard (infra/utils/ssrf_guard.py)."""

from __future__ import annotations

import pytest

from octop.infra.utils.ssrf_guard import (
    UnsafeOutboundUrl,
    host_allowed_for_issuer,
    is_private_or_local_host,
    issuer_base_domain,
    validate_https_url,
)


def test_issuer_base_domain() -> None:
    assert issuer_base_domain("https://mcp.notion.com") == "notion.com"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("10.0.0.1", True),
        ("192.168.1.1", True),
        ("host.docker.internal", True),
        ("nas.local", True),
        ("mcp.example.com", False),
        ("8.8.8.8", False),
    ],
)
def test_is_private_or_local_host(host: str, expected: bool) -> None:
    assert is_private_or_local_host(host) is expected


@pytest.mark.parametrize(
    ("host", "issuer", "allowed"),
    [
        ("mcp.notion.com", "https://mcp.notion.com", True),
        ("api.notion.com", "https://mcp.notion.com", True),
        ("evil.com", "https://mcp.notion.com", False),
        ("notion.com.evil.com", "https://mcp.notion.com", False),
    ],
)
def test_host_allowed_for_issuer(host: str, issuer: str, allowed: bool) -> None:
    assert host_allowed_for_issuer(host, issuer) is allowed


@pytest.mark.parametrize(
    "url",
    [
        "http://mcp.notion.com/token",
        "https://127.0.0.1/token",
        "https://10.0.0.1/token",
        "https://localhost/token",
        "https://169.254.169.254/latest/meta-data",
    ],
)
def test_validate_https_url_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(UnsafeOutboundUrl):
        validate_https_url(url, field="token_endpoint")


def test_validate_https_url_accepts_public_host() -> None:
    assert (
        validate_https_url("https://mcp.notion.com/.well-known/oauth-authorization-server")
        == "https://mcp.notion.com/.well-known/oauth-authorization-server"
    )


@pytest.mark.parametrize(
    "host",
    [
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.1.1",
        "[::1]",
        "[fd00::1]",
        "[fe80::1]",
        "[::ffff:192.168.1.1]",
    ],
)
async def test_intranet_model_accepts_only_explicit_local_networks(host):
    from octop.infra.utils.ssrf_guard import resolve_intranet_endpoint

    name, address = await resolve_intranet_endpoint(f"https://{host}/v1")
    assert name == address == host.strip("[]")


@pytest.mark.parametrize(
    "host",
    ["8.8.8.8", "0.0.0.0", "192.0.2.1", "224.0.0.1", "[::]", "[2001:db8::1]", "[::ffff:8.8.8.8]"],
)
async def test_intranet_model_rejects_public_reserved_and_unspecified_ips(host):
    from octop.infra.utils.ssrf_guard import resolve_intranet_endpoint

    with pytest.raises(UnsafeOutboundUrl):
        await resolve_intranet_endpoint(f"https://{host}/v1")


@pytest.mark.parametrize("answers", [[], ["10.0.0.1", "8.8.8.8"], None])
async def test_intranet_model_dns_fails_closed(monkeypatch, answers):
    import socket

    from octop.infra.utils.ssrf_guard import resolve_intranet_endpoint

    def resolve(*args, **kwargs):
        if answers is None:
            raise socket.gaierror("unreachable")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))
            for ip in answers
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(UnsafeOutboundUrl):
        await resolve_intranet_endpoint("https://enterprise.test/v1")


async def test_intranet_model_validates_the_transport_idna_hostname(monkeypatch):
    import socket

    import httpx

    from octop.infra.utils.ssrf_guard import resolve_intranet_endpoint

    url = "https://faß.example/v1"
    queried = []

    def resolve(host, *args, **kwargs):
        queried.append(host)
        # The legacy IDNA spelling is private, but the actual request host is public.
        ip = "10.0.0.1" if host == "fass.example" else "8.8.8.8"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(UnsafeOutboundUrl):
        await resolve_intranet_endpoint(url)
    assert queried == [httpx.URL(url).raw_host.decode("ascii")]


async def test_strict_model_transport_never_resolves_an_unvalidated_host(monkeypatch):
    from unittest.mock import AsyncMock

    from httpcore._backends.auto import AutoBackend

    from octop.infra.utils.ssrf_guard import _PinnedNetworkBackend

    connect = AsyncMock()
    monkeypatch.setattr(AutoBackend, "connect_tcp", connect)
    backend = _PinnedNetworkBackend("model.example", "10.0.0.1", strict_host=True)
    with pytest.raises(UnsafeOutboundUrl):
        await backend.connect_tcp("different.example", 443)
    connect.assert_not_called()
    await backend.connect_tcp("model.example", 443)
    assert connect.call_args.args == ("10.0.0.1", 443)
