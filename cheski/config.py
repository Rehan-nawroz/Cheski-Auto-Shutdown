"""Persistent settings for Cheski Auto Shutdown.

Settings live in ``%APPDATA%\\CheskiAutoShutdown\\config.json``.  Loading is
deliberately forgiving: a corrupt, truncated or hand-edited file must never
stop the app from starting, it just falls back to defaults.

``config_dir()`` is also where the rotating log file lives.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .triggers import parse_triggers

log = logging.getLogger("cheski.config")

APP_NAME = "CheskiAutoShutdown"

#: Words downloaders put in their title bars when a download completes.
#: Matching is a case-insensitive substring test, so "complete" already covers
#: "completed"/"Download complete", and "100%" covers numeric progress.
SUGGESTED_TRIGGERS: tuple[str, ...] = (
    "100%",        # numeric progress (Steam, browsers, torrents)
    "complete",    # IDM says "Download complete"; also matches "completed"
    "finished",
    "done",
    "downloaded",
    "seeding",     # torrents that reach 100% and start seeding
)

#: Fresh installs start with every suggested word armed; the GUI also offers
#: them as one-click additions for installs saved with an older, narrower set.
DEFAULT_TRIGGERS: tuple[str, ...] = SUGGESTED_TRIGGERS

#: Allowed ranges, shared with the GUI spinboxes and the power layer so a
#: hand-edited config file can never ask for something absurd.
INTERVAL_RANGE: tuple[float, float] = (0.5, 60.0)
DWELL_RANGE: tuple[int, int] = (1, 20)
DELAY_RANGE: tuple[int, int] = (15, 600)


def _base_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    # Fallback for stripped-down environments / tests without APPDATA.
    return Path.home() / f".{APP_NAME.lower()}"


def config_dir() -> Path:
    """Directory holding ``config.json`` and ``logs/``."""
    return _base_dir()


def config_path() -> Path:
    return _base_dir() / "config.json"


def log_dir() -> Path:
    return _base_dir() / "logs"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _as_float(raw: dict[str, Any], key: str, default: float, low: float, high: float) -> float:
    try:
        value = float(raw[key])
    except (KeyError, TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return float(clamp(value, low, high))


def _as_int(raw: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(float(raw[key]))
    except (KeyError, TypeError, ValueError):
        return default
    return int(clamp(value, low, high))


def _as_bool(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_optional_str(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


@dataclass
class Settings:
    """Every user-facing knob, in one serialisable place."""

    triggers: tuple[str, ...] = DEFAULT_TRIGGERS
    match_mode: str = "any"  # "any" | "all"
    case_sensitive: bool = False
    #: Safety: the trigger must be seen *absent* at least once after Start
    #: before a match is allowed to fire.  Stops an instant shutdown when the
    #: window already reads "100%" (e.g. Steam verifying) as you click Start.
    require_transition: bool = True
    #: Watch every window of the target's process, not just one.  Catches
    #: downloaders whose completion popup is a separate window (e.g. IDM: the
    #: main title never changes, but "Download complete" pops up as its own
    #: window belonging to the same process).
    watch_process_windows: bool = True
    interval_seconds: float = 2.0
    dwell_checks: int = 3
    shutdown_delay_seconds: int = 60
    dry_run: bool = False
    #: Remembered target, by process name (handles are not stable across runs).
    last_process: str | None = None
    last_title_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["triggers"] = list(self.triggers)
        return data

    @classmethod
    def from_dict(cls, raw: Any) -> "Settings":
        settings = cls()
        if not isinstance(raw, dict):
            return settings

        triggers = raw.get("triggers")
        if isinstance(triggers, (str, list, tuple)):
            parsed = parse_triggers(triggers)
            if parsed:
                settings.triggers = parsed

        mode = raw.get("match_mode")
        if isinstance(mode, str) and mode.strip().lower() in {"any", "all"}:
            settings.match_mode = mode.strip().lower()

        settings.case_sensitive = _as_bool(raw, "case_sensitive", settings.case_sensitive)
        settings.require_transition = _as_bool(raw, "require_transition", settings.require_transition)
        settings.watch_process_windows = _as_bool(
            raw, "watch_process_windows", settings.watch_process_windows
        )
        settings.dry_run = _as_bool(raw, "dry_run", settings.dry_run)
        settings.interval_seconds = round(
            _as_float(raw, "interval_seconds", settings.interval_seconds, *INTERVAL_RANGE), 2
        )
        settings.dwell_checks = _as_int(raw, "dwell_checks", settings.dwell_checks, *DWELL_RANGE)
        settings.shutdown_delay_seconds = _as_int(
            raw, "shutdown_delay_seconds", settings.shutdown_delay_seconds, *DELAY_RANGE
        )
        settings.last_process = _as_optional_str(raw, "last_process")
        settings.last_title_hint = _as_optional_str(raw, "last_title_hint")
        return settings


def load_settings(path: Path | str | None = None) -> Settings:
    """Read settings, falling back to defaults on any problem."""
    target = Path(path) if path is not None else config_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError) as exc:
        log.warning("Could not read settings from %s (%s); using defaults", target, exc)
        return Settings()
    return Settings.from_dict(raw)


def save_settings(settings: Settings, path: Path | str | None = None) -> bool:
    """Write settings atomically.  Returns False instead of raising."""
    target = Path(path) if path is not None else config_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
        os.replace(temp, target)
        return True
    except OSError as exc:
        log.warning("Could not save settings to %s (%s)", target, exc)
        return False
