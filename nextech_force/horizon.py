"""Recording horizons: how long to record and what time window to display.

Two modes are registered:

* ``fixed``    -- record for a bounded duration (default 20 s). The x-axis is
  pinned to the full span from the start, so the trace fills in left to right
  and the scale never shifts under you.
* ``infinite`` -- record until interrupted. The x-axis scrolls to follow the
  most recent ``window_s`` seconds, or shows everything when ``window_s`` is
  None.

Both are selected by name through :func:`create_horizon`, so a CLI flag or a
config string maps straight onto behaviour without branching at the call site.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple, Type

logger = logging.getLogger(__name__)

HORIZON_REGISTRY: Dict[str, Type[Horizon]] = {}

DEFAULT_FIXED_DURATION_S = 20.0
DEFAULT_INFINITE_WINDOW_S = 20.0


def register_horizon(name: str):
    """Register a Horizon subclass under a lookup name."""

    def decorator(cls: Type[Horizon]) -> Type[Horizon]:
        if name in HORIZON_REGISTRY:
            raise ValueError(f"Horizon {name!r} is already registered")
        HORIZON_REGISTRY[name] = cls
        cls.name = name
        return cls

    return decorator


class Horizon(ABC):
    """Decides when recording stops and which time window the plot shows."""

    name: str = "base"

    @abstractmethod
    def should_continue(self, elapsed_s: float, n_samples: int) -> bool:
        """Whether acquisition should keep running.

        Args:
            elapsed_s: Seconds since the first sample.
            n_samples: Samples collected so far.
        """

    @abstractmethod
    def xlim(self, latest_t: float) -> Tuple[float, float]:
        """X-axis limits given the newest sample timestamp, in seconds."""

    @property
    @abstractmethod
    def is_bounded(self) -> bool:
        """True if recording terminates on its own."""

    @property
    def expected_duration_s(self) -> Optional[float]:
        """Total run length if known, else None."""
        return None

    def progress(self, elapsed_s: float) -> Optional[float]:
        """Fraction complete in 0..1, or None when unbounded."""
        total = self.expected_duration_s
        if not total:
            return None
        return min(1.0, elapsed_s / total)

    def describe(self) -> str:
        """One-line human summary for titles and logs."""
        return self.name


@register_horizon("fixed")
class FixedHorizon(Horizon):
    """Record for a fixed duration, then stop.

    Args:
        duration_s: How long to record. Defaults to 20 s.
    """

    def __init__(self, duration_s: float = DEFAULT_FIXED_DURATION_S) -> None:
        if duration_s <= 0:
            raise ValueError(f"duration_s must be positive, got {duration_s}")
        self.duration_s = float(duration_s)

    def should_continue(self, elapsed_s: float, n_samples: int) -> bool:
        return elapsed_s < self.duration_s

    def xlim(self, latest_t: float) -> Tuple[float, float]:
        # Pinned to the whole window so the axis never rescales mid-recording.
        return 0.0, self.duration_s

    @property
    def is_bounded(self) -> bool:
        return True

    @property
    def expected_duration_s(self) -> Optional[float]:
        return self.duration_s

    def describe(self) -> str:
        return f"fixed horizon, {self.duration_s:g} s"


@register_horizon("infinite")
class InfiniteHorizon(Horizon):
    """Record until stopped, showing a scrolling window.

    Args:
        window_s: Width of the visible window. None shows the whole recording,
            which keeps rescaling as it grows.
    """

    def __init__(self, window_s: Optional[float] = DEFAULT_INFINITE_WINDOW_S) -> None:
        if window_s is not None and window_s <= 0:
            raise ValueError(f"window_s must be positive or None, got {window_s}")
        self.window_s = float(window_s) if window_s is not None else None

    def should_continue(self, elapsed_s: float, n_samples: int) -> bool:
        return True

    def xlim(self, latest_t: float) -> Tuple[float, float]:
        if self.window_s is None:
            return 0.0, max(1.0, latest_t)
        if latest_t <= self.window_s:
            return 0.0, self.window_s
        return latest_t - self.window_s, latest_t

    @property
    def is_bounded(self) -> bool:
        return False

    def describe(self) -> str:
        if self.window_s is None:
            return "infinite horizon, full history"
        return f"infinite horizon, {self.window_s:g} s window"


def create_horizon(mode: str, **kwargs) -> Horizon:
    """Build a horizon by registered name.

    Args:
        mode: "fixed" or "infinite".
        **kwargs: Forwarded to the horizon constructor.

    Returns:
        The constructed Horizon.

    Raises:
        KeyError: If the mode is not registered.
    """
    try:
        cls = HORIZON_REGISTRY[mode]
    except KeyError:
        raise KeyError(
            f"Unknown horizon {mode!r}; available: {sorted(HORIZON_REGISTRY)}"
        ) from None
    horizon = cls(**kwargs)
    logger.debug("Created %s", horizon.describe())
    return horizon
