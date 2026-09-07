"""Command-line interface: ``nextech-force <command>``."""
from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from .analysis import band_power, rate_report, step_response
from .config import GaugeConfig, PlotConfig, RecordingConfig
from .gauge import ForceGauge
from .horizon import DEFAULT_FIXED_DURATION_S, DEFAULT_INFINITE_WINDOW_S, create_horizon
from .recorder import record

logger = logging.getLogger(__name__)


def _build_horizon(args: argparse.Namespace):
    """Construct the horizon selected by the CLI flags."""
    if args.mode == "fixed":
        return create_horizon("fixed", duration_s=args.duration)
    window = None if args.window is not None and args.window <= 0 else args.window
    return create_horizon("infinite", window_s=window)


def _add_horizon_args(parser: argparse.ArgumentParser) -> None:
    """Attach the shared horizon-selection flags."""
    parser.add_argument("--mode", choices=("fixed", "infinite"), default="fixed",
                        help="fixed: stop after --duration. "
                             "infinite: run until interrupted (default: fixed)")
    parser.add_argument("--duration", type=float, default=DEFAULT_FIXED_DURATION_S,
                        help=f"fixed-mode length in seconds "
                             f"(default: {DEFAULT_FIXED_DURATION_S:g})")
    parser.add_argument("--window", type=float, default=DEFAULT_INFINITE_WINDOW_S,
                        help=f"infinite-mode visible window in seconds; "
                             f"0 shows all history "
                             f"(default: {DEFAULT_INFINITE_WINDOW_S:g})")


def _gauge_config(args: argparse.Namespace) -> GaugeConfig:
    """Build a GaugeConfig from common CLI flags."""
    return GaugeConfig(backend=args.backend, port=args.port,
                       latency_ms=args.latency, output_format=args.format)


def _require_viz(what: str):
    """Import the plotting entry point, or explain which extra is missing."""
    try:
        from .plotting import plot_trace
        return plot_trace
    except ImportError as exc:
        raise SystemExit(
            f"{what} needs numpy and matplotlib, which are not installed.\n"
            f"  pip install 'nextech-force[viz]'\n({exc})") from exc


def _require_trace(what: str):
    """Import Trace, or explain which extra is missing."""
    try:
        from .trace import Trace
        return Trace
    except ImportError as exc:
        raise SystemExit(
            f"{what} needs numpy, which is not installed.\n"
            f"  pip install 'nextech-force[analysis]'\n({exc})") from exc


