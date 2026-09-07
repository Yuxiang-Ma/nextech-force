"""Capture / compare a behavioural fingerprint of the package.

Refactoring this package must not change what it measures. The already-recorded
CSVs make most of the surface a *deterministic* oracle: given a fixed input
file, parsing, statistics, spectra and step detection must produce byte-identical
output before and after the refactor. Only the live hardware rate is stochastic,
and it is reported separately with an explicit bound rather than folded into the
same verdict.

    python benchmarks/parity_snapshot.py capture  benchmarks/parity_golden.json
    python benchmarks/parity_snapshot.py compare  benchmarks/parity_golden.json

Exit code is non-zero if any deterministic value moved.
"""
from __future__ import annotations

import hashlib
import json
import sys
from typing import Any, Dict

TRACES = ["data/rest_dither.csv", "data/demo_fixed_20s.csv"]

# Replies observed on the wire from a real DFS-X 100N, plus malformed inputs.
PARSE_CASES = [
    b"-0.16", b"0.00", b"C: - 17.98 N", b"T: + 4.20 N", b"-  0.00  N  ",
    b"+12.34", b"C: - 0.00 N", b"-0.00", b"", b"\r\n", b"garbage", b"N",
]

HORIZON_PROBES = [0.0, 0.5, 3.2, 5.0, 19.9, 20.0, 25.0, 50.0]


def _round(value: Any, places: int = 6) -> Any:
    """Round floats recursively so the fingerprint is stable across platforms."""
    if isinstance(value, float):
        return round(value, places)
    if isinstance(value, dict):
        return {k: _round(v, places) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round(v, places) for v in value]
    return value


def fingerprint() -> Dict[str, Any]:
    """Compute the full deterministic fingerprint of the current code."""
    import numpy as np

    from nextech_force import (
        Trace,
        band_power,
        create_horizon,
        rate_report,
        step_response,
    )
    from nextech_force.analysis import autocorrelation
    from nextech_force.gauge import ForceGauge

    out: Dict[str, Any] = {}

    # --- parsing -----------------------------------------------------------
    out["parse"] = {c.decode("ascii", "replace"): ForceGauge.parse(c)
                    for c in PARSE_CASES}

    # --- horizons ----------------------------------------------------------
    horizons = {
        "fixed_20": create_horizon("fixed", duration_s=20.0),
        "fixed_5": create_horizon("fixed", duration_s=5.0),
        "infinite_20": create_horizon("infinite", window_s=20.0),
        "infinite_5": create_horizon("infinite", window_s=5.0),
        "infinite_all": create_horizon("infinite", window_s=None),
    }
    out["horizon"] = {
        name: {
            "describe": h.describe(),
            "is_bounded": h.is_bounded,
            "xlim": [_round(h.xlim(p)) for p in HORIZON_PROBES],
            "continue": [h.should_continue(p, 0) for p in HORIZON_PROBES],
            "progress": [_round(h.progress(p)) for p in HORIZON_PROBES],
        }
        for name, h in horizons.items()
    }

    # --- analysis over the recorded traces ---------------------------------
    out["traces"] = {}
    for path in TRACES:
        trace = Trace.load_csv(path)
        entry: Dict[str, Any] = {
            "n": len(trace),
            "sha256_force": hashlib.sha256(
                np.asarray(trace.force, dtype="<f8").tobytes()).hexdigest(),
            "sha256_t": hashlib.sha256(
                np.asarray(trace.t, dtype="<f8").tobytes()).hexdigest(),
            "summary": _round(trace.summary()),
            "metadata": dict(trace.metadata),
        }
        report = rate_report(trace)
        entry["rate_report"] = _round({
            "poll_rate_hz": report.poll_rate_hz,
            "median_gap_ms": report.median_gap_ms,
            "n_changes": report.n_changes,
            "change_rate_hz": report.change_rate_hz,
            "min_change_gap_ms": report.min_change_gap_ms,
            "p05_change_gap_ms": report.p05_change_gap_ms,
            "median_run_length": report.median_run_length,
            "distinct_values": report.distinct_values,
        })
        entry["verdict"] = report.verdict()
        lags, ac = autocorrelation(trace, max_lag=20)
        entry["autocorr"] = _round(ac.tolist())
        try:
            entry["band_power"] = _round(band_power(trace))
        except ValueError as exc:
            entry["band_power"] = f"ValueError: {exc}"
        try:
            entry["step_response"] = _round(step_response(trace))
        except ValueError as exc:
            entry["step_response"] = f"ValueError: {exc}"
        out["traces"][path] = entry

    return out


def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """Flatten nested structures to dotted paths for precise diffing."""
    flat: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            flat.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            flat.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        flat[prefix] = obj
    return flat


def compare(golden_path: str) -> int:
    """Compare the current fingerprint against the stored one."""
    with open(golden_path, encoding="utf-8") as handle:
        golden = json.load(handle)
    current = fingerprint()

    g_flat, c_flat = _flatten(golden), _flatten(current)
    missing = sorted(set(g_flat) - set(c_flat))
    added = sorted(set(c_flat) - set(g_flat))
    changed = sorted(k for k in set(g_flat) & set(c_flat) if g_flat[k] != c_flat[k])

    print(f"compared {len(g_flat)} deterministic values")
    for key in missing:
        print(f"  MISSING  {key}: was {g_flat[key]!r}")
    for key in added:
        print(f"  ADDED    {key}: now {c_flat[key]!r}")
    for key in changed:
        print(f"  CHANGED  {key}: {g_flat[key]!r} -> {c_flat[key]!r}")

    if missing or added or changed:
        print(f"\nFAIL: {len(missing)} missing, {len(added)} added, "
              f"{len(changed)} changed")
        return 1
    print("\nPASS: exact parity on every deterministic value")
    return 0


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("capture", "compare"):
        print(__doc__)
        return 2
    action, path = sys.argv[1], sys.argv[2]
    if action == "capture":
        data = fingerprint()
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        print(f"captured {len(_flatten(data))} values -> {path}")
        return 0
    return compare(path)


if __name__ == "__main__":
    sys.exit(main())
