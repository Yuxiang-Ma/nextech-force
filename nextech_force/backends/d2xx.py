"""FTDI D2XX backend.

D2XX exposes ``FT_SetLatencyTimer``, which is the whole reason this backend
exists: the driver default of 16 ms pins polling near 60 Hz, and setting it to
1 ms lifts the same gauge to ~170 Hz without a registry edit or root.

The library is loaded lazily. Importing this module on a machine with no D2XX
library must succeed -- ``nextech_force`` is imported on Linux boxes where the
serial backend is the one that will actually be used, and an import-time
``WinDLL`` call would take the whole package down with it.
"""
from __future__ import annotations

import ctypes as ct
import logging
import platform
import threading
from typing import Optional

from .base import Backend, BackendError

logger = logging.getLogger(__name__)

OK = 0
BITS_8, STOP_BITS_1, PARITY_NONE = 8, 0, 0
PURGE_RX, PURGE_TX = 1, 2
FLOW_NONE = 0x0000

_STATUS = {
    0: "OK", 1: "INVALID_HANDLE", 2: "DEVICE_NOT_FOUND", 3: "DEVICE_NOT_OPENED",
    4: "IO_ERROR", 5: "INSUFFICIENT_RESOURCES", 6: "INVALID_PARAMETER",
    7: "INVALID_BAUD_RATE", 8: "DEVICE_NOT_OPENED_FOR_ERASE",
    9: "DEVICE_NOT_OPENED_FOR_WRITE", 10: "FAILED_TO_WRITE_DEVICE",
    18: "OTHER_ERROR",
}

# Candidate library names per platform. Linux/macOS users who install FTDI's
# D2XX package get libftd2xx; most Linux users will not have it and should use
# the serial backend instead.
_LIB_NAMES = {
    "Windows": ["ftd2xx.dll"],
    "Darwin": ["libftd2xx.dylib"],
    "Linux": ["libftd2xx.so", "libftd2xx.so.1"],
}

_dll = None
_dll_lock = threading.Lock()


class D2xxError(BackendError):
    """A D2XX call returned a non-zero status, or the library is unavailable."""


def _load() -> ct.CDLL:
    """Load the D2XX shared library, caching the handle.

    Raises:
        D2xxError: If no candidate library could be loaded.
    """
    global _dll
    if _dll is not None:
        return _dll
    with _dll_lock:
        if _dll is not None:
            return _dll
        system = platform.system()
        loader = ct.WinDLL if system == "Windows" else ct.CDLL
        errors = []
        for name in _LIB_NAMES.get(system, []):
            try:
                _dll = loader(name)
                logger.debug("Loaded D2XX library %s", name)
                return _dll
            except OSError as exc:
                errors.append(f"{name}: {exc}")
        raise D2xxError(
            f"No D2XX library available on {system}. Tried: "
            f"{'; '.join(errors) or 'nothing'}. "
            "On Linux prefer the 'serial' backend, which needs no FTDI SDK."
        )


def is_available() -> bool:
    """Whether a D2XX library can be loaded on this machine."""
    try:
        _load()
        return True
    except D2xxError:
        return False


def _check(status: int, fn: str) -> None:
    """Raise if a D2XX call failed."""
    if status != OK:
        raise D2xxError(f"{fn} failed: {_STATUS.get(status, status)} ({status})")


def device_count() -> int:
    """Number of FTDI devices visible to the D2XX driver."""
    dll = _load()
    n = ct.c_ulong()
    _check(dll.FT_CreateDeviceInfoList(ct.byref(n)), "FT_CreateDeviceInfoList")
    return n.value


class D2xxDevice(Backend):
    """An FTDI device opened through D2XX with a settable latency timer."""

    name = "d2xx"

    def __init__(self, index: int = 0, baud: int = 38400,
                 latency_ms: int = 1) -> None:
        self._dll = _load()
        self._index = index
        self.h = ct.c_void_p()
        _check(self._dll.FT_Open(index, ct.byref(self.h)), "FT_Open")
        try:
            _check(self._dll.FT_SetBaudRate(self.h, baud), "FT_SetBaudRate")
            _check(self._dll.FT_SetDataCharacteristics(
                self.h, BITS_8, STOP_BITS_1, PARITY_NONE),
                "FT_SetDataCharacteristics")
            _check(self._dll.FT_SetLatencyTimer(self.h, latency_ms),
                   "FT_SetLatencyTimer")
            _check(self._dll.FT_SetTimeouts(self.h, 500, 500), "FT_SetTimeouts")
            # pyserial asserts these on open; the gauge needs them to reply.
            _check(self._dll.FT_SetFlowControl(self.h, FLOW_NONE, 0, 0),
                   "FT_SetFlowControl")
            _check(self._dll.FT_SetDtr(self.h), "FT_SetDtr")
            _check(self._dll.FT_SetRts(self.h), "FT_SetRts")
            self.purge()
        except Exception:
            self.close()
            raise

    @property
    def description(self) -> str:
        return f"d2xx[{self._index}]"

    def get_latency(self) -> Optional[int]:
        v = ct.c_ubyte()
        _check(self._dll.FT_GetLatencyTimer(self.h, ct.byref(v)),
               "FT_GetLatencyTimer")
        return v.value

    def set_latency(self, ms: int) -> bool:
        _check(self._dll.FT_SetLatencyTimer(self.h, ms), "FT_SetLatencyTimer")
        return True

    def purge(self) -> None:
        _check(self._dll.FT_Purge(self.h, PURGE_RX | PURGE_TX), "FT_Purge")

    def in_waiting(self) -> int:
        n = ct.c_ulong()
        _check(self._dll.FT_GetQueueStatus(self.h, ct.byref(n)),
               "FT_GetQueueStatus")
        return n.value

    def write(self, data: bytes) -> int:
        written = ct.c_ulong()
        buf = ct.create_string_buffer(data, len(data))
        _check(self._dll.FT_Write(self.h, buf, len(data), ct.byref(written)),
               "FT_Write")
        return written.value

    def read(self, n: int) -> bytes:
        if n <= 0:
            return b""
        got = ct.c_ulong()
        buf = ct.create_string_buffer(n)
        _check(self._dll.FT_Read(self.h, buf, n, ct.byref(got)), "FT_Read")
        return buf.raw[:got.value]

    def close(self) -> None:
        if self.h:
            self._dll.FT_Close(self.h)
            self.h = ct.c_void_p()
