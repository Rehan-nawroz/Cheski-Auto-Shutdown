"""Unit tests for process rows (the picker's data layer)."""

from __future__ import annotations

import os

from cheski.ui.processes import build_rows
from cheski.windows import WindowInfo


def test_memory_text_units():
    from cheski.ui.processes import ProcessRow

    info = WindowInfo(1, "App", pid=10, process="app.exe")
    assert ProcessRow(info, None, "App").memory_text == "—"
    mb = ProcessRow(info, 150 * 1024 * 1024, "App").memory_text
    assert mb.endswith("MB") and mb.split()[0] == "150"
    gb = ProcessRow(info, 2 * 1024**3, "App").memory_text
    assert gb.endswith("GB")


def test_uwp_host_shows_the_window_title_as_display_name():
    rows = build_rows([WindowInfo(5, "Settings", pid=9, process="ApplicationFrameHost.exe")])
    assert rows[0].display_name == "Settings"


def test_search_text_covers_name_process_pid():
    rows = build_rows([WindowInfo(5, "Steam - Downloading", pid=77, process="steam.exe")])
    hay = rows[0].search_text
    assert "steam" in hay and "77" in hay and "downloading" in hay


def test_own_pid_is_excluded_from_rows():
    rows = build_rows(
        [
            WindowInfo(1, "Cheski", pid=os.getpid(), process="python.exe"),
            WindowInfo(2, "Steam", pid=8, process="steam.exe"),
        ],
        own_pid=os.getpid(),
    )
    assert [row.info.handle for row in rows] == [2]


def test_rows_sort_by_display_name():
    rows = build_rows(
        [
            WindowInfo(2, "Zebra", pid=3, process="z.exe"),
            WindowInfo(1, "Apple", pid=2, process="a.exe"),
        ]
    )
    assert [row.display_name for row in rows] == ["Apple", "Zebra"]
