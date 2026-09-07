"""Analysis must distinguish a fresh gauge from a held-register one.

These build traces whose ground truth is known by construction: one where the
value genuinely updates every poll, and one where a 10 Hz register is oversampled
at 200 Hz. The report has to tell them apart, because that distinction is the
whole basis for claiming the gauge beats its documented 10 Hz.
"""
import numpy as np
import pytest

from nextech_force import Trace, band_power, rate_report, step_response
from nextech_force.analysis import autocorrelation

FS = 200.0
QUANTUM = 0.02  # N, the DFS-X display resolution


def _fresh_trace(seconds=20.0, seed=0):
    """Every poll is an independent conversion of a drifting signal."""
    rng = np.random.default_rng(seed)
    n = int(seconds * FS)
    t = np.arange(n) / FS
    drift = 0.05 * np.sin(2 * np.pi * 0.3 * t)
    noise = rng.normal(0, 0.012, n)
    return Trace(t=t, force=np.round((drift + noise) / QUANTUM) * QUANTUM)


def _held_trace(internal_hz=10.0, seconds=20.0, seed=0):
    """A 10 Hz register read at 200 Hz: each value repeats ~20 times."""
    rng = np.random.default_rng(seed)
    n = int(seconds * FS)
    t = np.arange(n) / FS
    n_internal = int(seconds * internal_hz)
    internal = np.round(rng.normal(0, 0.04, n_internal) / QUANTUM) * QUANTUM
    idx = np.minimum((t * internal_hz).astype(int), n_internal - 1)
    return Trace(t=t, force=internal[idx])


def test_fresh_trace_reports_poll_rate():
    report = rate_report(_fresh_trace())
    assert report.poll_rate_hz == pytest.approx(FS, rel=0.02)
    assert report.n_samples == 4000


def test_fresh_trace_changes_between_adjacent_polls():
    report = rate_report(_fresh_trace())
    poll_period_ms = 1000.0 / FS
    assert report.min_change_gap_ms <= poll_period_ms * 1.5
    assert "at least as fast" in report.verdict()


def test_held_register_is_detected():
    report = rate_report(_held_trace(internal_hz=10.0))
    # Changes cannot occur closer together than the 100 ms internal period.
    assert report.min_change_gap_ms >= 90.0
    assert report.median_run_length >= 15
    assert "internal update near" in report.verdict()


def test_held_register_implied_rate_is_about_right():
    report = rate_report(_held_trace(internal_hz=20.0))
    implied = 1000.0 / report.min_change_gap_ms
    assert 15.0 <= implied <= 25.0


def test_autocorrelation_shows_plateau_only_when_held():
    _, ac_held = autocorrelation(_held_trace(internal_hz=10.0), max_lag=10)
    _, ac_fresh = autocorrelation(_fresh_trace(), max_lag=10)
    # A held register stays highly correlated across the hold; fresh does not.
    assert ac_held[1] > 0.9
    assert ac_fresh[1] < ac_held[1]


def test_band_power_reports_white_floor():
    powers = band_power(_fresh_trace())
    assert "white_floor_mN" in powers
    assert powers["white_floor_mN"] > 0


def test_rate_report_needs_samples():
    with pytest.raises(ValueError, match="at least 3"):
        rate_report(Trace(t=np.array([0.0]), force=np.array([0.0])))


def test_step_response_measures_a_known_edge():
    """A 40 ms ramp must be recovered as roughly a 40 ms rise time."""
    n = int(5 * FS)
    t = np.arange(n) / FS
    force = np.zeros(n)
    rise_s = 0.04
    start = int(1.0 * FS)
    ramp = int(rise_s * FS)
    force[start:start + ramp] = np.linspace(0, -20, ramp)
    force[start + ramp:] = -20.0
    result = step_response(Trace(t=t, force=force))
    # 10-90% of a linear ramp covers 80% of its duration.
    assert result["fastest_rise_ms"] == pytest.approx(rise_s * 1000 * 0.8, rel=0.25)
    assert result["bandwidth_hz"] > 0


def test_step_response_refuses_a_flat_trace():
    n = int(5 * FS)
    trace = Trace(t=np.arange(n) / FS, force=np.zeros(n))
    with pytest.raises(ValueError, match="press the load cell"):
        step_response(trace)
