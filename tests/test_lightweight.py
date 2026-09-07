"""The acquisition path must stay dependency-free.

This package is meant to be dropped into an existing data-collection pipeline,
so `import nextech_force` pulling numpy and matplotlib would be a real cost paid
by every consumer. These tests run the import in a *subprocess* -- checking
sys.modules in-process would be meaningless once the rest of the suite has
already imported numpy.
"""
import subprocess
import sys
import textwrap

import pytest

CORE_NAMES = ["ForceGauge", "Sample", "Recorder", "record", "create_horizon",
              "GaugeConfig", "RecordingConfig", "FixedHorizon", "InfiniteHorizon",
              "open_backend", "available_backends"]


def _run(code: str) -> str:
    """Run code in a clean interpreter and return stdout."""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"subprocess failed:\n{result.stderr}"
    return result.stdout.strip()


def test_import_pulls_no_heavy_dependencies():
    out = _run("""
        import sys
        import nextech_force
        heavy = [m for m in ('numpy', 'matplotlib', 'serial', 'scipy', 'pandas')
                 if m in sys.modules]
        print(','.join(heavy) if heavy else 'NONE')
    """)
    assert out == "NONE", f"import nextech_force pulled in: {out}"


def test_core_api_is_available_without_extras():
    out = _run(f"""
        import nextech_force as nf
        missing = [n for n in {CORE_NAMES!r} if not hasattr(nf, n)]
        print(','.join(missing) if missing else 'ALL')
    """)
    assert out == "ALL", f"missing from bare install: {out}"


def test_numpy_arrives_only_when_trace_is_touched():
    out = _run("""
        import sys
        import nextech_force as nf
        before = 'numpy' in sys.modules
        _ = nf.Trace
        after = 'numpy' in sys.modules
        print(f'{before},{after}')
    """)
    assert out == "False,True"


def test_matplotlib_arrives_only_when_a_plot_is_drawn(tmp_path):
    """Even importing plotting must not cost matplotlib -- only drawing does."""
    out = _run(f"""
        import sys
        import nextech_force as nf
        _ = nf.Trace                       # numpy, but still no matplotlib
        _ = nf.plot_trace                  # module imported, backend not yet
        mid = 'matplotlib' in sys.modules
        nf.plot_trace(nf.Trace(t=[0.0, 1.0], force=[0.0, -1.0]),
                      save_path={str(tmp_path / 'p.png')!r})
        after = 'matplotlib' in sys.modules
        print(f'{{mid}},{{after}}')
    """)
    assert out == "False,True"


def test_recorder_module_does_not_import_numpy():
    """Recording and CSV streaming must work on a bare install."""
    out = _run("""
        import sys
        import nextech_force.recorder  # noqa: F401
        print('numpy' in sys.modules)
    """)
    assert out == "False"


def test_gauge_module_does_not_import_numpy():
    out = _run("""
        import sys
        import nextech_force.gauge  # noqa: F401
        print('numpy' in sys.modules)
    """)
    assert out == "False"


def test_csv_header_stays_in_sync():
    """recorder.py duplicates CSV_HEADER to stay numpy-free; keep them equal.

    The duplication is deliberate, so this guards the one hazard it creates:
    a column added to one and not the other would silently produce CSVs whose
    header disagrees with their rows.
    """
    from nextech_force import recorder, trace
    assert recorder.CSV_HEADER == trace.CSV_HEADER


def test_lazy_attribute_error_is_still_an_attribute_error():
    import nextech_force as nf
    with pytest.raises(AttributeError, match="no attribute"):
        _ = nf.does_not_exist


def test_dir_lists_the_public_api():
    import nextech_force as nf
    listed = dir(nf)
    for name in ("ForceGauge", "Trace", "plot_trace", "live_record"):
        assert name in listed
