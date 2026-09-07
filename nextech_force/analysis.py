"""Diagnostics for how fast the gauge really is.

These answer a question the raw poll rate cannot: whether each poll returns a
freshly converted reading or the same held register read repeatedly. A gauge
that refreshed at 10 Hz while being polled at 200 Hz would hold each value for
~20 polls, which shows up three independent ways -- quantized change gaps, a
staircase in the autocorrelation, and a spectrum that rolls off instead of
reaching a white floor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from .trace import Trace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateReport:
    """Summary of achieved rate and evidence about sample freshness."""

    poll_rate_hz: float
    median_gap_ms: float
    n_samples: int
    n_changes: int
    change_rate_hz: float
    min_change_gap_ms: float
    p05_change_gap_ms: float
    median_run_length: float
    distinct_values: int

    def verdict(self) -> str:
        """Plain-language reading of whether samples are fresh."""
        if self.n_changes < 3:
            return ("Too few value changes to judge; the reading was static. "
                    "Re-run while varying the load.")
        poll_period = 1000.0 / self.poll_rate_hz
        if self.min_change_gap_ms <= poll_period * 1.5:
            return (f"Values change between polls only "
                    f"{self.min_change_gap_ms:.1f} ms apart, so the gauge is "
                    f"refreshing at least as fast as the {self.poll_rate_hz:.0f} Hz "
                    f"poll rate. No held-register quantization.")
        implied = 1000.0 / self.min_change_gap_ms
        return (f"Shortest observed change gap is {self.min_change_gap_ms:.1f} ms, "
                f"implying an internal update near {implied:.0f} Hz -- below the "
                f"poll rate, so some polls return repeated values.")


def rate_report(trace: Trace) -> RateReport:
    """Infer the effective update rate from a fast-polled trace.

    Whenever the reported value changes, that change lands on an internal
    update tick. Sampling well above the internal period makes the gaps between
    distinct readings quantize to multiples of it.
    """
    if len(trace) < 3:
        raise ValueError("Need at least 3 samples for a rate report")
    t, v = trace.t, trace.force
    gaps = np.diff(t)
    poll_rate = len(t) / (t[-1] - t[0])

    changes = np.flatnonzero(np.diff(v) != 0)
    if len(changes) >= 2:
        change_gaps = np.diff(t[changes + 1]) * 1e3
        min_gap = float(change_gaps.min())
        p05 = float(np.percentile(change_gaps, 5))
    else:
        min_gap = p05 = float("nan")

    runs, current = [], 1
    for i in range(1, len(v)):
        if v[i] == v[i - 1]:
            current += 1
        else:
            runs.append(current)
            current = 1
    runs.append(current)

    return RateReport(
        poll_rate_hz=float(poll_rate),
        median_gap_ms=float(np.median(gaps) * 1e3),
        n_samples=len(t),
        n_changes=int(len(changes)),
        change_rate_hz=float(len(changes) / (t[-1] - t[0])),
        min_change_gap_ms=min_gap,
        p05_change_gap_ms=p05,
        median_run_length=float(np.median(runs)),
        distinct_values=int(len(np.unique(v))),
    )


def autocorrelation(trace: Trace, max_lag: int = 40) -> Tuple[np.ndarray, np.ndarray]:
    """Normalised autocorrelation of the force signal.

    A hold-and-repeat register keeps correlation near 1 across the hold and then
    drops off a cliff. Fresh per-poll conversions decay immediately from lag 1.

    Returns:
        (lag_times_s, correlation) truncated to max_lag.
    """
    v = trace.force - trace.force.mean()
    n = v.size
    if n < max_lag + 2:
        raise ValueError(f"Need more than {max_lag + 2} samples")
    ac = np.correlate(v, v, mode="full")[n - 1:]
    ac = ac / ac[0]
    dt = float(np.median(np.diff(trace.t)))
    lags = np.arange(max_lag + 1) * dt
    return lags, ac[:max_lag + 1]


def band_power(trace: Trace, bands: Optional[list] = None) -> Dict[str, float]:
    """Welch band powers, normalised to the lowest band.

    An internally filtered gauge keeps rolling off across the whole span. Fresh
    conversions bottom out on a flat white noise floor well before Nyquist.

    Returns:
        Mapping of "lo-hi Hz" to power relative to the first band, plus the
        absolute white-noise floor under key "white_floor_mN".
    """
    v, t = trace.force, trace.t
    fs = 1.0 / float(np.median(np.diff(t)))
    seg = len(v) // 8
    if seg < 16:
        raise ValueError("Trace too short for a spectrum")
    win = np.hanning(seg)
    segments = [v[i:i + seg] for i in range(0, len(v) - seg, seg // 2)]
    psd = np.zeros(seg // 2 + 1)
    for s in segments:
        psd += np.abs(np.fft.rfft((s - s.mean()) * win)) ** 2
    psd /= len(segments) * (win ** 2).sum() * fs
    freq = np.fft.rfftfreq(seg, 1 / fs)

    nyq = fs / 2
    bands = bands or [(0.5, 2), (2, 5), (5, 10), (10, 20), (20, 40),
                      (40, min(60, nyq)), (min(60, nyq), nyq)]
    ref_mask = (freq >= bands[0][0]) & (freq < bands[0][1])
    ref = psd[ref_mask].mean() if ref_mask.any() else np.nan

    out: Dict[str, float] = {}
    for lo, hi in bands:
        mask = (freq >= lo) & (freq < hi)
        if mask.any() and hi > lo:
            out[f"{lo:g}-{hi:g} Hz"] = float(10 * np.log10(psd[mask].mean() / ref))

    hf_mask = freq >= 0.4 * nyq
    if hf_mask.any():
        out["white_floor_mN"] = float(np.sqrt(psd[hf_mask].mean() * fs / 2) * 1000)
    return out


def step_response(trace: Trace, low_frac: float = 0.1,
                  high_frac: float = 0.9) -> Dict[str, float]:
    """Measure the fastest 10-90% transition in a trace containing force steps.

    This is the number that matters for tactile work: poll rate bounds how often
    you can ask, but rise time bounds what the sensor can actually follow.

    Returns:
        Dict with fastest/median rise time in ms, implied bandwidth, and the
        peak slope. Empty if no full-span edge was found.

    Raises:
        ValueError: If the trace never moved enough to contain a step.
    """
    v, t = trace.force, trace.t
    span = float(v.max() - v.min())
    if span < 1.0:
        raise ValueError(
            f"Force span is only {span:.2f} N; press the load cell firmly "
            "and re-record so there is a real step to measure")

    thr_lo = v.min() + low_frac * span
    thr_hi = v.min() + high_frac * span

    # Compression reads negative, so a press is a falling edge and the release
    # is a rising one. Both are equally valid measurements of how fast the gauge
    # tracks a change, so classify each sample into a low/high state and time
    # every crossing between opposite states in either direction.
    state = np.zeros(v.size, dtype=np.int8)
    state[v <= thr_lo] = -1
    state[v >= thr_hi] = 1
    settled = np.flatnonzero(state != 0)

    rises = []
    for a, b in zip(settled[:-1], settled[1:]):
        if state[a] != state[b]:
            # Last sample in one state to first sample in the other is exactly
            # the low_frac-to-high_frac transition time.
            rises.append((t[b] - t[a]) * 1e3)

    slope = float(np.max(np.abs(np.gradient(v, t))))
    if not rises:
        return {"peak_slope_N_per_s": slope}
    rises.sort()
    fastest = rises[0]
    return {
        "fastest_rise_ms": fastest,
        "median_rise_ms": float(np.median(rises)),
        "n_edges": float(len(rises)),
        "peak_slope_N_per_s": slope,
        # 0.35 / t_r is the standard first-order rise-time to -3 dB conversion.
        "bandwidth_hz": 0.35 / (fastest / 1e3),
    }
