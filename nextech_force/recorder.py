"""Acquisition that runs independently of drawing.

The gauge answers ~170 times a second; a matplotlib figure redraws maybe 30
times a second. Polling from inside a draw callback would therefore throw away
most of the rate advantage. The recorder owns a background thread that polls as
fast as the gauge allows and appends into a bounded deque, so the plot samples a
snapshot whenever it happens to redraw and never gates acquisition.
"""
from __future__ import annotations

import csv
import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, List, Optional, Tuple

from .config import RecordingConfig
from .gauge import ForceGauge
from .horizon import Horizon

if TYPE_CHECKING:
    from .trace import Trace

logger = logging.getLogger(__name__)

# Duplicated from trace.py rather than imported: this module must stay
# importable without numpy, and trace.py needs it.
CSV_HEADER = ["t_s", "force_N", "latency_ms"]


class Recorder:
    """Polls a gauge on a background thread under the control of a horizon.

    Example:
        >>> with ForceGauge() as gauge:
        ...     rec = Recorder(gauge, create_horizon("fixed", duration_s=5))
        ...     rec.start()
        ...     rec.wait()
        ...     trace = rec.trace()
    """

    def __init__(self, gauge: ForceGauge, horizon: Horizon,
                 config: Optional[RecordingConfig] = None) -> None:
        self.gauge = gauge
        self.horizon = horizon
        self.config = config or RecordingConfig()

        self._buf: deque = deque(maxlen=self.config.buffer_samples)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._done = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0: Optional[float] = None
        self._n_total = 0
        self._n_dropped = 0
        self._csv_handle = None
        self._csv_writer = None
        self._error: Optional[BaseException] = None

    @property
    def is_running(self) -> bool:
        """Whether the acquisition thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def n_samples(self) -> int:
        """Total samples acquired, including any rolled out of the buffer."""
        return self._n_total

    @property
    def n_unparsed(self) -> int:
        """Replies that could not be parsed into a number."""
        return self._n_dropped

    @property
    def elapsed_s(self) -> float:
        """Seconds since the first sample."""
        return 0.0 if self._t0 is None else time.perf_counter() - self._t0

    @property
    def error(self) -> Optional[BaseException]:
        """Exception that ended the acquisition thread, if any."""
        return self._error

    def start(self) -> None:
        """Begin acquiring. Returns immediately.

        Raises:
            RuntimeError: If a recording is already in progress.
        """
        if self.is_running:
            raise RuntimeError("Recorder is already running")
        if self.config.tare_on_start:
            self.gauge.zero()
            time.sleep(self.config.settle_s)
        if self.config.csv_path:
            self._open_csv()
        self._stop.clear()
        self._done.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="force-acq",
                                        daemon=True)
        self._thread.start()
        logger.info("Recording started (%s)", self.horizon.describe())

    def _open_csv(self) -> None:
        """Open the streaming CSV and write metadata plus header."""
        # Deliberately not a context manager: the handle must stay open for the
        # lifetime of the acquisition thread and is closed in _close_csv().
        self._csv_handle = open(  # noqa: SIM115
            self.config.csv_path, "w", newline="", encoding="utf-8")
        for key, value in self._metadata().items():
            self._csv_handle.write(f"# {key}: {value}\n")
        self._csv_writer = csv.writer(self._csv_handle)
        self._csv_writer.writerow(CSV_HEADER)

    def _metadata(self) -> dict:
        """Provenance recorded alongside the samples."""
        return {
            "device": self.gauge.device_info or "unknown",
            "horizon": self.horizon.describe(),
            "output_format": self.gauge.config.output_format,
            "latency_timer_ms": str(self.gauge.config.latency_ms),
            "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def _run(self) -> None:
        """Acquisition loop. Runs on the background thread."""
        try:
            while not self._stop.is_set():
                sample = self.gauge.read()
                if sample.value is None:
                    self._n_dropped += 1
                    continue
                if self._t0 is None:
                    self._t0 = sample.t_mid
                rel_t = sample.t_mid - self._t0
                with self._lock:
                    self._buf.append((rel_t, sample.value, sample.latency_ms))
                    self._n_total += 1
                if self._csv_writer is not None:
                    self._csv_writer.writerow(
                        [f"{rel_t:.6f}", f"{sample.value:.4f}",
                         f"{sample.latency_ms:.3f}"])
                if not self.horizon.should_continue(rel_t, self._n_total):
                    break
        except BaseException as exc:  # surfaced to the caller via .error
            self._error = exc
            logger.exception("Acquisition thread failed")
        finally:
            self._close_csv()
            self._done.set()
            logger.info("Recording stopped after %d samples in %.2f s",
                        self._n_total, self.elapsed_s)

    def _close_csv(self) -> None:
        """Flush and close the streaming CSV if one is open."""
        if self._csv_handle is not None:
            self._csv_handle.flush()
            self._csv_handle.close()
            self._csv_handle = None
            self._csv_writer = None

    def snapshot(self) -> Tuple[List[float], List[float]]:
        """Copy the current buffer for plotting.

        Plain lists rather than arrays so this module stays importable without
        numpy; callers that need arrays convert the visible slice themselves,
        which is cheaper than converting the whole buffer every frame.

        Returns:
            (t, force) lists, empty if nothing has been acquired yet.
        """
        with self._lock:
            if not self._buf:
                return [], []
            rows = list(self._buf)
        return [r[0] for r in rows], [r[1] for r in rows]

    def stop(self) -> None:
        """Ask the acquisition thread to finish and wait briefly for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the horizon ends the recording.

        Args:
            timeout: Seconds to wait, or None to wait indefinitely.

        Returns:
            True if the recording finished, False on timeout.
        """
        return self._done.wait(timeout)

    def trace(self) -> Trace:
        """Build a Trace from whatever is currently buffered.

        Imported lazily: Trace needs numpy, which the acquisition path does not.

        Raises:
            ImportError: If numpy is not installed.
        """
        from .trace import Trace
        with self._lock:
            rows = list(self._buf)
        if not rows:
            return Trace(t=[], force=[], metadata=self._metadata())
        return Trace(
            t=[r[0] for r in rows],
            force=[r[1] for r in rows],
            latency_ms=[r[2] for r in rows],
            metadata=self._metadata(),
        )

    def __enter__(self) -> Recorder:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def record(gauge: ForceGauge, horizon: Horizon,
           config: Optional[RecordingConfig] = None) -> Trace:
    """Record to completion and return the trace.

    Blocks until the horizon stops the run, so it only terminates on its own
    for a bounded horizon. Ctrl-C ends an unbounded one cleanly.
    """
    recorder = Recorder(gauge, horizon, config)
    recorder.start()
    try:
        while recorder.is_running:
            recorder.wait(timeout=0.2)
    except KeyboardInterrupt:
        logger.info("Interrupted; stopping acquisition")
        recorder.stop()
    if recorder.error is not None:
        raise recorder.error
    return recorder.trace()
