"""Drive the gauge through the exact code path Ubuntu will take.

There is no Linux machine here, so this forces the Linux branches on Windows:
``platform.system()`` reports Linux and the D2XX library is made unloadable.
What remains is the real serial backend talking to the real gauge -- the same
objects, selection logic and I/O calls Ubuntu will use.

What this proves: package import, backend auto-selection, port discovery by
FTDI vendor id, open/read/write/purge, parsing, tare, and the recorder.

What it cannot prove: the sysfs latency_timer write (Linux-only file) and
/dev/ttyUSB* naming. Those are covered by unit tests with an injected
filesystem, and by benchmarks/ubuntu_check.py on the real machine.
"""
from __future__ import annotations

import logging
import platform

from nextech_force.backends import d2xx as d2xx_mod

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

_real_system = platform.system


def force_linux() -> None:
    """Make every platform check in the package believe this is Linux."""
    platform.system = lambda: "Linux"
    d2xx_mod._dll = None
    d2xx_mod._LIB_NAMES = {"Linux": ["/nonexistent/libftd2xx.so"]}


def main() -> None:
    print(f"real platform     : {_real_system()}")
    force_linux()

    from nextech_force import ForceGauge, GaugeConfig, Recorder, create_horizon
    from nextech_force.backends import _auto_order, available_backends, describe_platform
    from nextech_force.backends.serial_vcp import find_ports

    print(f"simulated platform: {platform.system()}")
    print(f"describe_platform : {describe_platform()}")
    print(f"auto order        : {_auto_order()}")
    print(f"available         : {available_backends()}")
    assert "d2xx" not in available_backends(), "D2XX should be unavailable"
    assert _auto_order()[0] == "serial", "Linux must prefer the serial backend"
    print(f"discovered ports  : {find_ports()}")

    # auto-selection must now land on serial, with no explicit backend given.
    with ForceGauge(GaugeConfig(latency_ms=1)) as gauge:
        print(f"\nselected backend  : {gauge.backend_name}")
        assert gauge.backend_name.startswith("serial"), "expected serial transport"
        print(f"device            : {gauge.device_info}")
        print(f"latency reported  : {gauge._dev.get_latency()}  "
              f"(None off-Linux: no sysfs file exists here)")

        samples = list(gauge.stream(max_samples=120))
        span = samples[-1].t_recv - samples[0].t_send
        good = [s for s in samples if s.value is not None]
        print(f"rate              : {len(samples)/span:.1f} Hz")
        print(f"parsed            : {len(good)}/{len(samples)}")
        print(f"tare              : {gauge.zero()}")

        recorder = Recorder(gauge, create_horizon("fixed", duration_s=2.0))
        recorder.start()
        recorder.wait(timeout=10)
        t, v = recorder.snapshot()
        print(f"recorder          : {recorder.n_samples} samples, "
              f"{len(t)} buffered, unparsed={recorder.n_unparsed}")
        assert recorder.error is None, recorder.error
        assert len(t) == len(v) and len(t) > 50

    print("\nOK: the Linux code path works end to end against real hardware.")
    print("Unverified on this machine: sysfs latency write, /dev/ttyUSB* naming.")


if __name__ == "__main__":
    main()
