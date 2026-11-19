"""Octop — smarter self-hosted AI assistant (multi-user, multi-agent)."""

from __future__ import annotations

from octop.infra.utils.arm64_compat import ensure_arm64_openssl_compat
from octop.infra.utils.httpx_proxy import install_httpx_cidr_no_proxy

__version__ = "1.0.3b3"

# Mask SVE on Linux ARM64 systems with the sve2-without-sve kernel bug to prevent
# OpenSSL SIGILL in cryptography, while retaining NEON/AES/SHA hardware crypto.
ensure_arm64_openssl_compat()

# httpx 0.28 treats NO_PROXY CIDR as an exact IP (see issue #1347). Patch once
# at import so CLI, ``octop run``, and in-process harness clients all honor it.
install_httpx_cidr_no_proxy()
