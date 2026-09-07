"""Measure true poll rate of the Nextech gauge, bypassing nexgraphpy's fixed 100 ms sleep.

For each output command we time a full request->response round trip and report
the achieved sample rate and latency distribution.
"""
import statistics
import time

import serial

PORT = "COM4"
N = 300


def bench(ser: serial.Serial, cmd: str, n: int = N) -> dict:
    ser.reset_input_buffer()
    latencies = []
    values = []
    t_start = time.perf_counter()
    for _ in range(n):
        t0 = time.perf_counter()
        ser.write(cmd.encode())
        line = ser.readline()          # blocks until \n or timeout
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1e3)
        values.append(line)
    wall = time.perf_counter() - t_start

    ok = sum(1 for v in values if v.endswith(b"\n"))
    return {
        "cmd": cmd,
        "n": n,
        "rate_hz": n / wall,
        "mean_ms": statistics.mean(latencies),
        "median_ms": statistics.median(latencies),
        "p95_ms": sorted(latencies)[int(0.95 * n)],
        "min_ms": min(latencies),
        "max_ms": max(latencies),
        "complete": ok,
        "sample": values[0],
    }


with serial.Serial(port=PORT, baudrate=38400, bytesize=8,
                   stopbits=serial.STOPBITS_ONE, timeout=1.0) as ser:
    time.sleep(0.2)
    for cmd, label in [("L", "mini "), ("v", "short"), ("l", "long ")]:
        r = bench(ser, cmd)
        print(f"{label} cmd={r['cmd']!r}  {r['rate_hz']:7.1f} Hz  "
              f"latency mean {r['mean_ms']:6.2f} ms  median {r['median_ms']:6.2f}  "
              f"p95 {r['p95_ms']:6.2f}  min {r['min_ms']:6.2f}  max {r['max_ms']:7.2f}  "
              f"complete {r['complete']}/{r['n']}  e.g. {r['sample']!r}")
