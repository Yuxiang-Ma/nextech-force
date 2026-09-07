"""Run this on the Ubuntu machine to confirm the Linux-only pieces.

Everything else in this package is exercised on Windows CI and by the unit
tests. Two things genuinely cannot be checked anywhere but a real Linux box:

  1. the ``ftdi_sio`` sysfs ``latency_timer`` file exists and is writable, which
     is what lifts the rate from ~60 Hz to ~170 Hz;
  2. the gauge enumerates as ``/dev/ttyUSB*`` and the user can open it.

    python benchmarks/ubuntu_check.py

Exit code is non-zero if the gauge could not be driven at all. A low rate is
reported as a warning with the fix, not as a failure.
"""
from __future__ import annotations

import getpass
import glob
import logging
import os
import platform
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

UDEV_RULE = (
    'ACTION=="add", SUBSYSTEM=="usb-serial", DRIVER=="ftdi_sio", '
    'ATTR{latency_timer}="1"'
)


def check_environment() -> None:
    """Report kernel, groups, device nodes and the sysfs knob."""
    print(f"platform      : {platform.system()} {platform.release()} "
          f"{platform.machine()}")
    if platform.system() != "Linux":
        print("NOTE: not Linux -- run this on the Ubuntu machine.")

    nodes = sorted(glob.glob("/dev/ttyUSB*"))
    print(f"/dev/ttyUSB*  : {nodes or 'NONE FOUND'}")
    if not nodes:
        print("  -> Is the gauge plugged in? Check `dmesg | tail` for ftdi_sio.")

    try:
        import grp
        groups = [grp.getgrgid(g).gr_name for g in os.getgroups()]
    except (ImportError, KeyError, OSError):
        groups = []
    print(f"user/groups   : {getpass.getuser()} {groups}")
    if "dialout" not in groups:
        print("  -> Not in 'dialout'. Opening /dev/ttyUSB* will fail with EACCES.")
        print("     sudo usermod -aG dialout $USER   (then log out and back in)")

    for node in nodes:
        leaf = os.path.basename(node)
        sysfs = f"/sys/bus/usb-serial/devices/{leaf}/latency_timer"
        if not os.path.exists(sysfs):
            print(f"{leaf:<13}: no latency_timer at {sysfs}")
            continue
        with open(sysfs, encoding="ascii") as handle:
            current = handle.read().strip()
        writable = os.access(sysfs, os.W_OK)
        print(f"{leaf:<13}: latency_timer={current} writable={writable}")
        if not writable:
            print("  -> Rate will cap near 60 Hz. Install the udev rule:")
            print(f"     echo '{UDEV_RULE}' \\")
            print("       | sudo tee /etc/udev/rules.d/99-ftdi-latency.rules")
            print("     sudo udevadm control --reload-rules && sudo udevadm trigger")


def check_gauge() -> int:
    """Open the gauge and measure the achieved rate."""
    try:
        from nextech_force import ForceGauge, GaugeConfig
        from nextech_force.backends import describe_platform
    except ImportError as exc:
        print(f"FAIL: cannot import nextech_force: {exc}")
        return 1

    print(f"\nbackends      : {describe_platform()}")
    try:
        with ForceGauge(GaugeConfig(latency_ms=1)) as gauge:
            print(f"transport     : {gauge.backend_name}")
            print(f"device        : {gauge.device_info}")
            print(f"latency timer : {gauge._dev.get_latency()} ms")
            samples = list(gauge.stream(max_samples=300))
            span = samples[-1].t_recv - samples[0].t_send
            good = [s for s in samples if s.value is not None]
            rate = len(samples) / span
            lat = sorted(s.latency_ms for s in samples)
            print(f"rate          : {rate:.1f} Hz")
            print(f"latency       : median {lat[len(lat)//2]:.2f} ms")
            print(f"parsed        : {len(good)}/{len(samples)}")
            print(f"tare          : {gauge.zero()}")

            if rate < 100:
                print(f"\nWARNING: {rate:.0f} Hz is the 16 ms latency-timer ceiling. "
                      "Apply the udev rule above to reach ~170 Hz.")
            else:
                print(f"\nOK: {rate:.0f} Hz -- matches the Windows result.")
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic tool
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1
    return 0


def main() -> int:
    check_environment()
    return check_gauge()


if __name__ == "__main__":
    sys.exit(main())
