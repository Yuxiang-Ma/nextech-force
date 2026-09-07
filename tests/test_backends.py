"""Backend selection, and the Linux-only paths simulated on any platform.

The sysfs latency timer is the whole reason the Linux backend can match the
Windows rate, and it cannot be exercised on a Windows CI box. Rather than leave
it untested, the platform check and the file write are injected so the logic --
which path is chosen, what happens when the write is refused -- is verified
everywhere, leaving only the kernel's own behaviour unverified.
"""
import platform

import pytest

from nextech_force.backends import (
    BACKEND_REGISTRY,
    BackendError,
    _auto_order,
    available_backends,
    describe_platform,
    open_backend,
    register_backend,
    serial_vcp,
)
from nextech_force.backends import d2xx as d2xx_mod


def test_both_backends_registered():
    assert set(BACKEND_REGISTRY) == {"d2xx", "serial"}


def test_unknown_backend_lists_alternatives():
    with pytest.raises(KeyError, match="serial"):
        open_backend("carrier-pigeon")


def test_linux_prefers_serial(monkeypatch):
    """ftdi_sio owns the device on Linux, so D2XX must not be tried first."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert _auto_order()[0] == "serial"


def test_windows_prefers_d2xx(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert _auto_order()[0] == "d2xx"


def test_describe_platform_is_a_single_line():
    text = describe_platform()
    assert "\n" not in text
    assert "backends" in text


def test_available_backends_is_a_subset_of_registry():
    assert set(available_backends()) <= set(BACKEND_REGISTRY)


def test_d2xx_module_imports_without_the_library(monkeypatch):
    """Importing the package on Linux must not blow up on a missing DLL.

    The original code called ctypes.WinDLL at module scope, which made the whole
    package unimportable on Linux. Loading must stay lazy.
    """
    monkeypatch.setattr(d2xx_mod, "_dll", None)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(d2xx_mod, "_LIB_NAMES", {"Linux": ["definitely-not-real.so"]})
    assert d2xx_mod.is_available() is False
    with pytest.raises(d2xx_mod.D2xxError, match="No D2XX library"):
        d2xx_mod._load()


def test_d2xx_error_names_the_serial_alternative(monkeypatch):
    monkeypatch.setattr(d2xx_mod, "_dll", None)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(d2xx_mod, "_LIB_NAMES", {"Linux": ["nope.so"]})
    with pytest.raises(d2xx_mod.D2xxError, match="serial"):
        d2xx_mod._load()


def test_open_backend_auto_reports_every_failure(monkeypatch):
    def boom(**_kwargs):
        raise BackendError("nope")

    monkeypatch.setitem(BACKEND_REGISTRY, "d2xx", boom)
    monkeypatch.setitem(BACKEND_REGISTRY, "serial", boom)
    with pytest.raises(BackendError, match="No usable backend"):
        open_backend("auto")


def test_duplicate_backend_registration_rejected():
    with pytest.raises(ValueError, match="already registered"):
        register_backend("serial")(lambda **kw: None)


# --- Linux sysfs latency timer -------------------------------------------

def test_sysfs_path_is_none_off_linux(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert serial_vcp._sysfs_latency_path("COM4") is None


def test_sysfs_path_found_on_linux(monkeypatch, tmp_path):
    """/dev/ttyUSB0 must map to its usb-serial latency_timer file."""
    device_dir = tmp_path / "ttyUSB0"
    device_dir.mkdir()
    (device_dir / "latency_timer").write_text("16")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(serial_vcp, "SYSFS_LATENCY",
                        str(tmp_path / "{port}" / "latency_timer"))
    found = serial_vcp._sysfs_latency_path("/dev/ttyUSB0")
    assert found is not None
    assert found.endswith("latency_timer")


def test_sysfs_path_absent_for_unknown_device(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(serial_vcp, "SYSFS_LATENCY",
                        str(tmp_path / "{port}" / "latency_timer"))
    assert serial_vcp._sysfs_latency_path("/dev/ttyUSB9") is None


class _FakeSerialDevice(serial_vcp.SerialDevice):
    """Constructs without touching hardware, to test the latency logic alone."""

    def __init__(self, port):
        self._port = port


def test_latency_write_succeeds(monkeypatch, tmp_path):
    latency_file = tmp_path / "latency_timer"
    latency_file.write_text("16")
    monkeypatch.setattr(serial_vcp, "_sysfs_latency_path",
                        lambda port: str(latency_file))
    dev = _FakeSerialDevice("/dev/ttyUSB0")
    assert dev._write_latency(1) is True
    assert latency_file.read_text() == "1"
    assert dev.get_latency() == 1


def test_latency_write_refused_is_survivable(monkeypatch, caplog):
    """A root-owned sysfs file must degrade to a warning, not an exception.

    A working 60 Hz link is better than refusing to run, so the backend keeps
    going and tells the user the exact udev rule that fixes it.
    """
    monkeypatch.setattr(serial_vcp, "_sysfs_latency_path", lambda port: "/fake/latency")

    def denied(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("builtins.open", denied)
    dev = _FakeSerialDevice("/dev/ttyUSB0")
    with caplog.at_level("WARNING"):
        assert dev._write_latency(1) is False
    assert "udev" in caplog.text
    assert "60 Hz" in caplog.text


def test_missing_pyserial_names_the_fix(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_serial(name, *args, **kwargs):
        if name == "serial" or name.startswith("serial."):
            raise ImportError("No module named 'serial'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_serial)
    with pytest.raises(serial_vcp.SerialBackendError, match="pip install pyserial"):
        serial_vcp._import_serial()


def test_no_ports_message_mentions_dialout(monkeypatch):
    """The usual Linux failure is a permissions problem, so say so."""
    monkeypatch.setattr(serial_vcp, "find_ports", lambda: [])
    monkeypatch.setattr(serial_vcp, "_import_serial", lambda: __import__("serial"))
    with pytest.raises(serial_vcp.SerialBackendError, match="dialout"):
        serial_vcp.SerialDevice(port=None)
