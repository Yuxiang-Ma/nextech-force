"""Force gauge driver for Nextech DFS/DFT devices.

`nexgraphpy` caps out near 10 Hz because `_get_output()` sleeps a fixed 100 ms
after every command. Removing that sleep exposes a second, larger bottleneck:
the FTDI latency timer, which defaults to 16 ms and pins the poll rate near
60 Hz. Setting it to 1 ms lifts sustained polling to ~170 Hz on a DFS-X 100N.

How that timer is reached differs per platform, so it lives behind a backend:
D2XX on Windows, the ftdi_sio sysfs knob on Linux. This module depends only on
the backend interface and imports nothing beyond the standard library, so it can
be dropped into a data-collection pipeline without pulling numpy or matplotlib.

The gauge is strictly request/response and does *not* queue commands: a burst
of 50 polls yields exactly one reply. Keep one poll outstanding at a time.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Optional

from .backends import Backend, BackendError, open_backend
from .config import (
    CMD_INFO,
    CMD_PEAK_COMPRESSION,
    CMD_PEAK_TENSION,
    CMD_RESET,
    CMD_ZERO,
    GaugeConfig,
)

logger = logging.getLogger(__name__)

_VALUE_RE = re.compile(rb"([+-]?\s*\d+\.?\d*)")


@dataclass(frozen=True)
class Sample:
    """One reading with host timestamps taken around the round trip."""

    t_send: float
    t_recv: float
    value: Optional[float]
    raw: bytes

    @property
    def latency_ms(self) -> float:
        """Round-trip time for this poll, in milliseconds."""
        return (self.t_recv - self.t_send) * 1e3

    @property
    def t_mid(self) -> float:
        """Midpoint timestamp -- best single estimate of the acquisition instant.

        The true conversion happened somewhere inside the round trip, so the
        midpoint bounds the error at half the latency (~2.5 ms at 1 ms latency
        timer), comfortably under one frame at 30-60 fps.
        """
        return 0.5 * (self.t_send + self.t_recv)


class ForceGauge:
    """A Nextech force gauge polled at its true maximum rate.

    Example:
        >>> with ForceGauge() as gauge:
        ...     gauge.zero()
        ...     for sample in gauge.stream(duration_s=1.0):
        ...         print(sample.t_mid, sample.value)
    """

    def __init__(self, config: Optional[GaugeConfig] = None) -> None:
        self.config = config or GaugeConfig()
        self._dev: Optional[Backend] = None
        self.device_info: str = ""

    @property
    def is_connected(self) -> bool:
        """Whether the transport is currently open."""
        return self._dev is not None

    @property
    def backend_name(self) -> str:
        """Which transport is in use, e.g. 'd2xx[0]' or 'serial[/dev/ttyUSB0]'."""
        return self._dev.description if self._dev is not None else "none"

    @property
    def model(self) -> str:
        """Model designation parsed from the info string, e.g. "DFS-X 100N"."""
        parts = self.device_info.split()
        return " ".join(parts[:2]) if len(parts) >= 2 else self.device_info

    def connect(self) -> bool:
        """Open the gauge and read its identification string.

        Returns:
            True if the gauge responded to the info command.

        Raises:
            BackendError: If no transport could be opened or configured.
        """
        self._dev = open_backend(self.config.backend, **self.config.backend_kwargs())
        actual = self._dev.get_latency()
        if actual is not None and actual != self.config.latency_ms:
            logger.warning(
                "Latency timer is %d ms, requested %d ms -- the poll rate will "
                "be capped near %.0f Hz", actual, self.config.latency_ms,
                1000.0 / max(actual, 1))
        raw = self._exchange(CMD_INFO, settle_s=0.2)
        self.device_info = raw.decode("ascii", "replace").strip()
        if not self.device_info:
            logger.error("Gauge did not respond to the info command")
            return False
        logger.info("Connected: %s via %s (latency timer %s ms)",
                    self.device_info, self._dev.description,
                    "unknown" if actual is None else actual)
        return True

    def _exchange(self, cmd: bytes, settle_s: float = 0.0) -> bytes:
        """Send one command and read one newline-terminated reply."""
        if self._dev is None:
            raise BackendError("Gauge is not connected")
        self._dev.purge()
        self._dev.write(cmd)
        if settle_s:
            time.sleep(settle_s)
        buf = bytearray()
        deadline = time.perf_counter() + self.config.read_timeout_s
        while time.perf_counter() < deadline:
            pending = self._dev.in_waiting()
            if pending:
                buf += self._dev.read(pending)
                if b"\n" in buf:
                    break
        return bytes(buf).strip()

    @staticmethod
    def parse(raw: bytes) -> Optional[float]:
        """Parse a reply into signed newtons.

        Sign is carried by prefix: 'C:' is compression (negative), 'T:' is
        tension (positive). The mini format carries a bare leading sign.

        Args:
            raw: The stripped reply bytes.

        Returns:
            The signed value, or None if the reply could not be parsed.
        """
        if not raw:
            return None
        head = raw.split(b".")[0]
        negative = raw.startswith(b"C:") or b"-" in head
        match = _VALUE_RE.search(raw.replace(b"C:", b"").replace(b"T:", b""))
        if not match:
            return None
        try:
            magnitude = abs(float(match.group(1).replace(b" ", b"")))
        except ValueError:
            logger.debug("Unparseable reply: %r", raw)
            return None
        return -magnitude if negative else magnitude

    def read(self) -> Sample:
        """Take a single reading."""
        t0 = time.perf_counter()
        raw = self._exchange(self.config.command)
        t1 = time.perf_counter()
        return Sample(t_send=t0, t_recv=t1, value=self.parse(raw), raw=raw)

    def stream(self, duration_s: Optional[float] = None,
               max_samples: Optional[int] = None) -> Iterator[Sample]:
        """Yield samples as fast as the gauge will answer.

        Args:
            duration_s: Stop after this many seconds, or None for no time limit.
            max_samples: Stop after this many samples, or None for no count limit.

        Yields:
            Sample instances in acquisition order.

        Raises:
            ValueError: If neither bound is given.
        """
        if duration_s is None and max_samples is None:
            raise ValueError("Provide duration_s or max_samples to bound the stream")
        end = time.perf_counter() + duration_s if duration_s else float("inf")
        count = 0
        while time.perf_counter() < end:
            if max_samples is not None and count >= max_samples:
                break
            yield self.read()
            count += 1

    def zero(self) -> bool:
        """Tare the gauge to the current load."""
        return self._send(CMD_ZERO)

    def reset(self) -> bool:
        """Clear stored peak values."""
        return self._send(CMD_RESET)

    def peak_tension(self) -> Optional[float]:
        """Read the stored peak tension value."""
        return self.parse(self._exchange(CMD_PEAK_TENSION, settle_s=0.05))

    def peak_compression(self) -> Optional[float]:
        """Read the stored peak compression value."""
        return self.parse(self._exchange(CMD_PEAK_COMPRESSION, settle_s=0.05))

    def _send(self, cmd: bytes) -> bool:
        """Fire-and-forget command with no reply expected."""
        if self._dev is None:
            logger.error("Gauge is not connected")
            return False
        try:
            self._dev.write(cmd)
            return True
        except BackendError as exc:
            logger.error("Command %r failed: %s", cmd, exc)
            return False

    def close(self) -> None:
        """Release the device so other programs can open the port again."""
        if self._dev is not None:
            self._dev.close()
            self._dev = None

    def __enter__(self) -> ForceGauge:
        if not self.connect():
            self.close()
            raise BackendError("Failed to connect to the gauge")
        return self

    def __exit__(self, *exc) -> None:
        self.close()
