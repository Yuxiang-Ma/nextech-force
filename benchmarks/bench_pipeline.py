"""Does the gauge accept pipelined (queued) commands, or only one at a time?

Send a burst of N poll commands, then read whatever comes back for a fixed
window. If the gauge buffers commands we get N replies; if it only services one
command at a time we get far fewer, which caps any pipelining strategy.
"""
import time

import serial

PORT = "COM4"


def burst_probe(ser: serial.Serial, cmd: str, burst: int, window: float = 3.0):
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    t0 = time.perf_counter()
    ser.write(cmd.encode() * burst)
    buf = bytearray()
    t_last = None
    while time.perf_counter() - t0 < window:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            t_last = time.perf_counter()
    replies = [ln for ln in buf.replace(b"\r", b"\n").split(b"\n") if ln.strip()]
    span = (t_last - t0) if t_last else float("nan")
    rate = len(replies) / span if span and span == span and span > 0 else 0.0
    return len(replies), span * 1e3, rate


with serial.Serial(port=PORT, baudrate=38400, bytesize=8,
                   stopbits=serial.STOPBITS_ONE, timeout=0.2) as ser:
    time.sleep(0.2)
    for cmd, label in [("L", "mini "), ("l", "long ")]:
        print(f"--- {label} (cmd={cmd!r}) ---", flush=True)
        for burst in (1, 2, 5, 10, 50):
            got, span_ms, rate = burst_probe(ser, cmd, burst)
            print(f"  sent={burst:3d}  replies={got:3d}  span={span_ms:8.1f} ms  "
                  f"eff={rate:7.1f} Hz", flush=True)
