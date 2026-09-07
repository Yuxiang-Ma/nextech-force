"""Smoke test: identify the gauge and capture raw responses to each output command."""
import time

import serial

PORT = "COM4"

with serial.Serial(port=PORT, baudrate=38400, bytesize=8,
                   stopbits=serial.STOPBITS_ONE, timeout=1.0) as ser:
    time.sleep(0.2)
    ser.reset_input_buffer()

    for cmd, label in [("!", "device info"), ("l", "long"), ("v", "short"),
                       ("L", "mini"), ("x", "print"), ("p", "peak tension"),
                       ("c", "peak compression")]:
        ser.reset_input_buffer()
        ser.write(cmd.encode())
        time.sleep(0.25)
        raw = ser.read_all()
        print(f"{label:18s} cmd={cmd!r:5s} -> {raw!r}")
