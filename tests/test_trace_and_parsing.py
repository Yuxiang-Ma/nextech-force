"""Reply parsing, Trace statistics and CSV round-tripping."""
import numpy as np
import pytest

from nextech_force import GaugeConfig, Trace
from nextech_force.gauge import ForceGauge, Sample


@pytest.mark.parametrize("raw,expected", [
    (b"-0.16", -0.16),
    (b"0.00", 0.0),
    (b"C: - 17.98 N", -17.98),      # compression -> negative
    (b"T: + 4.20 N", 4.20),         # tension -> positive
    (b"-  0.00  N", 0.0),
    (b"+12.34", 12.34),
])
def test_parse_known_replies(raw, expected):
    assert ForceGauge.parse(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [b"", b"\r\n", b"garbage"])
def test_parse_rejects_junk(raw):
    assert ForceGauge.parse(raw) is None


def test_gauge_config_validates():
    with pytest.raises(ValueError, match="output_format"):
        GaugeConfig(output_format="enormous")
    with pytest.raises(ValueError, match="latency_ms"):
        GaugeConfig(latency_ms=0)


def test_mini_format_is_the_fast_command():
    assert GaugeConfig(output_format="mini").command == b"L"
    assert GaugeConfig(output_format="long").command == b"l"


def _demo_trace(n=500, fs=175.0):
    t = np.arange(n) / fs
    force = -5.0 * np.sin(2 * np.pi * 0.5 * t)
    return Trace(t=t, force=force, latency_ms=np.full(n, 5.0),
                 metadata={"device": "DFS-X 100N", "horizon": "fixed horizon, 20 s"})


def test_trace_summary():
    trace = _demo_trace()
    s = trace.summary()
    assert s["samples"] == 500
    assert s["rate_hz"] == pytest.approx(175.0, rel=0.01)
    assert s["peak_abs_N"] == pytest.approx(5.0, abs=0.1)
    assert s["latency_median_ms"] == 5.0


def test_trace_csv_roundtrip(tmp_path):
    original = _demo_trace()
    path = tmp_path / "t.csv"
    original.save_csv(str(path))
    loaded = Trace.load_csv(str(path))

    assert len(loaded) == len(original)
    np.testing.assert_allclose(loaded.t, original.t, atol=1e-6)
    np.testing.assert_allclose(loaded.force, original.force, atol=1e-4)
    # Metadata survives as leading comment lines.
    assert loaded.metadata["device"] == "DFS-X 100N"
    assert "fixed" in loaded.metadata["horizon"]


def test_trace_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="must match"):
        Trace(t=np.zeros(5), force=np.zeros(4))


def test_slice_window():
    trace = _demo_trace()
    window = trace.slice_window(0.5, 1.0)
    assert len(window) < len(trace)
    assert window.t.min() >= 0.5 and window.t.max() <= 1.0


def test_from_samples_drops_unparsed():
    samples = [
        Sample(t_send=0.0, t_recv=0.005, value=1.0, raw=b"1.00"),
        Sample(t_send=0.006, t_recv=0.011, value=None, raw=b"junk"),
        Sample(t_send=0.012, t_recv=0.017, value=2.0, raw=b"2.00"),
    ]
    trace = Trace.from_samples(samples)
    assert len(trace) == 2
    assert trace.t[0] == 0.0                       # rebased to first good sample
    np.testing.assert_allclose(trace.force, [1.0, 2.0])


def test_empty_csv_raises(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("# device: nothing\n")
    with pytest.raises(ValueError):
        Trace.load_csv(str(path))


def test_sample_timestamps():
    sample = Sample(t_send=1.0, t_recv=1.006, value=3.0, raw=b"3.00")
    assert sample.latency_ms == pytest.approx(6.0)
    assert sample.t_mid == pytest.approx(1.003)