def cmd_info(args: argparse.Namespace) -> int:
    """Print gauge identification and a short rate check."""
    from .backends import describe_platform
    print(f"platform: {describe_platform()}")
    with ForceGauge(_gauge_config(args)) as gauge:
        print(f"backend: {gauge.backend_name}")
        print(f"device : {gauge.device_info}")
        print(f"model  : {gauge.model}")
        samples = list(gauge.stream(max_samples=200))
        span = samples[-1].t_recv - samples[0].t_send
        good = [s for s in samples if s.value is not None]
        lat = sorted(s.latency_ms for s in samples)
        print(f"rate   : {len(samples)/span:.1f} Hz")
        print(f"latency: median {lat[len(lat)//2]:.2f} ms, "
              f"p95 {lat[int(0.95*len(lat))]:.2f} ms")
        print(f"parsed : {len(good)}/{len(samples)}")
        print(f"current: {good[-1].value:+.2f} N" if good else "current: n/a")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Record a trace headlessly, then save CSV and PNG."""
    horizon = _build_horizon(args)
    rec_config = RecordingConfig(csv_path=args.csv, tare_on_start=not args.no_tare)
    with ForceGauge(_gauge_config(args)) as gauge:
        print(f"{gauge.device_info}\nRecording: {horizon.describe()}", flush=True)
        if not horizon.is_bounded:
            print("Press Ctrl-C to stop.", flush=True)
        trace = record(gauge, horizon, rec_config)

    if not len(trace):
        print("No samples captured.", file=sys.stderr)
        return 1
    for key, value in trace.summary().items():
        print(f"  {key:20s} {value:12.3f}")
    if args.csv:
        print(f"csv  -> {args.csv}")
    if args.plot:
        from .config import PlotConfig
        _require_viz("--plot")(trace, PlotConfig(), horizon=horizon,
                               save_path=args.plot)
        print(f"plot -> {args.plot}")
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    """Record with a live animated plot."""
    from .live import live_record

    horizon = _build_horizon(args)
    rec_config = RecordingConfig(csv_path=args.csv, tare_on_start=not args.no_tare)
    plot_config = PlotConfig(refresh_hz=args.fps)
    with ForceGauge(_gauge_config(args)) as gauge:
        print(f"{gauge.device_info}\nLive: {horizon.describe()}", flush=True)
        trace = live_record(horizon, gauge=gauge, plot_config=plot_config,
                            recording_config=rec_config)
    if len(trace):
        for key, value in trace.summary().items():
            print(f"  {key:20s} {value:12.3f}")
        if args.plot:
            _require_viz("--plot")(trace, PlotConfig(), horizon=horizon,
                                   save_path=args.plot)
            print(f"plot -> {args.plot}")
    return 0


def cmd_plot(args: argparse.Namespace) -> int:
    """Render a previously recorded CSV."""
    from .config import PlotConfig
    plot_trace = _require_viz("plot")
    trace = _require_trace("plot").load_csv(args.csv)
    out = args.out or args.csv.rsplit(".", 1)[0] + ".png"
    plot_trace(trace, PlotConfig(), save_path=out, show=args.show)
    print(f"plot -> {out}  ({len(trace)} samples, {trace.rate_hz:.1f} Hz)")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Report rate, freshness and (optionally) step-response bandwidth."""
    trace = _require_trace("analyze").load_csv(args.csv)
    report = rate_report(trace)
    print(f"samples          : {report.n_samples}")
    print(f"poll rate        : {report.poll_rate_hz:.1f} Hz "
          f"(median gap {report.median_gap_ms:.2f} ms)")
    print(f"value changes    : {report.n_changes} "
          f"({report.change_rate_hz:.1f}/s), {report.distinct_values} distinct")
    print(f"min change gap   : {report.min_change_gap_ms:.1f} ms")
    print(f"repeat run length: {report.median_run_length:.1f} polls (median)")
    print(f"\nverdict: {report.verdict()}")

    try:
        print("\nband power relative to 0.5-2 Hz:")
        for band, db in band_power(trace).items():
            if band.endswith("mN"):
                print(f"  white floor      {db:8.2f} mN rms")
            else:
                print(f"  {band:16s} {db:+8.1f} dB")
    except ValueError as exc:
        logger.debug("Spectrum skipped: %s", exc)

    try:
        step = step_response(trace)
        print("\nstep response:")
        for key, value in step.items():
            print(f"  {key:22s} {value:10.2f}")
    except ValueError as exc:
        print(f"\nstep response: {exc}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="nextech-force",
        description="High-rate recording and visualization for Nextech force gauges.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="enable debug logging")
    parser.add_argument("--backend", default="auto",
                        help="transport: auto (default), d2xx, or serial. "
                             "Linux normally uses serial, Windows d2xx")
    parser.add_argument("--port", default=None,
                        help="explicit serial port (COM4, /dev/ttyUSB0); "
                             "auto-detected by FTDI vendor id when omitted")
    parser.add_argument("--latency", type=int, default=1, metavar="MS",
                        help="FTDI latency timer in ms; 16 is the driver default "
                             "and caps polling near 60 Hz (default: 1)")
    parser.add_argument("--format", choices=("mini", "short", "long"),
                        default="mini",
                        help="gauge output format; mini is fastest (default: mini)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="identify the gauge and check its rate")
    p_info.set_defaults(func=cmd_info)

    p_rec = sub.add_parser("record", help="record a trace headlessly")
    _add_horizon_args(p_rec)
    p_rec.add_argument("--csv", default="force.csv", help="output CSV path")
    p_rec.add_argument("--plot", default="force.png",
                       help="output PNG path; empty string to skip")
    p_rec.add_argument("--no-tare", action="store_true",
                       help="skip the zero command before recording")
    p_rec.set_defaults(func=cmd_record)

    p_live = sub.add_parser("live", help="record with a live plot")
    _add_horizon_args(p_live)
    p_live.add_argument("--csv", default=None, help="also stream to this CSV")
    p_live.add_argument("--plot", default=None, help="save a PNG when finished")
    p_live.add_argument("--fps", type=float, default=30.0, help="redraw rate")
    p_live.add_argument("--no-tare", action="store_true",
                        help="skip the zero command before recording")
    p_live.set_defaults(func=cmd_live)

    p_plot = sub.add_parser("plot", help="render a recorded CSV")
    p_plot.add_argument("csv", help="input CSV")
    p_plot.add_argument("-o", "--out", default=None, help="output PNG")
    p_plot.add_argument("--show", action="store_true", help="open a window")
    p_plot.set_defaults(func=cmd_plot)

    p_an = sub.add_parser("analyze", help="rate, freshness and bandwidth report")
    p_an.add_argument("csv", help="input CSV")
    p_an.set_defaults(func=cmd_analyze)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
