"""Live force plot driven by a background recorder.

Drawing and acquisition are deliberately decoupled: the recorder thread polls
the gauge at its full rate while the figure redraws at ``PlotConfig.refresh_hz``
and simply reads whatever snapshot is current. The horizon decides both when
recording ends and which slice of time the axis shows, so ``fixed`` and
``infinite`` differ only in the objects passed in, not in the drawing code.
"""
from __future__ import annotations

import bisect
import logging
from typing import List, Optional

import numpy as np

from .config import PlotConfig, RecordingConfig
from .gauge import ForceGauge
from .horizon import Horizon
from .plotting import COLOR_FILL, COLOR_PEAK, COLOR_TRACE, COLOR_ZERO, _pad_ylim
from .recorder import Recorder
from .trace import Trace

logger = logging.getLogger(__name__)


class LivePlot:
    """Animated view of an in-progress recording."""

    def __init__(self, recorder: Recorder, horizon: Horizon,
                 config: Optional[PlotConfig] = None) -> None:
        self.recorder = recorder
        self.horizon = horizon
        self.config = config or PlotConfig()
        self._fig = None
        self._anim = None

    def _build_figure(self):
        """Create the figure and the artists the animation mutates."""
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(11, 5.5))
        self._fig = fig
        self._ax = ax

        self._line, = ax.plot([], [], color=COLOR_TRACE,
                              linewidth=self.config.line_width)
        self._fill = None
        self._marker, = ax.plot([], [], "o", color=COLOR_PEAK, markersize=5,
                                zorder=5)
        ax.axhline(0, color=COLOR_ZERO, linewidth=0.8, linestyle="--", zorder=1)

        ax.set_xlabel("time (s)")
        ax.set_ylabel(f"force ({self.config.units})")
        ax.set_xlim(*self.horizon.xlim(0.0))
        ax.set_ylim(self.config.ylim or (-1.0, 1.0))
        if self.config.grid:
            ax.grid(alpha=0.25, linewidth=0.6)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

        title = self.config.title
        if title is None:
            device = self.recorder.gauge.model or "force gauge"
            title = f"{device}  --  {self.horizon.describe()}"
        ax.set_title(title, fontsize=11, loc="left")

        self._readout = ax.text(
            0.995, 0.97, "", transform=ax.transAxes, ha="right", va="top",
            fontsize=9.5, family="monospace",
            bbox={"boxstyle": "round,pad=0.4", "facecolor": "white",
                  "edgecolor": "#cccccc", "alpha": 0.85})

        fig.canvas.mpl_connect("close_event", lambda _evt: self.recorder.stop())
        fig.tight_layout()
        return fig

    def _readout_text(self, v: List[float]) -> str:
        """Live numeric readout shown in the corner."""
        if not v:
            return "waiting for samples..."
        rate = self.recorder.n_samples / max(self.recorder.elapsed_s, 1e-9)
        lines = [
            f"{v[-1]:+8.2f} {self.config.units}",
            f"peak {max(abs(x) for x in v):6.2f} {self.config.units}",
            f"{rate:6.1f} Hz   n={self.recorder.n_samples}",
        ]
        progress = self.horizon.progress(self.recorder.elapsed_s)
        if progress is not None:
            filled = int(round(progress * 20))
            lines.append(f"[{'#' * filled}{'.' * (20 - filled)}] {progress*100:3.0f}%")
        else:
            lines.append(f"t = {self.recorder.elapsed_s:6.1f} s  (Ctrl-C / close)")
        return "\n".join(lines)

    def _update(self, _frame):
        """Animation callback: pull a snapshot and redraw."""
        t, v = self.recorder.snapshot()

        if t:
            lo, hi = self.horizon.xlim(t[-1])
            # Timestamps are monotonic, so the visible window is a contiguous
            # slice -- bisect finds it without scanning or converting the rest.
            i0 = bisect.bisect_left(t, lo)
            i1 = bisect.bisect_right(t, hi)
            t_vis = np.asarray(t[i0:i1], dtype=float)
            v_vis = np.asarray(v[i0:i1], dtype=float)
            self._ax.set_xlim(lo, hi)
        else:
            t_vis = v_vis = np.empty(0)
            self._ax.set_xlim(*self.horizon.xlim(0.0))

        self._line.set_data(t_vis, v_vis)

        if self._fill is not None:
            self._fill.remove()
            self._fill = None
        if t_vis.size:
            self._fill = self._ax.fill_between(t_vis, 0, v_vis, color=COLOR_FILL,
                                               alpha=0.25, linewidth=0)
            peak = int(np.argmax(np.abs(v_vis)))
            self._marker.set_data([t_vis[peak]], [v_vis[peak]])
            if self.config.ylim is None:
                self._ax.set_ylim(*_pad_ylim(v_vis))

        self._readout.set_text(self._readout_text(v))

        if not self.recorder.is_running and self._anim is not None:
            self._anim.event_source.stop()
            self._ax.set_title(self._ax.get_title() + "   [finished]",
                               fontsize=11, loc="left")
        return self._line, self._marker, self._readout

    def run(self, block: bool = True):
        """Show the window and animate until the recording ends.

        Args:
            block: Keep the window open after the recording finishes.

        Returns:
            The Trace captured during the session.
        """
        import matplotlib.pyplot as plt
        from matplotlib.animation import FuncAnimation

        fig = self._build_figure()
        interval_ms = max(10.0, 1000.0 / self.config.refresh_hz)
        # cache_frame_data=False: this is an open-ended live stream, not a
        # finite frame sequence to memoise.
        self._anim = FuncAnimation(fig, self._update, interval=interval_ms,
                                   blit=False, cache_frame_data=False,
                                   save_count=0)
        try:
            plt.show(block=block)
        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self.recorder.stop()
        return self.recorder.trace()


def live_record(horizon: Horizon,
                gauge: Optional[ForceGauge] = None,
                plot_config: Optional[PlotConfig] = None,
                recording_config: Optional[RecordingConfig] = None) -> Trace:
    """Record and watch a force trace live in one call.

    Args:
        horizon: Controls run length and the visible time window.
        gauge: An already-connected gauge. One is opened and closed if omitted.
        plot_config: Appearance and refresh rate.
        recording_config: Tare behaviour, CSV path, buffer size.

    Returns:
        The captured Trace.
    """
    owns_gauge = gauge is None
    gauge = gauge or ForceGauge()
    if owns_gauge and not gauge.connect():
        gauge.close()
        raise RuntimeError("Failed to connect to the gauge")
    try:
        recorder = Recorder(gauge, horizon, recording_config)
        recorder.start()
        return LivePlot(recorder, horizon, plot_config).run()
    finally:
        if owns_gauge:
            gauge.close()
