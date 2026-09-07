"""Transport backends, selected by name or auto-detected per platform.

``auto`` prefers D2XX where a D2XX library exists (Windows, and macOS/Linux with
FTDI's SDK installed) because it sets the latency timer through a documented API
call needing no privileges. Otherwise it falls back to the pyserial VCP backend,
which is the normal path on Linux.
"""
from __future__ import annotations

import logging
import platform
from typing import Callable, Dict, List

from .base import Backend, BackendError

logger = logging.getLogger(__name__)

BACKEND_REGISTRY: Dict[str, Callable[..., Backend]] = {}

__all__ = [
    "Backend",
    "BackendError",
    "BACKEND_REGISTRY",
    "register_backend",
    "open_backend",
    "available_backends",
]


def register_backend(name: str):
    """Register a Backend factory under a lookup name."""

    def decorator(factory: Callable[..., Backend]) -> Callable[..., Backend]:
        if name in BACKEND_REGISTRY:
            raise ValueError(f"Backend {name!r} is already registered")
        BACKEND_REGISTRY[name] = factory
        return factory

    return decorator


def _make_d2xx(**kwargs) -> Backend:
    """Open a D2XX device. Accepts device_index/port, ignoring what it cannot use."""
    from .d2xx import D2xxDevice
    return D2xxDevice(
        index=kwargs.get("device_index", 0),
        baud=kwargs.get("baud", 38400),
        latency_ms=kwargs.get("latency_ms", 1),
    )


def _make_serial(**kwargs) -> Backend:
    """Open a VCP device."""
    from .serial_vcp import SerialDevice
    return SerialDevice(
        port=kwargs.get("port"),
        baud=kwargs.get("baud", 38400),
        latency_ms=kwargs.get("latency_ms", 1),
        timeout_s=kwargs.get("read_timeout_s", 0.5),
    )


register_backend("d2xx")(_make_d2xx)
register_backend("serial")(_make_serial)


def available_backends() -> List[str]:
    """Names of backends whose dependencies are importable right now."""
    from . import d2xx, serial_vcp
    found = []
    if d2xx.is_available():
        found.append("d2xx")
    if serial_vcp.is_available():
        found.append("serial")
    return found


def _auto_order() -> List[str]:
    """Backend preference order for this platform."""
    if platform.system() == "Linux":
        # ftdi_sio owns the device; D2XX would need it unbound plus FTDI's SDK.
        return ["serial", "d2xx"]
    return ["d2xx", "serial"]


def open_backend(name: str = "auto", **kwargs) -> Backend:
    """Open a transport.

    Args:
        name: A registered backend name, or "auto" to pick per platform.
        **kwargs: Forwarded to the backend (device_index, port, baud,
            latency_ms, read_timeout_s).

    Returns:
        An open Backend.

    Raises:
        BackendError: If the named backend failed, or no backend worked.
        KeyError: If the name is not registered.
    """
    if name != "auto":
        if name not in BACKEND_REGISTRY:
            raise KeyError(f"Unknown backend {name!r}; "
                           f"available: {sorted(BACKEND_REGISTRY)}")
        return BACKEND_REGISTRY[name](**kwargs)

    failures = []
    for candidate in _auto_order():
        try:
            backend = BACKEND_REGISTRY[candidate](**kwargs)
            logger.debug("Auto-selected %s backend", candidate)
            return backend
        except (BackendError, OSError) as exc:
            failures.append(f"{candidate}: {exc}")
            logger.debug("Backend %s unavailable: %s", candidate, exc)
    raise BackendError(
        "No usable backend on this machine.\n  " + "\n  ".join(failures))


def describe_platform() -> str:
    """One-line summary of platform and backend availability, for diagnostics."""
    return (f"{platform.system()} {platform.machine()} | "
            f"available backends: {', '.join(available_backends()) or 'none'} | "
            f"auto order: {', '.join(_auto_order())}")
