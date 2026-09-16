"""Window identity tests.

The liveness check is monkeypatched where it matters: real HWND values are
unpredictable, and tests must not depend on whether ``IsWindow(4242)`` happens
to be true on the developer's desktop.
"""

from __future__ import annotations

import os
import sys

import pytest

from cheski.windows import (
    FakeWindowSource,
    PyGetWindowSource,
    WindowInfo,
    filter_windows,
    is_window_alive,
    process_name_for_window,
    resolve_target,
    window_title,
)


def test_filter_windows_drops_junk_and_duplicates_and_sorts():
    infos = [
        WindowInfo(1, "Steam"),
        WindowInfo(1, "Steam"),  # same handle twice
        WindowInfo(2, ""),  # no title
        WindowInfo(3, "Program Manager"),  # shell furniture
        WindowInfo(4, "  chrome   -  Downloading  "),  # whitespace collapsed
        WindowInfo(0, "no handle"),
    ]

    assert [info.title for info in filter_windows(infos)] == [
        "chrome - Downloading",
        "Steam",
    ]


def test_filter_windows_is_case_insensitive_about_ignored_titles():
    assert filter_windows([WindowInfo(1, "PROGRAM MANAGER")]) == []


def test_label_shows_process_then_pid_then_nothing():
    assert WindowInfo(1, "Steam", pid=1, process="steam.exe").label == "Steam -- steam.exe"
    assert WindowInfo(1, "Steam", pid=42).label == "Steam -- pid 42"
    assert WindowInfo(1, "").label == "(untitled window)"


def test_fake_source_collapses_whitespace_too():
    source = FakeWindowSource([WindowInfo(5, "  Steam   -   Downloading 12%  ")])
    assert source.list_windows()[0].title == "Steam - Downloading 12%"


# -- resolve_target ----------------------------------------------------


def test_resolve_target_re_reads_the_title_of_a_live_handle(monkeypatch, fake_source):
    monkeypatch.setattr("cheski.windows.is_window_alive", lambda handle: True)
    target = fake_source.list_windows()[0]
    fake_source.set_title(4242, "Steam - Downloading 77%")

    resolved = resolve_target(fake_source, target)

    assert resolved is not None
    assert resolved.handle == 4242
    assert resolved.title == "Steam - Downloading 77%"


def test_resolve_target_re_attaches_by_pid(fake_source, windows_never_alive):
    target = WindowInfo(4242, "Steam - old window", pid=1234, process="steam.exe")
    fake_source.windows = [
        WindowInfo(9999, "Steam - new window", pid=1234, process="steam.exe"),
        WindowInfo(8888, "Notepad", pid=555, process="notepad.exe"),
    ]

    resolved = resolve_target(fake_source, target)

    assert resolved is not None
    assert resolved.handle == 9999


def test_resolve_target_re_attaches_by_process_name_without_a_pid(fake_source, windows_never_alive):
    target = WindowInfo(4242, "Steam - old window", pid=None, process="steam.exe")
    fake_source.windows = [WindowInfo(9999, "Steam - new window", pid=1234, process="STEAM.EXE")]

    resolved = resolve_target(fake_source, target)

    assert resolved is not None
    assert resolved.handle == 9999


def test_resolve_target_prefers_the_most_informative_title(fake_source, windows_never_alive):
    target = WindowInfo(4242, "Steam", pid=1234, process="steam.exe")
    fake_source.windows = [
        WindowInfo(1, "Steam", pid=1234, process="steam.exe"),
        WindowInfo(2, "Steam - Downloading 100%", pid=1234, process="steam.exe"),
    ]

    assert resolve_target(fake_source, target).handle == 2


def test_resolve_target_returns_none_when_the_application_is_gone(fake_source, windows_never_alive):
    fake_source.windows = [WindowInfo(8888, "Notepad", pid=555, process="notepad.exe")]
    target = WindowInfo(4242, "Steam", pid=1234, process="steam.exe")

    assert resolve_target(fake_source, target) is None


def test_resolve_target_never_re_attaches_to_our_own_process(fake_source, windows_never_alive):
    """The tool must not end up watching its own window all night."""
    target = WindowInfo(4242, "Steam - old window", pid=None, process="python3.12.exe")
    fake_source.windows = [
        WindowInfo(99, "Cheski Auto Shutdown 1.0.0", pid=os.getpid(), process="python3.12.exe")
    ]

    assert resolve_target(fake_source, target) is None


def test_resolve_target_prefers_pid_over_process_name(fake_source, windows_never_alive):
    target = WindowInfo(4242, "Steam", pid=1234, process="steam.exe")
    fake_source.windows = [
        WindowInfo(11, "Steam - same exe, other instance", pid=4321, process="steam.exe"),
        WindowInfo(22, "Steam - same pid", pid=1234, process="steam.exe"),
    ]

    assert resolve_target(fake_source, target).handle == 22


# -- defensive API wrappers -------------------------------------------


def test_invalid_handles_are_rejected_without_raising():
    assert window_title(0) is None
    assert process_name_for_window(0) == (None, None)
    assert is_window_alive(0) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only window enumeration")
def test_live_enumeration_returns_real_alive_windows():
    """A genuine integration check against this machine's desktop."""
    windows = PyGetWindowSource().list_windows()

    assert isinstance(windows, list)
    for info in windows:
        assert info.handle > 0
        assert info.title.strip()
        assert is_window_alive(info.handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only window enumeration")
def test_live_title_read_matches_the_enumerated_title():
    windows = PyGetWindowSource().list_windows()
    if not windows:
        pytest.skip("no top-level windows to inspect")

    info = windows[0]
    assert window_title(info.handle) == info.title
