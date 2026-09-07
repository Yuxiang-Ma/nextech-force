"""Immutable configuration objects for gauge access, recording and plotting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Output-format commands understood by DFS/DFT gauges. 'L' (mini) is the
# fastest because its reply is the shortest and every byte costs ~0.26 ms at
# 38400 baud.
CMD_MINI = b"L"
CMD_SHORT = b"v"
CMD_LONG = b"l"
CMD_INFO = b"!"
CMD_ZERO = b"z"
CMD_RESET = b"r"
CMD_UNIT = b"u"
CMD_MODE = b"m"
CMD_PEAK_TENSION = b"p"
CMD_PEAK_COMPRESSION = b"c"

OUTPUT_COMMANDS = {"mini": CMD_MINI, "short": CMD_SHORT, "long": CMD_LONG}


@dataclass(frozen=True)
class GaugeConfig:
    """How to open and poll the gauge.

    Attributes:
        backend: "auto" (per-platform default), "d2xx", or "serial". Linux
            normally uses "serial"; Windows normally uses "d2xx".
        device_index: Index into the D2XX device list. 0 with one gauge attached.
        port: Explicit port for the serial backend ("COM4", "/dev/ttyUSB0").
            None auto-detects by FTDI vendor id.
        baud: Serial rate. Nextech devices use 38400.
        latency_ms: FTDI latency timer. The driver default of 16 ms caps polling
            near 60 Hz; 1 ms lifts it to ~170 Hz.
        read_timeout_s: Give up on a reply after this long.
        output_format: One of "mini", "short", "long".
    """

    backend: str = "auto"
    device_index: int = 0
    port: Optional[str] = None
    baud: int = 38400
    latency_ms: int = 1
    read_timeout_s: float = 0.25
    output_format: str = "mini"

    def __post_init__(self) -> None:
        if self.output_format not in OUTPUT_COMMANDS:
            raise ValueError(
                f"output_format must be one of {sorted(OUTPUT_COMMANDS)}, "
                f"got {self.output_format!r}")
        if not 1 <= self.latency_ms <= 255:
            raise ValueError(f"latency_ms must be 1..255, got {self.latency_ms}")

    def backend_kwargs(self) -> dict:
        """Arguments to hand to :func:`nextech_force.backends.open_backend`."""
        return {
            "device_index": self.device_index,
            "port": self.port,
            "baud": self.baud,
            "latency_ms": self.latency_ms,
            "read_timeout_s": self.read_timeout_s,
        }

    @property
    def command(self) -> bytes:
        """The poll command byte for the configured output format."""
        return OUTPUT_COMMANDS[self.output_format]


@dataclass(frozen=True)
class RecordingConfig:
    """How a recording session behaves.

    Attributes:
        csv_path: Stream samples here as they arrive. None keeps them in memory.
        buffer_samples: Cap on the in-memory ring buffer used for plotting. The
            CSV is never truncated; only the plotting buffer rolls.
        tare_on_start: Send a zero command before recording begins.
        settle_s: Pause after taring so the reading stabilises.
    """

    csv_path: Optional[str] = None
    buffer_samples: int = 400_000
    tare_on_start: bool = True
    settle_s: float = 0.3

    def __post_init__(self) -> None:
        if self.buffer_samples < 2:
            raise ValueError("buffer_samples must be at least 2")


@dataclass(frozen=True)
class PlotConfig:
    """Appearance and refresh behaviour of the force plot.

    Attributes:
        refresh_hz: Redraw rate. Deliberately far below the acquisition rate --
            acquisition runs in its own thread and is never throttled by drawing.
        title: Figure title. None derives one from the gauge and horizon.
        units: Y-axis unit label.
        ylim: Fixed y limits, or None to autoscale with padding.
        show_stats: Draw a live readout of current/min/max/rate.
        line_width: Trace line width.
        grid: Draw a background grid.
    """

    refresh_hz: float = 30.0
    title: Optional[str] = None
    units: str = "N"
    ylim: Optional[tuple] = None
    show_stats: bool = True
    line_width: float = 1.2
    grid: bool = True

    def __post_init__(self) -> None:
        if self.refresh_hz <= 0:
            raise ValueError("refresh_hz must be positive")
