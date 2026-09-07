"""Horizon selection, termination and axis-window behaviour."""
import pytest

from nextech_force import create_horizon
from nextech_force.horizon import (
    HORIZON_REGISTRY,
    FixedHorizon,
    InfiniteHorizon,
    register_horizon,
)


def test_registry_contains_both_modes():
    assert set(HORIZON_REGISTRY) == {"fixed", "infinite"}


def test_fixed_defaults_to_20_seconds():
    horizon = create_horizon("fixed")
    assert horizon.duration_s == 20.0
    assert horizon.expected_duration_s == 20.0
    assert horizon.is_bounded


def test_fixed_stops_at_duration():
    horizon = FixedHorizon(duration_s=5.0)
    assert horizon.should_continue(4.99, 100)
    assert not horizon.should_continue(5.0, 100)
    assert not horizon.should_continue(7.0, 100)


def test_fixed_axis_stays_pinned():
    """The axis must not rescale mid-recording, or the trace appears to move."""
    horizon = FixedHorizon(duration_s=20.0)
    assert horizon.xlim(0.0) == (0.0, 20.0)
    assert horizon.xlim(3.2) == (0.0, 20.0)
    assert horizon.xlim(19.9) == (0.0, 20.0)


def test_fixed_progress():
    horizon = FixedHorizon(duration_s=10.0)
    assert horizon.progress(0.0) == 0.0
    assert horizon.progress(5.0) == pytest.approx(0.5)
    assert horizon.progress(50.0) == 1.0


def test_infinite_never_stops():
    horizon = create_horizon("infinite")
    assert not horizon.is_bounded
    assert horizon.should_continue(1e9, 10**9)
    assert horizon.expected_duration_s is None
    assert horizon.progress(123.0) is None


def test_infinite_window_scrolls_only_after_it_fills():
    horizon = InfiniteHorizon(window_s=5.0)
    assert horizon.xlim(0.0) == (0.0, 5.0)
    assert horizon.xlim(3.0) == (0.0, 5.0)      # not yet full
    assert horizon.xlim(12.0) == (7.0, 12.0)    # scrolling


def test_infinite_full_history_grows():
    horizon = InfiniteHorizon(window_s=None)
    assert horizon.xlim(0.5) == (0.0, 1.0)      # floors at 1 s
    assert horizon.xlim(42.0) == (0.0, 42.0)


@pytest.mark.parametrize("bad", [0, -1.0])
def test_rejects_nonpositive_duration(bad):
    with pytest.raises(ValueError):
        FixedHorizon(duration_s=bad)
    with pytest.raises(ValueError):
        InfiniteHorizon(window_s=bad)


def test_unknown_mode_lists_alternatives():
    with pytest.raises(KeyError, match="fixed"):
        create_horizon("nope")


def test_duplicate_registration_rejected():
    with pytest.raises(ValueError, match="already registered"):
        register_horizon("fixed")(FixedHorizon)
