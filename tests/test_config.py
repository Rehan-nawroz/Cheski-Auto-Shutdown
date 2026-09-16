"""Settings tests: a broken config file must never stop the app starting."""

from __future__ import annotations

import json

import pytest

from cheski.config import (
    DEFAULT_TRIGGERS,
    DELAY_RANGE,
    DWELL_RANGE,
    INTERVAL_RANGE,
    Settings,
    clamp,
    config_dir,
    config_path,
    load_settings,
    log_dir,
    save_settings,
)


def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10


def test_paths_live_under_the_app_folder():
    assert config_dir().name == "CheskiAutoShutdown"
    assert config_path().parent == config_dir()
    assert log_dir().parent == config_dir()


def test_defaults_are_sane():
    settings = Settings()
    assert settings.triggers == DEFAULT_TRIGGERS
    assert settings.match_mode == "any"
    assert settings.require_transition is True
    assert INTERVAL_RANGE[0] <= settings.interval_seconds <= INTERVAL_RANGE[1]
    assert DELAY_RANGE[0] <= settings.shutdown_delay_seconds <= DELAY_RANGE[1]
    assert DWELL_RANGE[0] <= settings.dwell_checks <= DWELL_RANGE[1]


def test_round_trip(tmp_path):
    path = tmp_path / "nested" / "config.json"
    original = Settings(
        triggers=("done", "100%"),
        match_mode="all",
        case_sensitive=True,
        require_transition=False,
        interval_seconds=3.5,
        dwell_checks=4,
        shutdown_delay_seconds=120,
        dry_run=True,
        last_process="steam.exe",
        last_title_hint="Steam - Downloading",
    )

    assert save_settings(original, path) is True
    assert load_settings(path) == original


def test_missing_file_yields_defaults(tmp_path):
    assert load_settings(tmp_path / "absent.json") == Settings()


def test_corrupt_file_yields_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_non_object_json_yields_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"nonsense": True, "interval_seconds": 5}), encoding="utf-8")
    settings = load_settings(path)
    assert settings.interval_seconds == 5
    assert not hasattr(settings, "nonsense")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(-10, INTERVAL_RANGE[0]), (0, INTERVAL_RANGE[0]), (9999, INTERVAL_RANGE[1]), ("junk", 2.0)],
)
def test_interval_is_clamped(raw, expected):
    assert Settings.from_dict({"interval_seconds": raw}).interval_seconds == expected


@pytest.mark.parametrize("raw", [1, 0, -5, "x", None])
def test_dwell_has_a_floor(raw):
    assert Settings.from_dict({"dwell_checks": raw}).dwell_checks >= DWELL_RANGE[0]


@pytest.mark.parametrize("raw", [1, 0, 999999])
def test_delay_is_clamped(raw):
    value = Settings.from_dict({"shutdown_delay_seconds": raw}).shutdown_delay_seconds
    assert DELAY_RANGE[0] <= value <= DELAY_RANGE[1]


def test_triggers_from_a_string():
    assert Settings.from_dict({"triggers": "100%, complete"}).triggers == ("100%", "complete")


def test_triggers_from_a_list_with_junk():
    assert Settings.from_dict({"triggers": ["100%", "", "  ", "complete"]}).triggers == (
        "100%",
        "complete",
    )


def test_blank_trigger_list_keeps_the_default():
    assert Settings.from_dict({"triggers": []}).triggers == DEFAULT_TRIGGERS


def test_match_mode_is_validated():
    assert Settings.from_dict({"match_mode": "ALL"}).match_mode == "all"
    assert Settings.from_dict({"match_mode": "nonsense"}).match_mode == "any"


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("no", False), (1, True), (0, False)])
def test_booleans_from_loose_types(raw, expected):
    assert Settings.from_dict({"dry_run": raw}).dry_run is expected


def test_nan_interval_falls_back():
    assert Settings.from_dict({"interval_seconds": float("nan")}).interval_seconds == 2.0


def test_save_leaves_no_temp_file(tmp_path):
    path = tmp_path / "config.json"
    save_settings(Settings(), path)
    assert path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_reports_failure_instead_of_raising(tmp_path):
    # A directory where the file should be: writing must fail, not crash.
    blocked = tmp_path / "config.json"
    blocked.mkdir()
    assert save_settings(Settings(), blocked) is False
