"""Sustained poll rate using busy-wait reads instead of blocking readline().

readline() pays the FTDI latency-timer quantum on every call. Spinning on
in_waiting picks the reply up as soon as the driver surfaces it.
"""
import statistics
import time

import serial

PORT = "COM4"
N = 400


def poll_once(ser: serial.Serial, cmd: bytes, timeout: float = 0.5) -> tuple:
    ser.write(cmd)
    buf = bytearray()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            if b"\n" in buf:
                break
    return (time.perf_counter() - t0) * 1e3, bytes(buf)


with serial.Serial(port=PORT, baudrate=38400, bytesize=8,
                   stopbits=serial.STOPBITS_ONE, timeout=0.5) as ser:
    time.sleep(0.2)
    for cmd, label in [(b"L", "mini "), (b"v", "short"), (b"l", "long ")]:
        ser.reset_input_buffer()
        lat, vals, misses = [], [], 0
        t_start = time.perf_counter()
        for _ in range(N):
            ms, raw = poll_once(ser, cmd)
            lat.append(ms)
            if not raw.endswith(b"\n"):
                misses += 1
            vals.append(raw.strip())
        wall = time.perf_counter() - t_start
        s = sorted(lat)
        print(f"{label} {N/wall:7.1f} Hz sustained | "
              f"latency mean {statistics.mean(lat):6.2f} "
              f"median {s[N//2]:6.2f} p05 {s[int(.05*N)]:6.2f} p95 {s[int(.95*N)]:6.2f} "
              f"max {s[-1]:7.2f} ms | miss {misses} | uniq {len(set(vals))}", flush=True)
