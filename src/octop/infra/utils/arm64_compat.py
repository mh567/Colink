"""ARM64 Linux platform compatibility utilities."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def ensure_arm64_openssl_compat(*, cpuinfo_path: Path | None = None) -> None:
    """Mask SVE on Linux ARM64 if kernel reports sve2 without sve.

    Prevents SIGILL on `cntb` inside OpenSSL's `OPENSSL_cpuid_setup` on older
    Linux kernels (such as 5.4 in Kylin V10 SP1 / UTM virtualization).
    """
    if not sys.platform.startswith("linux"):
        return
    if "OPENSSL_armcap" in os.environ:
        return
    try:
        machine = platform.machine().lower()
        if machine not in ("aarch64", "arm64"):
            return
        path = cpuinfo_path or Path("/proc/cpuinfo")
        cpuinfo = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in cpuinfo.splitlines():
        field, _, value = line.partition(":")
        if field.strip() == "Features":
            features = value.split()
            if "sve2" in features and "sve" not in features:
                # OpenSSL treats the value as a full override, not a feature mask.
                os.environ["OPENSSL_armcap"] = "0"  # noqa: SIM112
                return
