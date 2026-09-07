"""The byte-transport contract the gauge driver depends on.

The gauge protocol is identical everywhere; only the way the host reaches the
FTDI chip differs. Windows exposes D2XX, which can set the latency timer through
a documented API call. Linux binds the chip to the ``ftdi_sio`` kernel driver and
exposes the same knob as a sysfs file. Both reduce to: move bytes, and let us set
the latency timer -- so both hide behind this interface and the driver never
branches on platform.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class BackendError(RuntimeError):
    """A transport could not be opened, configured, or used."""


class Backend(ABC):
    """Byte transport to an FTDI-based gauge."""

    #: Human-readable name used in logs and error messages.
    name: str = "base"

    @abstractmethod
    def purge(self) -> None:
        """Discard any buffered input and output."""

    @abstractmethod
    def write(self, data: bytes) -> int:
        """Send bytes. Returns the number written."""

    @abstractmethod
    def read(self, n: int) -> bytes:
        """Read up to n buffered bytes without blocking for more."""

    @abstractmethod
    def in_waiting(self) -> int:
        """Bytes currently available to read."""

    @abstractmethod
    def close(self) -> None:
        """Release the device."""

    @abstractmethod
    def get_latency(self) -> Optional[int]:
        """Current FTDI latency timer in ms, or None if it cannot be read."""

    @abstractmethod
    def set_latency(self, ms: int) -> bool:
        """Set the FTDI latency timer.

        Returns:
            True if the value was applied. False means the transport is usable
            but stuck at its current timer, which caps the achievable rate --
            the caller should warn rather than fail.
        """

    @property
    def description(self) -> str:
        """Identifier for logs, e.g. the port name or device index."""
        return self.name

    def __enter__(self) -> Backend:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
