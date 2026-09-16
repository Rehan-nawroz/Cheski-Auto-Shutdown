"""Power layer tests.

Nothing here may execute ``shutdown.exe`` -- every test injects a
:class:`~tests.conftest.RecordingRunner`, and the autouse guard in
``tests/conftest.py`` fails the test if the real command is ever reached.
"""

from __future__ import annotations

import pytest

from cheski.power import (
    CommandResult,
    PowerController,
    describe_return_code,
    render_message,
    sanitize_message,
)


def test_shutdown_argv_is_exactly_right(recorder):
    controller = PowerController(seconds=60, runner=recorder, dry_run=False)
    result = controller.schedule_shutdown()

    assert recorder.last == [
        "shutdown",
        "/s",
        "/t",
        "60",
        "/c",
        controller.message,
    ]
    assert result.ok is True
    assert result.returncode == 0
    assert result.dry_run is False


def test_abort_argv_is_exactly_right(recorder):
    result = PowerController(runner=recorder, dry_run=False).abort_shutdown()
    assert recorder.last == ["shutdown", "/a"]
    assert result.ok is True


def test_dry_run_never_executes_the_command():
    def exploding_runner(argv):  # pragma: no cover - must never be called
        raise AssertionError(f"runner was called with {argv}")

    controller = PowerController(seconds=60, dry_run=True, runner=exploding_runner)
    result = controller.schedule_shutdown()

    assert result.dry_run is True
    assert result.ok is True
    assert result.argv[0] == "shutdown"
    assert "Dry run" in result.message


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(1, 15), (0, 15), (-100, 15), (60, 60), (600, 600), (10_000, 600)],
)
def test_countdown_is_clamped(requested, expected):
    assert PowerController(seconds=requested).seconds == expected


def test_describe_matches_the_real_argv(recorder):
    controller = PowerController(seconds=60, runner=recorder)
    assert controller.describe() == f'shutdown /s /t 60 /c "{controller.message}"'


def test_describe_quotes_the_comment():
    controller = PowerController(seconds=30)
    assert controller.describe().startswith("shutdown /s /t 30 /c ")


def test_return_code_text_is_mapped():
    assert "no shutdown in progress" in describe_return_code(1116)
    assert "already scheduled" in describe_return_code(1190)
    assert "Access denied" in describe_return_code(5)
    assert describe_return_code(12345) == ""


def test_failed_command_reports_not_ok(recorder):
    recorder.returncode = 5
    recorder.stderr = "Access is denied.(5)"
    result = PowerController(runner=recorder, dry_run=False).schedule_shutdown()

    assert result.ok is False
    assert "Access denied" in result.message
    assert "Access is denied" in result.message


def test_abort_with_nothing_pending_is_explained(recorder):
    recorder.returncode = 1116
    result = PowerController(runner=recorder, dry_run=False).abort_shutdown()

    assert result.ok is False
    assert "nothing to abort" in result.message.lower()


def test_already_scheduled_is_explained(recorder):
    recorder.returncode = 1190
    result = PowerController(runner=recorder, dry_run=False).schedule_shutdown()

    assert result.ok is False
    assert "already scheduled" in result.message


def test_oserror_is_captured_not_raised():
    def missing_runner(argv):
        raise FileNotFoundError("shutdown.exe is missing")

    result = PowerController(runner=missing_runner, dry_run=False).schedule_shutdown()

    assert result.ok is False
    assert "Could not run" in result.message


def test_environment_variable_forces_dry_run(monkeypatch):
    monkeypatch.setenv("CHESKI_DRY_RUN", "1")
    assert PowerController(seconds=60).dry_run is True


# -- message handling --------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ['Download "done" & restart; now', "line\r\nbreak", "pipe|and<angle>caret^tick`"],
)
def test_unsafe_characters_are_stripped(raw):
    cleaned = sanitize_message(raw)
    for character in ('"', "'", "&", "|", "<", ">", "^", "`", ";", "\r", "\n", "\t"):
        assert character not in cleaned


def test_message_is_length_capped():
    assert len(sanitize_message("x" * 500)) == 200


def test_blank_message_gets_a_default():
    assert sanitize_message("   ") != ""


def test_render_message_tolerates_stray_braces():
    assert "60" in render_message("shutdown in {seconds}s", 60)
    assert render_message("100% {done} {seconds}", 60)


def test_default_message_mentions_the_countdown():
    controller = PowerController(seconds=45)
    assert "45s" in controller.message
    assert sanitize_message(controller.message) == controller.message


def test_command_result_ok_requires_zero_exit():
    assert CommandResult(argv=("shutdown", "/a"), returncode=0).ok is True
    assert CommandResult(argv=("shutdown", "/a"), returncode=1).ok is False
    assert CommandResult(argv=("shutdown", "/a"), dry_run=True).ok is True
    assert CommandResult(argv=("shutdown", "/a"), error="boom").ok is False
