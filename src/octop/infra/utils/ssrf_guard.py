"""Outbound HTTPS URL validation — mitigates SSRF (CWE-918)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import typing
from functools import partial
from urllib.parse import urlparse, urlunparse

import httpx
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import SOCKET_OPTION, AsyncNetworkStream


class UnsafeOutboundUrl(ValueError):
    """Raised when a URL must not be fetched server-side."""


_INTRANET_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


def _check_intranet_ip(value: str) -> None:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if not any(address in network for network in _INTRANET_NETWORKS):
        raise UnsafeOutboundUrl("model endpoint must resolve only to intranet addresses")


async def resolve_intranet_endpoint(url: str) -> tuple[str, str]:
    """Return a validated hostname and intranet IP for a pinned, no-redirect call."""
    parsed = urlparse(url)
    host = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise UnsafeOutboundUrl("invalid intranet model URL")
    # Use the transport's IDNA rules: stdlib IDNA maps ß to ss, while
    # httpx preserves it as a different ASCII hostname.
    endpoint = httpx.URL(url)
    port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    host = endpoint.raw_host.decode("ascii")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            async with asyncio.timeout(10):
                infos = await asyncio.get_running_loop().run_in_executor(
                    None,
                    partial(
                        socket.getaddrinfo,
                        host,
                        port,
                        type=socket.SOCK_STREAM,
                        proto=socket.IPPROTO_TCP,
                    ),
                )
        except (OSError, TimeoutError):
            raise UnsafeOutboundUrl("cannot resolve intranet model hostname") from None
        if not infos:
            raise UnsafeOutboundUrl("cannot resolve intranet model hostname") from None
        addresses = [str(info[4][0]) for info in infos]
    else:
        addresses = [host]
    for address in addresses:
        _check_intranet_ip(address)
    return host, addresses[0]


# Hostnames that never refer to a public Internet endpoint (Docker Desktop, etc.).
_LOCAL_HOSTNAMES = frozenset(
    {
        "localhost",
        "host.docker.internal",
        "gateway.docker.internal",
    }
)
_LOCAL_HOST_SUFFIXES = (".local", ".localhost", ".internal")


def is_private_or_local_host(host: str) -> bool:
    """True for loopback / RFC1918 / link-local literals and well-known local names.

    Used by MCP connector URL validation so self-hosted LAN endpoints are allowed
    without weakening the outbound SSRF guard used for OAuth / provider probes.
    """
    normalized = host.lower().rstrip(".")
    if not normalized:
        return False
    if normalized in _LOCAL_HOSTNAMES:
        return True
    if normalized.endswith(_LOCAL_HOST_SUFFIXES):
        return True
    try:
        addr = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private or addr.is_link_local


def _parse_https_host(url: str) -> tuple[str, int | None]:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeOutboundUrl("only https URLs are allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeOutboundUrl("missing hostname")
    return host.lower().rstrip("."), parsed.port


def _check_ip_not_private(ip_str: str) -> None:
    addr = ipaddress.ip_address(ip_str)
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
    ):
        raise UnsafeOutboundUrl("private or reserved IP addresses are not allowed")


def _check_ip_literal(host: str) -> None:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return
    _check_ip_not_private(host)


def _check_resolved_ip(ip_str: str) -> None:
    _check_ip_not_private(ip_str)


def issuer_base_domain(issuer: str) -> str:
    host = (urlparse(issuer).hostname or "").lower()
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def host_allowed_for_issuer(host: str, issuer: str) -> bool:
    normalized = host.lower().rstrip(".")
    issuer_host = (urlparse(issuer).hostname or "").lower()
    base = issuer_base_domain(issuer)
    if normalized in {issuer_host, base}:
        return True
    return normalized.endswith(f".{base}")


def validate_https_url(url: str, *, field: str = "url") -> str:
    """Reject non-https URLs and literal private/reserved IPs."""
    host, _ = _parse_https_host(url)
    if host == "localhost":
        raise UnsafeOutboundUrl(f"{field}: localhost is not allowed")
    _check_ip_literal(host)
    return url


async def validate_https_url_resolved(url: str, *, field: str = "url") -> str:
    """Also resolve DNS and reject private/reserved addresses."""
    validate_https_url(url, field=field)
    await _resolve_validated_ip(url)
    return url


async def _resolve_validated_ip(url: str) -> str:
    """Resolve ``url`` and return one validated (public) IP.

    Raises :class:`UnsafeOutboundUrl` if the host cannot be resolved or any
    resolved address is private/reserved.  The caller should pin the returned
    IP for the actual connection to defeat DNS-rebinding (TOCTOU).
    """
    host, port = _parse_https_host(url)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            host,
            port or 443,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise UnsafeOutboundUrl(f"cannot resolve hostname {host!r}") from exc
    if not infos:
        raise UnsafeOutboundUrl(f"cannot resolve hostname {host!r}")
    for info in infos:
        _check_resolved_ip(info[4][0])
    return infos[0][4][0]


class _PinnedNetworkBackend(AutoBackend):
    """Resolve the validated host to a fixed IP, while preserving SNI.

    Only requests whose host matches ``_target_host`` are pinned to
    ``_pin_ip``; everything else (e.g. redirects) resolves normally so the
    helper never breaks legitimate cross-host redirects.  The original
    hostname is passed to TLS via httpcore's SNI logic, so certificate
    validation is unaffected by the IP pinning.
    """

    def __init__(self, target_host: str, pin_ip: str, *, strict_host: bool = False) -> None:
        super().__init__()
        self._target_host = target_host
        self._pin_ip = pin_ip
        self._strict_host = strict_host

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        if host == self._target_host:
            return await super().connect_tcp(
                self._pin_ip,
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )
        if self._strict_host:
            raise UnsafeOutboundUrl("connection host differs from validated model host")
        return await super().connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class PinnedIPTransport(httpx.AsyncHTTPTransport):
    """httpx transport that pins the validated IP for ``target_host``.

    Swaps the underlying httpcore network backend so the validated host always
    connects to the validated IP — closing the DNS-rebinding window between
    validation and the actual TCP connection (CWE-918).
    """

    def __init__(self, target_host: str, pin_ip: str, *, strict_host: bool = False) -> None:
        super().__init__()
        self._pool._network_backend = _PinnedNetworkBackend(
            target_host, pin_ip, strict_host=strict_host
        )


async def safe_request(
    method: str,
    url: str,
    *,
    json: typing.Any | None = None,
    data: typing.Any | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> httpx.Response:
    """Validate, resolve, pin the IP, and perform an outbound HTTPS request.

    URL scheme/host must be https and resolve to a public IP (see
    :func:`validate_https_url_resolved`).  The connection is then pinned to the
    validated IP so a malicious DNS change between validation and connection
    cannot redirect the request to an internal address.
    """
    parsed = urlparse(url)
    host, port = _parse_https_host(url)
    pin_ip = await _resolve_validated_ip(url)
    # Rebuild the request URL from validated components so the outbound call
    # does not reuse the original user-controlled string (CWE-918 / CodeQL).
    netloc = f"{host}:{port}" if port is not None else host
    request_url = urlunparse(("https", netloc, parsed.path or "/", "", parsed.query, ""))
    transport = PinnedIPTransport(host, pin_ip)
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        return await client.request(method, request_url, json=json, data=data, headers=headers)
