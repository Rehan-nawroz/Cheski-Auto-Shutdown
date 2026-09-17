"""Process rows: windows enriched with memory usage (and friendly names).

The picker shows ``process · pid · 123 MB``.  Memory comes from ``psutil``
(declared optional — without it the column shows pid only).  UWP apps report
``ApplicationFrameHost.exe`` as their window owner; for those, the friendly
name is taken from the host's *window title* instead, so the picker still
reads "Settings" rather than a system process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cheski.windows import IGNORED_TITLES, WindowInfo

log = logging.getLogger("cheski.processes")

try:  # optional at runtime; installed by requirements.txt
    import psutil
except ImportError:  # pragma: no cover - exercised only without psutil
    psutil = None  # type: ignore[assignment]

_UWP_HOSTS = {"applicationframehost.exe", "runtimebroker.exe"}


@dataclass(frozen=True)
class ProcessRow:
    """One selectable row in the process picker."""

    info: WindowInfo
    memory_bytes: int | None
    #: Display name: the window title, except UWP hosts show their title.
    display_name: str

    @property
    def pid(self) -> int | None:
        return self.info.pid

    @property
    def process(self) -> str | None:
        return self.info.process

    @property
    def memory_text(self) -> str:
        if self.memory_bytes is None:
            return "—"
        value = float(self.memory_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.0f} {unit}"
            value /= 1024
        return "—"

    @property
    def search_text(self) -> str:
        """Lowercased haystack for the picker's search field."""
        parts = [
            self.display_name,
            self.info.process or "",
            str(self.info.pid or ""),
            self.info.title or "",
        ]
        return " ".join(parts).casefold()


def _memory_for_pid(pid: int | None) -> int | None:
    if psutil is None or not pid:
        return None
    try:
        return int(psutil.Process(int(pid)).memory_info().rss)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("memory lookup for pid %s failed: %s", pid, exc)
        return None


def _friendly_name(info: WindowInfo) -> str:
    title = (info.title or "").strip()
    if info.process and info.process.casefold() in _UWP_HOSTS and title:
        return " ".join(title.split())
    return " ".join(title.split()) or "(untitled)"


def build_rows(infos: list[WindowInfo], own_pid: int | None = None) -> list[ProcessRow]:
    """Filter, enrich and sort windows into picker rows.

    Sorting: display name, then pid — stable and predictable.
    """
    rows: list[ProcessRow] = []
    for info in infos:
        if own_pid is not None and info.pid and info.pid == own_pid:
            continue  # watching our own window can only ever be a silent no-op
        title = " ".join((info.title or "").split())
        if not title or title.casefold() in IGNORED_TITLES:
            continue
        rows.append(
            ProcessRow(
                info=info,
                memory_bytes=_memory_for_pid(info.pid),
                display_name=_friendly_name(info),
            )
        )
    rows.sort(key=lambda row: (row.display_name.casefold(), row.pid or 0, row.info.handle))
    return rows
