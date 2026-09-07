"""Virtual COM port backend (pyserial), with the Linux latency-timer knob.

This is the backend to use on Linux. The kernel binds FTDI chips to ``ftdi_sio``
and exposes them as ``/dev/ttyUSB*``; using D2XX there would mean unbinding that
driver and installing FTDI's SDK. The kernel driver already exposes the one
setting that matters at::

    /sys/bus/usb-serial/devices/ttyUSB0/latency_timer

which defaults to 16 -- the same 16 ms that pins polling near 60 Hz on Windows.
Writing 1 there is exactly equivalent to ``FT_SetLatencyTimer(1)``.

The file is root-owned by default. Rather than fail, the backend warns with the
precise remedy and keeps running at whatever timer is in force, because a
working 60 Hz link beats a hard error.
"""
from __future__ import annotations

import glob
import logging
import os
import platform
from typing import List, Optional

from .base import Backend, BackendError

logger = logging.getLogger(__name__)

SYSFS_LATENCY = "/sys/bus/usb-serial/devices/{port}/latency_timer"

# FTDI's USB vendor id. Nextech gauges ship an FT232R (VID 0403, PID 6001).
FTDI_VID = 0x0403


class SerialBackendError(BackendError):
    """The serial port could not be opened or configured."""


def _import_serial():
    """Import pyserial with an actionable message if it is missing."""
    try:
        import serial  # noqa: PLC0415
        import serial.tools.list_ports  # noqa: PLC0415
        return serial
    except ImportError as exc:
        raise SerialBackendError(
            "pyserial is required for the serial backend. "
            "Install it with: pip install pyserial"
        ) from exc


def is_available() -> bool:
    """Whether pyserial is importable."""
    try:
        _import_serial()
        return True
    except SerialBackendError:
        return False


def find_ports() -> List[str]:
    """Return candidate FTDI serial ports, most likely first.

    Matching is by USB vendor id rather than by device name, because the name
    differs per platform (``COM4``, ``/dev/ttyUSB0``, ``/dev/cu.usbserial-*``).
    """
    serial = _import_serial()
    ftdi, others = [], []
    for port in serial.tools.list_ports.comports():
        manufacturer = (port.manufacturer or "").upper()
        if getattr(port, "vid", None) == FTDI_VID or manufacturer.startswith("FTDI"):
            ftdi.append(port.device)
        elif "ttyUSB" in port.device or "usbserial" in port.device:
            others.append(port.device)
    return ftdi + others


def _sysfs_latency_path(port: str) -> Optional[str]:
    """Map a device path to its sysfs latency_timer file, if one exists."""
    if platform.system() != "Linux":
        return None
    leaf = os.path.basename(port)
    path = SYSFS_LATENCY.format(port=leaf)
    if os.path.exists(path):
        return path
    # Some kernels expose it under the tty device tree instead.
    for candidate in glob.glob(f"/sys/bus/usb-serial/devices/{leaf}*/latency_timer"):
        return candidate
    return None


class SerialDevice(Backend):
    """A gauge reached over a virtual COM port."""

    name = "serial"

    def __init__(self, port: Optional[str] = None, baud: int = 38400,
                 latency_ms: int = 1, timeout_s: float = 0.5) -> None:
        serial = _import_serial()
        if port is None:
            candidates = find_ports()
            if not candidates:
                raise SerialBackendError(
                    "No FTDI serial port found. On Linux check that the device "
                    "appears as /dev/ttyUSB* and that you are in the 'dialout' "
                    "group (sudo usermod -aG dialout $USER, then log out and in)."
                )
            port = candidates[0]
        self._port = port
        # Set the latency timer before opening: ftdi_sio applies it per-open.
        self._latency_applied = self._write_latency(latency_ms)
        try:
            self._ser = serial.Serial(
                port=port, baudrate=baud, bytesize=8,
                stopbits=serial.STOPBITS_ONE, parity=serial.PARITY_NONE,
                timeout=timeout_s, write_timeout=timeout_s,
            )
        except (OSError, serial.SerialException) as exc:
            raise SerialBackendError(f"Could not open {port}: {exc}") from exc
        self._ser.dtr = True
        self._ser.rts = True
        self.purge()

    @property
    def description(self) -> str:
        return f"serial[{self._port}]"

    def _write_latency(self, ms: int) -> bool:
        """Write the sysfs latency timer. Returns whether it took effect."""
        path = _sysfs_latency_path(self._port)
        if path is None:
            if platform.system() == "Linux":
                logger.warning("No sysfs latency_timer for %s; rate will be "
                               "capped near 60 Hz", self._port)
            return False
        try:
            with open(path, "w", encoding="ascii") as handle:
                handle.write(str(ms))
        except PermissionError:
            logger.warning(
                "Cannot write %s (needs root). Polling will be capped near "
                "60 Hz instead of ~170 Hz. Fix permanently with a udev rule:\n"
                '  echo \'ACTION=="add", SUBSYSTEM=="usb-serial", '
                'DRIVER=="ftdi_sio", ATTR{latency_timer}="1"\' '
                "| sudo tee /etc/udev/rules.d/99-ftdi-latency.rules\n"
                "  sudo udevadm control --reload-rules && sudo udevadm trigger",
                path)
            return False
        except OSError as exc:
            logger.warning("Could not set latency timer via %s: %s", path, exc)
            return False
        return True

    def get_latency(self) -> Optional[int]:
        path = _sysfs_latency_path(self._port)
        if path is None:
            return None
        try:
            with open(path, encoding="ascii") as handle:
                return int(handle.read().strip())
        except (OSError, ValueError):
            return None

    def set_latency(self, ms: int) -> bool:
        return self._write_latency(ms)

    def purge(self) -> None:
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

    def in_waiting(self) -> int:
        return self._ser.in_waiting

    def write(self, data: bytes) -> int:
        return self._ser.write(data) or 0

    def read(self, n: int) -> bytes:
        if n <= 0:
            return b""
        return self._ser.read(n)

    def close(self) -> None:
        if getattr(self, "_ser", None) is not None and self._ser.is_open:
            self._ser.close()
