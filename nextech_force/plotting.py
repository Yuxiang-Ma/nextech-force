"""Static rendering of a recorded force trace."""
from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np

from .config import PlotConfig
from .horizon import Horizon
from .trace import Trace

logger = logging.getLogger(__name__)

# Compression reads negative on these gauges, so the palette is chosen to keep
# the sign visually obvious rather than to look decorative.
COLOR_TRACE = "#1f4e79"
COLOR_FILL = "#4a90d9"
COLOR_ZERO = "#999999"
COLOR_PEAK = "#c0392b"


def _pad_ylim(force: np.ndarray, pad_frac: float = 0.12) -> Tuple[float, float]:
    """Y limits with headroom, degrading gracefully for a flat trace."""
    if force.size == 0:
        return -1.0, 1.0
    lo, hi = float(np.min(force)), float(np.max(force))
    span = hi - lo
    if span < 1e-9:
        return lo - 0.5, hi + 0.5
    pad = span * pad_frac
    return lo - pad, hi + pad


def _stats_text(trace: Trace) -> str:
    """Multi-line summary block for the corner of the plot."""
    s = trace.summary()
    if not s:
        return "no samples"
    lines = [
        f"n = {int(s['samples'])}",
        f"rate = {s['rate_hz']:.1f} Hz",
        f"peak = {s['peak_abs_N']:.2f} N",
        f"range = {s['min_N']:.2f} .. {s['max_N']:.2f} N",
    ]
    if "latency_median_ms" in s:
        lines.append(f"latency = {s['latency_median_ms']:.1f} ms")
    return "\n".join(lines)


def plot_trace(trace: Trace, config: Optional[PlotConfig] = None,
               horizon: Optional[Horizon] = None,
               save_path: Optional[str] = None,
               show: bool = False):
    """Render a force trace.

    Args:
        trace: The recorded data.
        config: Appearance settings.
        horizon: If given, its xlim pins the time axis so a fixed-horizon plot
            shows the full requested window even when the run ended early.
        save_path: Write a PNG here. Uses the Agg backend when not showing.
        show: Open an interactive window.

    Returns:
        The matplotlib Figure.
    """
    config = config or PlotConfig()
    import matplotlib
    if not show:
        matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    # height_ratios as a top-level kwarg needs matplotlib >= 3.6; the
    # gridspec_kw form works on 3.4+.
    fig, (ax, ax_lat) = plt.subplots(
        2, 1, figsize=(11, 6), sharex=True, constrained_layout=True,
        gridspec_kw={"height_ratios": [3, 1]})

    if len(trace):
        ax.fill_between(trace.t, 0, trace.force, color=COLOR_FILL, alpha=0.25,
                        linewidth=0)
        ax.plot(trace.t, trace.force, color=COLOR_TRACE,
                linewidth=config.line_width)
        peak_idx = int(np.argmax(np.abs(trace.force)))
        ax.plot(trace.t[peak_idx], trace.force[peak_idx], "o", color=COLOR_PEAK,
                markersize=5, zorder=5)
        ax.annotate(f"{trace.force[peak_idx]:.2f} {config.units}",
                    (trace.t[peak_idx], trace.force[peak_idx]),
                    textcoords="offset points", xytext=(8, 8),
                    color=COLOR_PEAK, fontsize=9, fontweight="bold")

    ax.axhline(0, color=COLOR_ZERO, linewidth=0.8, linestyle="--", zorder=1)
    ax.set_ylabel(f"force ({config.units})")
    ax.set_ylim(config.ylim or _pad_ylim(trace.force))
    if config.grid:
        ax.grid(alpha=0.25, linewidth=0.6)

    title = config.title
    if title is None:
        device = trace.metadata.get("device", "force gauge")
        mode = trace.metadata.get("horizon", "")
        title = f"{device}" + (f"  --  {mode}" if mode else "")
    ax.set_title(title, fontsize=11, loc="left")

    if config.show_stats:
        ax.text(0.995, 0.03, _stats_text(trace), transform=ax.transAxes,
                ha="right", va="bottom", fontsize=8.5, family="monospace",
                bbox={"boxstyle": "round,pad=0.4", "facecolor": "white",
                      "edgecolor": "#cccccc", "alpha": 0.85})

    if trace.latency_ms is not None and len(trace):
        ax_lat.plot(trace.t, trace.latency_ms, color="#7f8c8d", linewidth=0.7)
        ax_lat.axhline(float(np.median(trace.latency_ms)), color=COLOR_PEAK,
                       linewidth=0.8, linestyle=":",
                       label=f"median {np.median(trace.latency_ms):.1f} ms")
        ax_lat.legend(fontsize=8, loc="upper right", framealpha=0.85)
    ax_lat.set_ylabel("poll (ms)")
    ax_lat.set_xlabel("time (s)")
    if config.grid:
        ax_lat.grid(alpha=0.25, linewidth=0.6)

    if horizon is not None and len(trace):
        ax.set_xlim(*horizon.xlim(float(trace.t[-1])))
    elif len(trace):
        ax.set_xlim(float(trace.t[0]), float(trace.t[-1]))

    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
        ax_lat.spines[spine].set_visible(False)

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        logger.info("Saved plot to %s", save_path)
    if show:
        plt.show()
    return fig
