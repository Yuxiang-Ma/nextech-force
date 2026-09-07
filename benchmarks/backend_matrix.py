"""Exercise every available backend against the real gauge.

The serial backend is what Linux will use. It is testable here because pyserial
speaks to the same FTDI chip through the Windows VCP driver, so everything
except the Linux-only sysfs latency write runs identically. The expected
signature is that d2xx reaches ~170 Hz (it can set the latency timer) while
serial-on-Windows sits near 60 Hz (it cannot) -- which is the same 16 ms
ceiling, reproduced through a second, independent code path.
"""
from __future__ import annotations

import logging
import time

from nextech_force import ForceGauge, GaugeConfig
from nextech_force.backends import available_backends, describe_platform

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

N = 250


def bench(backend: str) -> None:
    """Connect through one backend and report identity plus achieved rate."""
    print(f"\n=== backend: {backend} ===", flush=True)
    try:
        with ForceGauge(GaugeConfig(backend=backend, latency_ms=1)) as gauge:
            print(f"  transport : {gauge.backend_name}")
            print(f"  device    : {gauge.device_info}")
            samples = list(gauge.stream(max_samples=N))
            span = samples[-1].t_recv - samples[0].t_send
            good = [s for s in samples if s.value is not None]
            lat = sorted(s.latency_ms for s in samples)
            print(f"  rate      : {len(samples)/span:7.1f} Hz")
            print(f"  latency   : median {lat[len(lat)//2]:6.2f} ms  "
                  f"p95 {lat[int(0.95*len(lat))]:6.2f} ms")
            print(f"  parsed    : {len(good)}/{len(samples)}")
            print(f"  tare      : {gauge.zero()}")
    except Exception as exc:  # noqa: BLE001 - report, do not abort the matrix
        print(f"  FAILED: {type(exc).__name__}: {exc}")


def main() -> None:
    print(describe_platform())
    for backend in available_backends():
        bench(backend)
        time.sleep(0.5)   # let the OS fully release the device between opens


if __name__ == "__main__":
    main()
