"""A recorded force trace: timestamps, values, and CSV round-tripping."""
from __future__ import annotations

import csv
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

CSV_HEADER = ["t_s", "force_N", "latency_ms"]


@dataclass
class Trace:
    """Force samples on a common time base starting at zero.

    Attributes:
        t: Sample times in seconds, relative to the first sample.
        force: Signed force in newtons. Compression is negative.
        latency_ms: Per-sample round-trip time, for diagnostics.
        metadata: Free-form provenance (device info, horizon, units).
    """

    t: np.ndarray
    force: np.ndarray
    latency_ms: Optional[np.ndarray] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.t = np.asarray(self.t, dtype=float)
        self.force = np.asarray(self.force, dtype=float)
        if self.t.shape != self.force.shape:
            raise ValueError(
                f"t and force must match: {self.t.shape} vs {self.force.shape}")
        if self.latency_ms is not None:
            self.latency_ms = np.asarray(self.latency_ms, dtype=float)

    def __len__(self) -> int:
        return int(self.t.size)

    @property
    def duration_s(self) -> float:
        """Span from first to last sample."""
        return float(self.t[-1] - self.t[0]) if len(self) > 1 else 0.0

    @property
    def rate_hz(self) -> float:
        """Mean achieved sample rate."""
        return len(self) / self.duration_s if self.duration_s > 0 else 0.0

    def summary(self) -> Dict[str, float]:
        """Headline statistics for reporting."""
        if not len(self):
            return {}
        stats = {
            "samples": float(len(self)),
            "duration_s": self.duration_s,
            "rate_hz": self.rate_hz,
            "min_N": float(np.min(self.force)),
            "max_N": float(np.max(self.force)),
            "mean_N": float(np.mean(self.force)),
            "sd_N": float(np.std(self.force)),
            "peak_abs_N": float(np.max(np.abs(self.force))),
        }
        if self.latency_ms is not None and self.latency_ms.size:
            stats["latency_median_ms"] = float(np.median(self.latency_ms))
            stats["latency_p95_ms"] = float(np.percentile(self.latency_ms, 95))
        return stats

    def slice_window(self, t_start: float, t_end: float) -> Trace:
        """Return the portion of the trace inside a time window."""
        mask = (self.t >= t_start) & (self.t <= t_end)
        return Trace(
            t=self.t[mask],
            force=self.force[mask],
            latency_ms=None if self.latency_ms is None else self.latency_ms[mask],
            metadata=dict(self.metadata),
        )

    def save_csv(self, path: str) -> None:
        """Write the trace to CSV, with metadata as leading comment lines."""
        with open(path, "w", newline="", encoding="utf-8") as handle:
            for key, value in self.metadata.items():
                handle.write(f"# {key}: {value}\n")
            writer = csv.writer(handle)
            writer.writerow(CSV_HEADER)
            lat = (self.latency_ms if self.latency_ms is not None
                   else np.full(len(self), np.nan))
            for ts, force, latency in zip(self.t, self.force, lat):
                writer.writerow([f"{ts:.6f}", f"{force:.4f}",
                                 "" if np.isnan(latency) else f"{latency:.3f}"])
        logger.info("Wrote %d samples to %s", len(self), path)

    @classmethod
    def load_csv(cls, path: str) -> Trace:
        """Read a trace previously written by :meth:`save_csv`.

        Raises:
            ValueError: If the file contains no usable rows.
        """
        metadata: Dict[str, str] = {}
        rows = []
        with open(path, newline="", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("#"):
                    if ":" in line:
                        key, _, value = line[1:].partition(":")
                        metadata[key.strip()] = value.strip()
                    continue
                rows.append(line)
        if not rows:
            raise ValueError(f"No data rows in {path}")
        reader = csv.DictReader(rows)
        t, force, latency = [], [], []
        for row in reader:
            try:
                t.append(float(row["t_s"]))
                force.append(float(row["force_N"]))
            except (KeyError, TypeError, ValueError):
                continue
            raw_latency = (row.get("latency_ms") or "").strip()
            latency.append(float(raw_latency) if raw_latency else np.nan)
        if not t:
            raise ValueError(f"No parseable samples in {path}")
        return cls(
            t=np.array(t),
            force=np.array(force),
            latency_ms=np.array(latency) if np.any(~np.isnan(latency)) else None,
            metadata=metadata,
        )

    @classmethod
    def from_samples(cls, samples: Sequence, metadata: Optional[Dict] = None) -> Trace:
        """Build a trace from :class:`~nextech_force.gauge.Sample` objects.

        Samples that failed to parse are dropped.
        """
        good = [s for s in samples if s.value is not None]
        if not good:
            return cls(t=np.array([]), force=np.array([]), metadata=metadata or {})
        t0 = good[0].t_mid
        return cls(
            t=np.array([s.t_mid - t0 for s in good]),
            force=np.array([s.value for s in good]),
            latency_ms=np.array([s.latency_ms for s in good]),
            metadata=metadata or {},
        )
