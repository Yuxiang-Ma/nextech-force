"""High-rate recording and visualization for Nextech DFS/DFT force gauges.

The vendor library `nexgraphpy` reports ~10 samples/s. That figure is a software
artifact: a hardcoded 100 ms sleep per command, stacked on the FTDI latency timer
default of 16 ms. Setting that timer to 1 ms reaches ~170 Hz on a DFS-X 100N,
with no hardware change and no admin rights.

Importing this package pulls **only the standard library**. The acquisition path
-- gauge, recorder, horizons, CSV streaming -- has no third-party dependencies at
all, so it can be dropped into an existing data-collection pipeline without
dragging numpy or matplotlib along. Analysis needs numpy and plotting needs
matplotlib; both are imported lazily on first use and are declared as extras::

    pip install nextech-force              # driver only
    pip install nextech-force[viz]         # + numpy, matplotlib, CLI plots

Quick start (no third-party deps):
    >>> from nextech_force import ForceGauge
    >>> with ForceGauge() as gauge:
    ...     for sample in gauge.stream(duration_s=1.0):
    ...         print(sample.t_mid, sample.value)

Record and plot (needs the `viz` extra):
    >>> from nextech_force import ForceGauge, create_horizon, record, plot_trace
    >>> with ForceGauge() as gauge:
    ...     trace = record(gauge, create_horizon("fixed", duration_s=20))
    >>> plot_trace(trace, save_path="force.png")

Live view, either horizon:
    >>> from nextech_force import live_record, create_horizon
    >>> live_record(create_horizon("fixed", duration_s=20))       # bounded
    >>> live_record(create_horizon("infinite", window_s=20))      # until stopped
"""
from typing import TYPE_CHECKING, Any

# Standard-library-only core. Anything below this line that needs numpy or
# matplotlib is resolved lazily by __getattr__.
from .backends import (
    BACKEND_REGISTRY,
    Backend,
    BackendError,
    available_backends,
    describe_platform,
    open_backend,
    register_backend,
)
from .config import GaugeConfig, PlotConfig, RecordingConfig
from .gauge import ForceGauge, Sample
from .horizon import (
    HORIZON_REGISTRY,
    FixedHorizon,
    Horizon,
    InfiniteHorizon,
    create_horizon,
    register_horizon,
)
from .recorder import Recorder, record

if TYPE_CHECKING:  # for type checkers only; not imported at runtime
    from .analysis import (
        RateReport,
        autocorrelation,
        band_power,
        rate_report,
        step_response,
    )
    from .live import live_record
    from .plotting import plot_trace
    from .trace import Trace

__version__ = "0.2.0"

# name -> (module, attribute). Imported on first attribute access so a bare
# install never pays for numpy or matplotlib.
_LAZY = {
    "Trace": (".trace", "Trace"),
    "plot_trace": (".plotting", "plot_trace"),
    "live_record": (".live", "live_record"),
    "LivePlot": (".live", "LivePlot"),
    "rate_report": (".analysis", "rate_report"),
    "RateReport": (".analysis", "RateReport"),
    "autocorrelation": (".analysis", "autocorrelation"),
    "band_power": (".analysis", "band_power"),
    "step_response": (".analysis", "step_response"),
    # Kept for backwards compatibility: these moved into backends/.
    "D2xxDevice": (".backends.d2xx", "D2xxDevice"),
    "D2xxError": (".backends.d2xx", "D2xxError"),
    "device_count": (".backends.d2xx", "device_count"),
    "SerialDevice": (".backends.serial_vcp", "SerialDevice"),
}

__all__ = [
    # Device
    "ForceGauge",
    "Sample",
    # Backends
    "Backend",
    "BackendError",
    "open_backend",
    "register_backend",
    "available_backends",
    "describe_platform",
    "BACKEND_REGISTRY",
    "D2xxDevice",
    "D2xxError",
    "SerialDevice",
    "device_count",
    # Configuration
    "GaugeConfig",
    "RecordingConfig",
    "PlotConfig",
    # Horizons
    "Horizon",
    "FixedHorizon",
    "InfiniteHorizon",
    "create_horizon",
    "register_horizon",
    "HORIZON_REGISTRY",
    # Recording
    "Recorder",
    "record",
    "Trace",
    # Visualization
    "plot_trace",
    "live_record",
    "LivePlot",
    # Analysis
    "rate_report",
    "RateReport",
    "autocorrelation",
    "band_power",
    "step_response",
    "__version__",
]

_EXTRA_HINT = {
    ".trace": "numpy", ".analysis": "numpy",
    ".plotting": "matplotlib", ".live": "matplotlib",
}


def __getattr__(name: str) -> Any:
    """Resolve heavy names on first use, with an actionable message if missing."""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    from importlib import import_module
    try:
        module = import_module(module_name, __package__)
    except ImportError as exc:
        hint = _EXTRA_HINT.get(module_name)
        if hint and hint in str(exc):
            raise ImportError(
                f"{name} requires {hint}, which is not installed. "
                f"Install the extras with: pip install nextech-force[viz]"
            ) from exc
        raise
    value = getattr(module, attr)
    globals()[name] = value  # cache so later lookups skip __getattr__
    return value


def __dir__():
    return sorted(__all__)
