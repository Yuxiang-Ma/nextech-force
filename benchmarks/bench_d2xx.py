"""Poll rate vs. FTDI latency timer, over D2XX.

Sweeps the latency timer to separate the USB-driver bottleneck from the gauge's
own response time, for each of the three output formats.
"""
import statistics
import time

from nextech_force.d2xx import D2xxDevice

N = 400


def bench(dev: D2xxDevice, cmd: bytes, n: int = N) -> dict:
    dev.purge()
    lat, vals = [], []
    t_start = time.perf_counter()
    for _ in range(n):
        t0 = time.perf_counter()
        dev.write(cmd)
        buf = bytearray()
        while time.perf_counter() - t0 < 0.5:
            q = dev.in_waiting()
            if q:
                buf += dev.read(q)
                if b"\n" in buf:
                    break
        lat.append((time.perf_counter() - t0) * 1e3)
        vals.append(bytes(buf).strip())
    wall = time.perf_counter() - t_start
    s = sorted(lat)
    return {"hz": n / wall, "mean": statistics.mean(lat), "med": s[n // 2],
            "p05": s[int(.05 * n)], "p95": s[int(.95 * n)], "max": s[-1],
            "uniq": len(set(vals))}


for lt in (16, 8, 4, 2, 1):
    with D2xxDevice(0, latency_ms=lt) as d:
        assert d.get_latency() == lt
        print(f"=== latency timer = {lt:2d} ms ===", flush=True)
        for cmd, label in [(b"L", "mini "), (b"v", "short"), (b"l", "long ")]:
            r = bench(d, cmd)
            print(f"  {label} {r['hz']:7.1f} Hz | mean {r['mean']:6.2f} "
                  f"med {r['med']:6.2f} "
                  f"p05 {r['p05']:6.2f} p95 {r['p95']:6.2f} max {r['max']:7.2f} ms | "
                  f"uniq {r['uniq']}", flush=True)
