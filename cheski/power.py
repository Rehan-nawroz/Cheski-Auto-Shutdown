"""The only module that can power the machine off.

Safety design
-------------
* Commands are built as **argv lists** and executed with ``shell=False``, so a
  trigger message containing quotes or ``&`` can never turn into a second
  command.
* :attr:`PowerController.dry_run` logs the exact command instead of running it.
  Set ``CHESKI_DRY_RUN=1`` in the environment to force it on globally (handy
  for a supervised trial run).
* The command runner is **injectable**, so tests assert on the exact argv
  without ever launching ``shutdown.exe``.

Windows detail worth knowing: ``shutdown /s /t <n>`` implies ``/f`` whenever
``n`` is greater than zero, so applications are force-closed at the deadline
with no "save your work?" prompt.  That is why the GUI countdown says so in
plain language.  Passing ``/t 0`` does *not* imply ``/f``.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

from .config import DELAY_RANGE

log = logging.getLogger("cheski.power")

MAX_COMMENT_LENGTH = 200
EXECUTABLE = "shutdown"

DEFAULT_MESSAGE = (
    "Cheski Auto Shutdown: watched download finished. "
    "PC shutting down in {seconds}s - run 'shutdown /a' to cancel."
)

#: Characters stripped from the user-facing message.  ``shell=False`` already
#: makes injection impossible; this is belt-and-braces in case anyone ever
#: switches to a shell, and keeps the log line readable.
_UNSAFE_CHARACTERS = ('"', "'", "&", "|", "<", ">", "^", "`", ";", "\r", "\n", "\t")

#: shutdown.exe exit codes we can explain to the user.
_RETURN_CODE_TEXT = {
    0: "Command accepted.",
    5: "Access denied - your account is not allowed to shut this PC down.",
    1116: "There is no shutdown in progress, so there was nothing to abort.",
    1190: "A shutdown is already scheduled on this PC.",
}


def describe_return_code(code: int | None) -> str:
    return _RETURN_CODE_TEXT.get(code, "") if code is not None else ""


def sanitize_message(text: str, limit: int = MAX_COMMENT_LENGTH) -> str:
    """Collapse whitespace and strip characters that are unsafe in a comment."""
    cleaned = str(text)
    for char in _UNSAFE_CHARACTERS:
        cleaned = cleaned.replace(char, " ")
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        cleaned = "Cheski Auto Shutdown: shutting down."
    return cleaned[:limit]


def render_message(template: str, seconds: int) -> str:
    """Format the message template, tolerating stray braces in user text."""
    try:
        rendered = template.format(seconds=seconds)
    except (KeyError, IndexError, ValueError):
        rendered = template
    return sanitize_message(rendered)


@dataclass(frozen=True)
class CommandResult:
    """What happened when we asked the OS to do something."""

    argv: tuple[str, ...]
    returncode: int | None = None
    output: str = ""
    dry_run: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.dry_run or self.returncode == 0

    @property
    def command_line(self) -> str:
        return subprocess.list2cmdline(list(self.argv))

    @property
    def message(self) -> str:
        if self.error:
            return self.error
        if self.dry_run:
            return "Dry run - command logged only, nothing was executed."
        if self.returncode == 0:
            return "Command accepted."
        explained = describe_return_code(self.returncode)
        tail = [line.strip() for line in (self.output or "").splitlines() if line.strip()]
        parts = [item for item in (explained, tail[-1] if tail else "") if item]
        if parts:
            return " ".join(parts)
        return f"shutdown.exe exited with code {self.returncode}."


def _default_runner(argv: list[str]) -> "subprocess.CompletedProcess[str]":
    kwargs: dict = {}
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creation_flags:
        kwargs["creationflags"] = creation_flags
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)  # noqa: S603


class PowerController:
    """Schedules and cancels the operating system shutdown.

    ``runner`` exists purely so tests can substitute a recorder.  Everything
    funnels through :meth:`run`, which honours ``dry_run``.
    """

    def __init__(
        self,
        *,
        seconds: int = 60,
        message: str = DEFAULT_MESSAGE,
        dry_run: bool = False,
        runner: Callable[[list[str]], "subprocess.CompletedProcess[str]"] | None = None,
        executable: str = EXECUTABLE,
    ) -> None:
        low, high = DELAY_RANGE
        try:
            clamped = int(max(low, min(high, int(seconds))))
        except (TypeError, ValueError):
            clamped = 60
        self.seconds = clamped
        self.message = render_message(message, clamped)
        self.runner = runner if runner is not None else _default_runner
        self.executable = executable
        self.dry_run = bool(dry_run) or _env_dry_run()

    # -- command construction -------------------------------------------

    def build_shutdown_argv(self) -> list[str]:
        """``shutdown /s /t 60 /c "..."``.

        Note ``/f`` is implied by the non-zero timeout; see the module
        docstring.  ``/d`` (a reason code) is deliberately omitted: passing an
        unplanned reason would be inaccurate, and planned reasons need
        elevation on some systems, so the tool stays admin-free.
        """
        return [
            self.executable,
            "/s",
            "/t",
            str(self.seconds),
            "/c",
            self.message,
        ]

    def build_abort_argv(self) -> list[str]:
        return [self.executable, "/a"]

    def describe(self) -> str:
        """Exact command line the app would run, for the GUI preview."""
        return subprocess.list2cmdline(self.build_shutdown_argv())

    # -- execution -------------------------------------------------------

    def run(self, argv: Sequence[str]) -> CommandResult:
        argv_tuple = tuple(str(item) for item in argv)
        if self.dry_run:
            log.info("DRY RUN: %s", subprocess.list2cmdline(list(argv_tuple)))
            return CommandResult(argv=argv_tuple, returncode=0, dry_run=True)

        command_line = subprocess.list2cmdline(list(argv_tuple))
        log.warning("Executing: %s", command_line)
        try:
            completed = self.runner(list(argv_tuple))
        except OSError as exc:
            log.error("Could not execute %s: %s", argv_tuple[0], exc)
            return CommandResult(
                argv=argv_tuple, error=f"Could not run {argv_tuple[0]}: {exc}"
            )

        output = f"{completed.stdout or ''}{completed.stderr or ''}"
        result = CommandResult(
            argv=argv_tuple, returncode=completed.returncode, output=output
        )
        if result.ok:
            log.info("%s -> accepted (rc=%s)", command_line, result.returncode)
        else:
            log.error("%s -> %s", command_line, result.message)
        return result

    def schedule_shutdown(self) -> CommandResult:
        """Ask Windows to shut down after :attr:`seconds`."""
        return self.run(self.build_shutdown_argv())

    def abort_shutdown(self) -> CommandResult:
        """Cancel a pending shutdown (only possible inside the timeout)."""
        return self.run(self.build_abort_argv())


def _env_dry_run() -> bool:
    return os.environ.get("CHESKI_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}

