"""Exercise the live-plot code path headlessly against the real gauge.

FuncAnimation needs a GUI event loop, which a terminal session does not have.
The drawing logic itself does not: this drives LivePlot._update directly on the
Agg backend while a real recorder thread fills the buffer, then saves the frames
it produced. It verifies the scrolling/pinned axis behaviour of both horizons
and that acquisition genuinely runs independently of drawing.
"""
from __future__ import annotations

import logging
import time

import matplotlib

matplotlib.use("Agg", force=True)

from nextech_force import ForceGauge, PlotConfig, RecordingConfig, create_horizon
from nextech_force.live import LivePlot
from nextech_force.recorder import Recorder

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def drive(mode: str, out_png: str, seconds: float, **horizon_kwargs) -> None:
    """Run a short recording and hand-crank the live plot's update callback."""
    horizon = create_horizon(mode, **horizon_kwargs)
    with ForceGauge() as gauge:
        recorder = Recorder(gauge, horizon,
                            RecordingConfig(csv_path=None, tare_on_start=True))
        plot = LivePlot(recorder, horizon, PlotConfig(refresh_hz=30))
        fig = plot._build_figure()
        recorder.start()

        frames, xlims = 0, []
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline and recorder.is_running:
            plot._update(frames)
            xlims.append(tuple(round(x, 2) for x in plot._ax.get_xlim()))
            frames += 1
            time.sleep(1 / 30)
        plot._update(frames)
        recorder.stop()

        fig.savefig(out_png, dpi=130, bbox_inches="tight")
        rate = recorder.n_samples / max(recorder.elapsed_s, 1e-9)
        print(f"[{mode}] frames={frames}  samples={recorder.n_samples}  "
              f"rate={rate:.1f} Hz  unparsed={recorder.n_unparsed}")
        print(f"[{mode}] xlim first={xlims[0]} mid={xlims[len(xlims)//2]} "
              f"last={xlims[-1]}")
        print(f"[{mode}] saved {out_png}")
        if recorder.error is not None:
            raise recorder.error


def main() -> None:
    # Fixed: axis must stay pinned at (0, duration) throughout.
    drive("fixed", "data/live_fixed.png", seconds=6.0, duration_s=20.0)
    # Infinite: axis must scroll once elapsed time exceeds the window.
    drive("infinite", "data/live_infinite.png", seconds=10.0, window_s=5.0)


if __name__ == "__main__":
    main()
