"""Trigger matching and the arming state machine.

Three safety properties live here, and each one exists because of a concrete
way this tool could otherwise shut a PC down by mistake:

1. **Whitespace/Unicode-normalised matching.**  A trigger you type as ``100%``
   must still match a title bar that renders ``Downloading (100 %)``, and
   full-width digits (``１００％``) must fold to ASCII.  Otherwise the tool
   silently never fires -- the safer failure, but still a broken one.
2. **Transition guard.**  If the title already contains the trigger when you
   press Start (Steam sitting at "100%" while it verifies, a finished download
   you forgot about), firing immediately would shut the PC down seconds after
   you clicked the button.  With ``require_transition`` the engine stays in
   WARMING until the trigger is seen *absent* at least once, then arms.
3. **Dwell.**  A trigger must be observed on ``dwell_checks`` consecutive polls
   before it counts, absorbing titles that flap between "100%" and "Verifying".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

ANY = "any"
ALL = "all"
_VALID_MODES = (ANY, ALL)

_SPLIT = re.compile(r"[,;|\r\n]+")


def parse_triggers(raw: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Turn free text (``"100%, complete"``) into a clean, de-duplicated tuple.

    Commas, semicolons, pipes and newlines all separate.  Duplicates are
    removed case-insensitively so ``100%, 100%`` is one trigger.  This is the
    one trigger parser: config loading and the GUI both go through it.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts = _SPLIT.split(raw)
    else:
        parts = [piece for item in raw for piece in _SPLIT.split(str(item))]

    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = " ".join(part.split())
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return tuple(out)


def _norm(value: str, case_sensitive: bool) -> str:
    # NFKC folds full-width digits/percent signs and non-breaking spaces.
    text = unicodedata.normalize("NFKC", str(value))
    return text if case_sensitive else text.casefold()


def _matches(title_norm: str, trigger: str, case_sensitive: bool) -> bool:
    needle = " ".join(_norm(trigger, case_sensitive).split())
    if not needle:
        return False
    haystack = " ".join(title_norm.split())
    if " " in needle:
        return needle in haystack
    # Space-free trigger: ignore all spaces in the title, so "100%" still
    # matches "Downloading 100 %" without inventing false positives like
    # "1000%" (no contiguous "100%" occurs there).
    return needle in haystack.replace(" ", "")


@dataclass(frozen=True)
class MatchResult:
    """Outcome of one poll."""

    matched: bool
    hits: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    streak: int = 0
    reason: str = ""


@dataclass
class TriggerConfig:
    triggers: tuple[str, ...] = ()
    match_mode: str = ANY
    case_sensitive: bool = False
    dwell_checks: int = 1
    require_transition: bool = True

    def __post_init__(self) -> None:
        self.triggers = parse_triggers(self.triggers)
        if self.match_mode not in _VALID_MODES:
            self.match_mode = ANY
        self.dwell_checks = max(1, int(self.dwell_checks))


@dataclass
class TriggerEngine:
    """Feed it title strings; it tells you when the download is done.

    The engine is not thread-safe by design -- it is owned by the monitor
    thread and observed through :class:`~cheski.monitor.MonitorEvent`.
    """

    config: TriggerConfig = field(default_factory=TriggerConfig)
    _streak: int = field(default=0, init=False, repr=False)
    _armed: bool = field(default=False, init=False, repr=False)
    _fired: bool = field(default=False, init=False, repr=False)
    _last_hits: tuple[str, ...] = field(default=(), init=False, repr=False)

    @property
    def armed(self) -> bool:
        """True once the trigger has been seen absent at least once."""
        return self._armed or not self.config.require_transition

    @property
    def fired(self) -> bool:
        return self._fired

    @property
    def streak(self) -> int:
        return self._streak

    def reset(self) -> None:
        """Forget everything, including a previous firing."""
        self._streak = 0
        self._armed = not self.config.require_transition
        self._fired = False
        self._last_hits = ()

    def _collect(self, title: str) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
        title_norm = _norm(title, self.config.case_sensitive)
        hits: list[str] = []
        missing: list[str] = []
        for trigger in self.config.triggers:
            if _matches(title_norm, trigger, self.config.case_sensitive):
                hits.append(trigger)
            else:
                missing.append(trigger)
        if self.config.match_mode == ALL:
            matched = bool(hits) and not missing
        else:
            matched = bool(hits)
        return matched, tuple(hits), tuple(missing)

    def evaluate(self, title: str) -> MatchResult:
        if self._fired:
            return MatchResult(
                matched=False,
                hits=self._last_hits,
                reason="Already fired; reset the engine to monitor again.",
            )

        matched, hits, missing = self._collect(title)

        if not matched:
            self._streak = 0
            self._armed = True
            reason = "Trigger words not present." if self.config.triggers else "No trigger words configured."
            return MatchResult(False, hits, missing, 0, reason)

        if self.config.require_transition and not self._armed:
            self._streak = 0
            return MatchResult(
                matched=False,
                hits=hits,
                missing=missing,
                streak=0,
                reason=(
                    "Trigger is already present in the title bar; waiting for it to "
                    "clear once before arming (safety)."
                ),
            )

        self._streak += 1
        if self._streak >= self.config.dwell_checks:
            self._fired = True
            self._last_hits = hits
            return MatchResult(True, hits, missing, self._streak, "Trigger confirmed.")

        remaining = self.config.dwell_checks - self._streak
        return MatchResult(
            False, hits, missing, self._streak, f"Matched; confirming in {remaining} more check(s)."
        )
