"""Unit tests for the extracted Tk-free run-state policy in cheski.ui.state."""

from __future__ import annotations

import pytest

from cheski.ui.state import (
    STATES,
    STATE_ABORTED,
    STATE_IDLE,
    STATE_MONITORING,
    STATE_TRIGGERED,
    STATE_WARMING,
    countdown_body,
    is_live,
    is_running,
    status_colour,
    status_text,
)


def test_every_state_has_status_text():
    for state in STATES:
        text = status_text(state)
        assert text and text != state


def test_status_text_falls_back_to_the_raw_state():
    assert status_text("mystery") == "mystery"


@pytest.mark.parametrize(
    ("state", "running", "live"),
    [
        (STATE_IDLE, False, False),
        (STATE_ABORTED, False, False),
        (STATE_WARMING, True, True),
        (STATE_MONITORING, True, True),
        (STATE_TRIGGERED, False, True),
    ],
)
def test_running_and_live_truth_table(state, running, live):
    assert is_running(state) is running
    assert is_live(state) is live


def test_status_colour_has_a_default():
    assert status_colour(STATE_TRIGGERED) != status_colour("mystery")


def test_countdown_body_always_warns_about_force_close():
    for dry_run in (True, False):
        lines = countdown_body(60, dry_run=dry_run)
        assert any("force-closed" in line for line in lines)
        assert any("60 seconds" in line for line in lines)


def test_countdown_body_dry_run_banner_comes_first():
    lines = countdown_body(30, dry_run=True)
    assert lines[0].startswith("DRY RUN")
    assert not countdown_body(30, dry_run=False)[0].startswith("DRY RUN")
