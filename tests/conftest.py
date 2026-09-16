"""Shared test fixtures.

The most important thing in this file is :func:`no_real_shutdown`: an autouse
guard that fails the test if anything tries to launch the real ``shutdown``
command.  Cheski's whole job is destroying a session; the suite must never do
that by accident.
"""

from __future__ import annotations

import subprocess

import pytest

from cheski.triggers import TriggerConfig, TriggerEngine
from cheski.windows import FakeWindowSource, WindowInfo


class RecordingRunner:
    """Stands in for ``subprocess.run``: records argv, replays a canned result."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, argv: list[str]) -> "subprocess.CompletedProcess[str]":
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), self.returncode, self.stdout, self.stderr)

    @property
    def last(self) -> list[str] | None:
        return self.calls[-1] if self.calls else None


@pytest.fixture(autouse=True)
def no_real_shutdown(monkeypatch):
    """Fail the test if the real shutdown.exe is ever invoked."""
    real_run = subprocess.run

    def guarded(argv, *args, **kwargs):
        target = argv[0] if isinstance(argv, (list, tuple)) and argv else argv
        if "shutdown" in str(target).casefold():
            raise AssertionError(
                "A test tried to run the real shutdown command: "
                f"{argv!r}. Inject a RecordingRunner instead."
            )
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)


@pytest.fixture(autouse=True)
def no_ambient_dry_run(monkeypatch):
    """A stray CHESKI_DRY_RUN in the developer's shell must not alter tests."""
    monkeypatch.delenv("CHESKI_DRY_RUN", raising=False)


@pytest.fixture
def recorder() -> RecordingRunner:
    return RecordingRunner()


@pytest.fixture
def fake_source() -> FakeWindowSource:
    """One fake downloader window, mid-download."""
    return FakeWindowSource(
        [WindowInfo(handle=4242, title="Steam - Downloading 12%", pid=1234, process="steam.exe")]
    )


@pytest.fixture
def fake_engine() -> TriggerEngine:
    """An engine that fires on the first match, with the safety guard on."""
    engine = TriggerEngine(
        TriggerConfig(triggers=("100%",), dwell_checks=1, require_transition=True)
    )
    engine.reset()
    return engine


@pytest.fixture
def windows_never_alive(monkeypatch):
    """Force the window-liveness check to report 'gone' deterministically.

    Real handles are unpredictable numbers, so tests that exercise re-attach
    must not depend on whether ``IsWindow(1)`` happens to be true.
    """
    monkeypatch.setattr("cheski.windows.is_window_alive", lambda handle: False)
