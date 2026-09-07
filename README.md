# nextech-force

[![CI](https://github.com/Yuxiang-Ma/nextech-force/actions/workflows/ci.yml/badge.svg)](https://github.com/Yuxiang-Ma/nextech-force/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

High-rate recording and visualization for Nextech DFS/DFT force gauges.

The vendor library `nexgraphpy` reports **~10 samples/s**. That figure is a
software artifact, not a hardware limit. This package reaches **~174 Hz** on the
same gauge over the same USB cable — no hardware change, no registry edit, no
administrator rights.

Characterized on `DFS-X 100N`, firmware 3.07, serial 0000009261 (100 N range,
0.02 N resolution, FTDI FT232R at 38400 baud).

---

## Install

```bash
pip install nextech-force            # driver only — no numpy, no matplotlib
pip install nextech-force[viz]       # + numpy, matplotlib, CLI plots
```

**`import nextech_force` pulls only the standard library.** The acquisition path
— gauge, recorder, horizons, CSV streaming — has zero third-party dependencies,
so it drops into an existing data-collection pipeline without dragging numpy or
matplotlib into your process. Analysis loads numpy on first use; plotting loads
matplotlib only when a figure is actually drawn. This is enforced by tests that
check `sys.modules` in a subprocess, not by convention.

| What you import | Cost |
|---|---|
| `ForceGauge`, `Recorder`, `record`, horizons, CSV | stdlib only (`pyserial` on Linux) |
| `Trace`, `rate_report`, `band_power` | + numpy |
| `plot_trace`, `live_record` | + matplotlib |

## Quick start

Minimal embedding — no third-party deps, nothing to configure:

```python
from nextech_force import ForceGauge

with ForceGauge() as gauge:          # auto-detects backend and port
    gauge.zero()
    for s in gauge.stream(duration_s=10):
        pipeline.push(t=s.t_mid, force_n=s.value)
```

Record and plot (needs `[viz]`):

```python
from nextech_force import ForceGauge, create_horizon, record, plot_trace

with ForceGauge() as gauge:
    trace = record(gauge, create_horizon("fixed", duration_s=20))
plot_trace(trace, save_path="force.png")
```

Live view:

```python
from nextech_force import create_horizon, live_record

live_record(create_horizon("fixed", duration_s=20))     # bounded, 20 s
live_record(create_horizon("infinite", window_s=20))    # until you stop it
```

## Does it work on Ubuntu?

**Yes** — via the `serial` backend, which is selected automatically. Version 0.1
was Windows-only (it called `ctypes.WinDLL` at import, so the package would not
even import on Linux); that is fixed.

```bash
sudo usermod -aG dialout $USER      # then log out and back in
pip install nextech-force
python -c "from nextech_force import ForceGauge; \
           print(ForceGauge().connect() or 'see logs')"
```

To get the full ~170 Hz you must lower the FTDI latency timer. Linux exposes the
same 16 ms default as Windows, at
`/sys/bus/usb-serial/devices/ttyUSB0/latency_timer`. The package writes it
automatically when permitted; the file is root-owned by default, so make it
stick with a udev rule:

```bash
echo 'ACTION=="add", SUBSYSTEM=="usb-serial", DRIVER=="ftdi_sio", ATTR{latency_timer}="1"' \
  | sudo tee /etc/udev/rules.d/99-ftdi-latency.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Without it you get ~60 Hz and a warning naming this exact fix — the package
degrades rather than refusing to run.

### What is actually verified, and what is not

Being precise, because I have no Ubuntu machine with this gauge attached:

| Claim | Status |
|---|---|
| Package imports on Linux (no `WinDLL` at import) | **verified in CI on `ubuntu-latest`** — bare install with neither numpy nor matplotlib present |
| Linux backend selection prefers `serial` | **verified in CI** — Ubuntu reports `available backends: serial` |
| Serial transport drives the real gauge | **verified on hardware** — `benchmarks/backend_matrix.py`, 250/250 parsed, tare OK |
| Whole Linux code path, end to end | **verified on hardware** — `benchmarks/linux_path_simulation.py` forces the Linux branches and records from the real gauge |
| sysfs latency write, `/dev/ttyUSB*` naming | **not verified** — Linux-only filesystem. Logic is unit-tested against an injected filesystem |

CI runs the full suite plus the 294-value parity check on `ubuntu-latest`
(Python 3.9 and 3.12) on every push, so everything except the gauge itself is
continuously verified on Linux.

Run `python benchmarks/ubuntu_check.py` on the Ubuntu box to close the last row.
It reports device nodes, group membership, the latency timer, and the achieved
rate, and prints the fix for whatever is missing.

macOS should work through either backend but is untested.

## The two horizon modes

Recording length and the plot's visible window are one decision, so a single
`Horizon` object drives both. The recorder, live plot and static plot code are
identical between modes.

| | `fixed` (default 20 s) | `infinite` |
|---|---|---|
| stops on its own | yes, after `duration_s` | no — Ctrl-C or close the window |
| x-axis | pinned to `(0, duration_s)` from frame one | scrolls to the last `window_s` |
| progress readout | percentage bar | elapsed time |
| use it for | repeatable trials, camera-synced clips | monitoring, tuning, open-ended capture |

`fixed` pins the axis so the trace fills left to right and the scale never
shifts mid-run. `infinite` shows `(0, window_s)` until the window fills, then
scrolls; pass `window_s=None` to keep the whole history in view.

Add a mode without touching any call site:

```python
from nextech_force import Horizon, register_horizon

@register_horizon("triggered")
class TriggeredHorizon(Horizon):
    ...
```

## CLI

```bash
nextech-force info                                    # platform, backend, rate
nextech-force record --mode fixed --duration 20 \
    --csv run.csv --plot run.png
nextech-force live   --mode infinite --window 20
nextech-force plot    run.csv -o run.png
nextech-force analyze run.csv                         # rate / freshness / bandwidth
```

Flags: `--backend {auto,d2xx,serial}`, `--port COM4|/dev/ttyUSB0`,
`--latency MS` (default 1), `--format {mini,short,long}` (default `mini`).
Commands needing an extra say which one is missing instead of tracebacking.

## Measured rates

| Configuration | Rate | Bottleneck |
|---|---|---|
| stock `nexgraphpy` | **9.86 Hz** | hardcoded `time.sleep(0.1)` per command |
| sleep removed, VCP serial | **59.8 Hz** | FTDI latency timer = 16 ms |
| D2XX + latency timer 1 ms | **173.9 Hz** | gauge round-trip (~5 ms) |

### Bottleneck 1 — the library

`nexgraphpy/nexgraph.py:295`, in `_get_output()`:

```python
self.usb_serial.write(self.device_command[command_key])
time.sleep(0.1)          # <-- the entire "10 samples/s" spec
```

### Bottleneck 2 — the FTDI latency timer

With the sleep gone, polling pinned at exactly 16.0 ms median — the FTDI driver
default (`LatencyTimer = 16` in the registry on Windows, `latency_timer` in
sysfs on Linux). The sweep confirms causation:

| Latency timer | mini (`L`) | short (`v`) | long (`l`) |
|---|---|---|---|
| 16 ms | 60.1 Hz | 59.8 Hz | 59.4 Hz |
| 8 ms | 109.8 Hz | 101.1 Hz | 102.7 Hz |
| 4 ms | 127.5 Hz | 107.6 Hz | 107.5 Hz |
| 2 ms | 153.2 Hz | 116.0 Hz | 121.4 Hz |
| 1 ms | **171.0 Hz** | 116.6 Hz | 120.9 Hz |

The two backends reproduce this independently: `d2xx` sets the timer and reaches
177.7 Hz, while `serial` on Windows cannot (registry-only) and sits at 60.8 Hz
with a 15.97 ms median — the same ceiling through a second code path.

## Protocol facts

- **The gauge does not queue commands.** A burst of 50 polls returns exactly
  **one** reply. Pipelining to hide USB latency does not work; keep one poll
  outstanding at a time.
- Strictly request/response — there is no free-running streaming mode.
- Sign is carried by prefix: `C:` = compression (negative), `T:` = tension.
- While a backend holds the device the port is unavailable to other programs
  (including the NexGraph GUI). Closing the gauge releases it.

## Is the data actually fresh at 170 Hz?

Yes — the gauge is not returning a stale register. From a 25 s resting trace
(`data/rest_dither.csv`, reproducible via `nextech-force analyze`):

- The reading changes **32×/s**, with a **minimum 4.5 ms gap between differing
  values** — consecutive polls returning different numbers. A 10 Hz internal
  update would floor that gap at ~100 ms and cluster all gaps at multiples of
  100 ms. It does not.
- Autocorrelation decays smoothly from lag 1 with **no staircase plateau**. A
  hold-and-repeat register holds correlation ≈1 across the hold, then cliffs.
- The spectrum flattens into a white floor (~4.7 mN rms) out to Nyquist instead
  of continuing to roll off.

**Caveat, stated plainly:** this establishes that the *transport and conversion*
are fresh at ~170 Hz. It does **not** establish the sensor's mechanical/filter
bandwidth, because at zero load the signal is dominated by low-frequency drift.
To measure that, record while physically pressing and releasing the cell:

```bash
nextech-force record --mode fixed --duration 20 --csv step.csv
nextech-force analyze step.csv        # 10-90% rise time -> bandwidth
```

`analyze` refuses to guess: if the force never moved more than 1 N it says so
rather than reporting a meaningless number.

## Timestamps and camera sync

Every `Sample` carries `t_send`, `t_recv`, `latency_ms` and `t_mid`. Use `t_mid`
as the acquisition timestamp — the conversion happened inside the round trip, so
the midpoint bounds the error at half the latency (~2.5 ms), well under one
frame at 30–60 fps. At 174 Hz you get 3–6 force samples per camera frame.

Acquisition runs on its own thread and is never gated by drawing: the live plot
redraws at 30 fps while the recorder keeps polling at full rate (measured:
169 Hz sustained across 275 draw frames).

## Layout

| Path | Purpose |
|---|---|
| `nextech_force/gauge.py` | `ForceGauge` — connect, poll, tare, parse (stdlib only) |
| `nextech_force/backends/` | `base` interface, `d2xx` (Windows), `serial_vcp` (Linux) |
| `nextech_force/horizon.py` | `fixed` / `infinite` horizons + registry |
| `nextech_force/recorder.py` | threaded acquisition, ring buffer, CSV streaming |
| `nextech_force/trace.py` | `Trace` container, stats, CSV round-trip |
| `nextech_force/plotting.py` | static PNG rendering |
| `nextech_force/live.py` | live animated plot |
| `nextech_force/analysis.py` | rate / freshness / spectrum / step response |
| `nextech_force/cli.py` | `nextech-force` command |
| `benchmarks/` | hardware characterization behind every number above |
| `tests/` | 75 tests, no hardware required |

## Development

```bash
pip install -e ".[dev]"
pytest -q                                             # 75 tests, hardware-free
ruff check .
python benchmarks/parity_snapshot.py compare benchmarks/parity_golden.json
```

`parity_golden.json` pins 294 deterministic values — parsing, horizon geometry,
trace statistics, spectra, step detection — computed from the recorded CSVs. Any
refactor must reproduce all 294 exactly. The v0.2 cross-platform refactor did.

## License

MIT
