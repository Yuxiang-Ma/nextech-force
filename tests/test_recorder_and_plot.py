"""Recorder and plotting against a fake gauge, so these run with no hardware."""
import time

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg", force=True)

from nextech_force import (
    FixedHorizon,
    InfiniteHorizon,
    PlotConfig,
    RecordingConfig,
    Trace,
    create_horizon,
    plot_trace,
)
from nextech_force.config import GaugeConfig
from nextech_force.gauge import Sample
from nextech_force.recorder import Recorder, record


class FakeGauge:
    """Stands in for ForceGauge, emitting a sine at a controllable rate."""

    def __init__(self, period_s=0.002):
        self.config = GaugeConfig()
        self.device_info = "FAKE-100N 0000 1.00"
        self.model = "FAKE-100N"
        self.period_s = period_s
        self.zeroed = False
        self._n = 0

    def zero(self):
        self.zeroed = True
        return True

    def read(self):
        t0 = time.perf_counter()
        if self.period_s:
            time.sleep(self.period_s)
        t1 = time.perf_counter()
        self._n += 1
        value = -5.0 * np.sin(2 * np.pi * 0.5 * t1)
        return Sample(t_send=t0, t_recv=t1, value=value, raw=b"x")


def test_fixed_horizon_stops_itself():
    gauge = FakeGauge()
    trace = record(gauge, FixedHorizon(duration_s=0.4),
                   RecordingConfig(tare_on_start=True, settle_s=0.0))
    assert 0.3 <= trace.duration_s <= 0.7
    assert len(trace) > 10
    assert gauge.zeroed


def test_tare_can_be_skipped():
    gauge = FakeGauge()
    record(gauge, FixedHorizon(duration_s=0.2),
           RecordingConfig(tare_on_start=False))
    assert not gauge.zeroed


def test_infinite_horizon_runs_until_stopped():
    gauge = FakeGauge()
    recorder = Recorder(gauge, InfiniteHorizon(window_s=5.0),
                        RecordingConfig(tare_on_start=False))
    recorder.start()
    try:
        deadline = time.perf_counter() + 10.0
        while recorder.n_samples < 10 and time.perf_counter() < deadline:
            time.sleep(0.005)
        assert recorder.is_running      # would have stopped if bounded
    finally:
        recorder.stop()
    assert not recorder.is_running
    assert recorder.n_samples >= 10


def test_snapshot_is_safe_while_running():
    """The plot reads snapshots concurrently; this must never tear or block."""
    gauge = FakeGauge()
    recorder = Recorder(gauge, InfiniteHorizon(), RecordingConfig(tare_on_start=False))
    recorder.start()
    try:
        for _ in range(30):
            t, v = recorder.snapshot()
            assert isinstance(t, list) and isinstance(v, list)
            assert len(t) == len(v)
            assert all(b >= a for a, b in zip(t, t[1:]))   # monotonic
            time.sleep(0.01)
    finally:
        recorder.stop()


def test_buffer_rolls_but_count_keeps_climbing():
    """The ring buffer caps what is retained; the total count keeps rising.

    Waits for the sample count rather than assuming a throughput: sleep
    granularity differs enough across hosts (~15 ms on Windows) that a fixed
    wall-clock window is not a reliable way to reach a sample target.
    """
    gauge = FakeGauge(period_s=0.0)
    recorder = Recorder(gauge, InfiniteHorizon(),
                        RecordingConfig(tare_on_start=False, buffer_samples=50))
    recorder.start()
    try:
        deadline = time.perf_counter() + 10.0
        while recorder.n_samples <= 50 and time.perf_counter() < deadline:
            time.sleep(0.005)
    finally:
        recorder.stop()
    t, _ = recorder.snapshot()
    assert recorder.n_samples > 50, "acquisition never reached 50 samples in 10 s"
    assert len(t) <= 50                  # ring buffer capped


def test_csv_is_streamed_during_recording(tmp_path):
    path = tmp_path / "stream.csv"
    gauge = FakeGauge()
    record(gauge, FixedHorizon(duration_s=0.3),
           RecordingConfig(csv_path=str(path), tare_on_start=False))
    assert path.exists()
    loaded = Trace.load_csv(str(path))
    assert len(loaded) > 5
    assert loaded.metadata["device"] == "FAKE-100N 0000 1.00"
    assert "fixed" in loaded.metadata["horizon"]


def test_double_start_is_rejected():
    recorder = Recorder(FakeGauge(), InfiniteHorizon(),
                        RecordingConfig(tare_on_start=False))
    recorder.start()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            recorder.start()
    finally:
        recorder.stop()


def test_plot_writes_a_png(tmp_path):
    n = 400
    trace = Trace(t=np.arange(n) / 175.0,
                  force=-3 * np.sin(np.arange(n) / 20.0),
                  latency_ms=np.full(n, 5.0),
                  metadata={"device": "FAKE-100N"})
    out = tmp_path / "p.png"
    plot_trace(trace, PlotConfig(), horizon=create_horizon("fixed", duration_s=20),
               save_path=str(out))
    assert out.exists() and out.stat().st_size > 5000


def test_plot_handles_an_empty_trace(tmp_path):
    out = tmp_path / "empty.png"
    plot_trace(Trace(t=np.empty(0), force=np.empty(0)), save_path=str(out))
    assert out.exists()


def test_plot_handles_a_perfectly_flat_trace(tmp_path):
    """An unloaded, tared gauge reads exactly zero -- ylim must not collapse."""
    n = 200
    trace = Trace(t=np.arange(n) / 175.0, force=np.zeros(n))
    out = tmp_path / "flat.png"
    fig = plot_trace(trace, save_path=str(out))
    lo, hi = fig.axes[0].get_ylim()
    assert hi > lo
    assert out.exists()
