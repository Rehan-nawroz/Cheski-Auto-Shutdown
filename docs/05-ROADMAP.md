# 05 — Roadmap

Each item below records the design sketch and the specific risk, so v2 starts
from a decision rather than a blank page. Ordered by expected value per unit of
risk.

## 1. Network-idle detection (the strongest addition)

**Problem it solves:** the title-bar approach only works for apps that put
progress in their title. Some of the biggest transfers — browser downloads,
`robocopy`, game launchers that only show a bubble — never touch the title bar.

**Design:** add `NetworkIdleSource` alongside `WindowSource`, behind the same
"is the download finished?" interface. Poll `psutil.net_io_counters()` (and,
on Windows, per-process IO counters) every interval; declare "finished" when
throughput stays below a threshold for a sustained window.

**Configuration that must exist before shipping:** idle threshold (KB/s),
sustained-for duration (default 5 minutes), and a minimum total bytes argument
so an app that never started downloading cannot trigger it.

**Risk:** the highest false-positive surface in the project. A paused download,
a stalled connection, or a VPN drop all look like "finished". Mitigation: make
it opt-in, require a *sustained* idle window, and show the observed throughput
in the status box so the user can calibrate it themselves.

**Interface change:** extract a `CompletionSignal` protocol that both
`TriggerEngine` and the idle detector satisfy, and let the GUI choose a
detection mode per target.

## 2. Minimise to the system tray

**Problem:** the window takes screen space for something the user wants to
forget about.

**Design:** `pystray` + Pillow (Pillow is already present on this machine;
`pystray` would be a new runtime dependency). Hide the Tk root with
`root.withdraw()`, keep the tray icon as the only UI, show the current state in
the tooltip, and offer Show / Stop / Abort / Quit in the menu.

**Risk:** two event loops, and a hidden window that is still holding the
monitor thread. Needs the countsdown dialog to remain topmost and visible even
when the main window is hidden — otherwise an unattended machine can be
scheduled for shutdown with no visible way to cancel. Test that path explicitly.

## 3. Multi-target groups

**Problem:** two downloads from two launchers; the PC should stay on until
*both* finish.

**Design:** the monitor owns a list of targets, each with its own
`TriggerEngine`, plus a group operator (`all` / `any`) and per-target weights.
The existing per-target engine stays as-is; a small aggregator decides when the
group is satisfied.

**Risk:** a target whose window is lost would block an `all` group forever. The
aggregator needs an explicit policy for lost, never-started and already-matched
targets, and the UI has to show which target is holding things up.

## 4. Shipping it as a real application

* PyInstaller `--onefile`, plus a stable icon.
* **Antivirus:** a small unsigned binary whose job is to invoke `shutdown.exe`
  is a textbook false-positive shape. Expect detections; document the
  submission process for the major vendors, and consider shipping the plain
  Python source as the primary distribution.
* The Microsoft Store Python install has its own quirks under PyInstaller, so
  the build should be verified against a python.org interpreter as well.
* Nice-to-have: a `pyproject.toml` with a `cheski` console script, and a
  `--tray` flag that starts minimised.

## 5. Hardening and quality of life

| Item | Note |
| --- | --- |
| Trigger presets per app | Ship known-good profiles (Steam, Epic, Chrome, qBittorrent) so users do not have to guess the wording. |
| Regex triggers | Power for advanced users, with an explicit warning that a bad pattern is a false-positive generator. Validate patterns at entry time. |
| Postpone / snooze | "Not now, ask me again in 30 minutes" — useful when the trigger fires but the user is mid-task. |
| Autostart on login | Optional Task Scheduler entry, so an unattended rig is always watching. |
| Non-Windows backends | `PowerController` is already isolated; a `systemctl` / `loginctl` backend would need a different window-title source (X11 `_NET_WM_NAME`, macOS Accessibility API). The trigger engine and monitor loop are platform-independent as written. |
| `shutdown /sg` | Restart-with-apps-restored variant, useful for dev machines. |
| Notification before firing | A desktop toast in the last few seconds, if the user turns out to be at the PC. |

## 6. Explicitly rejected

| Idea | Why not |
| --- | --- |
| Screen-scraping / OCR / vision model | Rejected at design time: cost, hallucination risk, and a false positive means an interrupted install. |
| Killing the download app before shutdown | Pointless — the download is already finished. If it is *not* finished, killing it destroys partial progress. |
| Forcing `/f` explicitly | Already implied by a non-zero `/t`; passing it adds nothing and hides the behaviour. |
| Passing a `/d` reason code | An inaccurate event-log entry, and planned reasons need elevation on some systems. |
| Silently rescheduling after an abort | The user said no. Re-arming on their behalf would be worse than not firing at all. |
