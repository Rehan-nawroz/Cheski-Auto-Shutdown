# 01 — Product plan

## 1. The problem

Downloading a large game or file means leaving a machine powered on for hours
after the interesting part is over. People either leave it running all night —
wasting power and hardware life — or stay awake to switch it off.

The user's real goal is not "monitor a download". It is **"let me go to sleep
and not think about this"**.

## 2. The chosen approach: watch the title bar

Almost every downloader reports progress in its window title: Steam,
Epic Games Store, Chrome, Edge, Firefox, Internet-Download-Manager, torrent
clients. When the job finishes, the title changes — usually to something
containing `100%`, `Complete`, or `Finished`.

So Cheski reads that string, and when the trigger word appears it shuts the PC
down.

### Why not a vision model

The original idea was a custom Vision LLM looking at the screen. It was
dropped, for three reasons:

1. **Cost.** Reading a window title is a few microseconds and effectively 0%
   CPU. A local vision model costs gigabytes of RAM and sustained GPU time on
   the machine you are trying to leave alone.
2. **Reliability.** A vision model can misread `99%` as `100%`, and the
   consequence of that mistake is a shutdown in the middle of an install.
   A title bar is an exact string placed there by the application's own code.
3. **Universality.** One implementation works across every downloader, because
   they all do the same thing to their title bars.

The trade-off accepted: this only works for apps that *do* put progress in the
title bar. Apps that only show progress in a notification bubble are out of
scope — a limitation worth stating plainly rather than hiding.

## 3. Target user and scenario

Someone downloading a large game or file who wants the machine off when it is
done. They are technical enough to run a Python program, but they are going to
bed and will not be watching the screen.

**Success looks like:** they start a download, launch Cheski, pick the window,
type `100%`, press Start, walk away — and the machine powers itself down after
the download ends, having force-closed nothing they cared about.

**Failure looks like:** the machine shuts down while the download is still
running, or it shuts down moments after the user presses Start because the
title already contained the trigger word.

## 4. The user flow

1. Start the download. Notice the title bar reads `Downloading (45%)`.
2. Launch Cheski Auto Shutdown.
3. Press **Refresh list**, pick the app.
4. Trigger words default to `100%`; adjust if the app uses different wording.
5. Press **Start monitoring**. Status reads *Arming*, then *Monitoring*,
   then *TRIGGERED*.
6. Minimise it and go to sleep.
7. The download finishes; the title bar changes; Cheski schedules the shutdown
   and shows a 60-second countdown window with a big **EMERGENCY CANCEL**
   button.
8. The PC powers off.

## 5. Requirements

### Must have (v1)

| # | Requirement |
| --- | --- |
| R1 | Pick a target window from a refreshed list of open windows. |
| R2 | User-defined trigger words, any language, multiple words, any/all matching. |
| R3 | Poll the target's title on a background thread with a configurable interval. |
| R4 | Never block or freeze the GUI while monitoring. |
| R5 | On match, schedule a shutdown with a countdown the user can cancel. |
| R6 | An always-available **EMERGENCY ABORT** button that runs `shutdown /a`. |
| R7 | Refuse to fire on a trigger that was already present when monitoring started. |
| R8 | Keep watching the right window even when its title changes. |
| R9 | Tell the user exactly which command it is about to run. |
| R10 | A dry-run mode that logs instead of executing. |

### Should have

| # | Requirement |
| --- | --- |
| R11 | Survive the target window being closed and reopened (re-attach). |
| R12 | Remember settings and the last target between runs. |
| R13 | Write an auditable log file for unattended runs. |
| R14 | Explain shutdown failures (`Access denied`, `nothing to abort`, …) instead of failing silently. |

### Out of scope for v1

Deliberately excluded, with the reason, so nobody assumes they exist:

| Idea | Why not now |
| --- | --- |
| System-tray icon | Needs `pystray` (and Pillow). Adds a dependency and a second windowing mode; the roadmap has it as the first v2 item. |
| Network-activity detection (`psutil`) | A different, complementary signal. It needs calibration per machine; better as a second backend once the first is proven. |
| Monitoring several apps at once | Turns a simple rule into a group expression (`all finished`) with a much larger false-positive surface. |
| Installer / MSI / signed binary | Packaging is a distribution concern, not a product one, and antivirus false-positives around `shutdown.exe` callers need testing. |
| macOS / Linux | `shutdown.exe` and the Win32 window APIs are the core of the design. The power layer is isolated so a port stays possible. |
| Any AI/vision path | Rejected above. |

## 6. Two specification corrections

Both were discovered by inspecting this machine during planning, and both
changed the design. They are recorded here because the original brief did not
mention either.

### 6.1 `shutdown /s /t 60` force-closes applications

The brief specified `shutdown /s /t 60`. `shutdown /?` states:

> The /f parameter is implied when a value greater than 0 is specified.

So at the end of the 60 seconds, Windows forcibly closes running applications
**without** the "do you want to save your changes?" prompt. A user who is at
the PC, sees the countdown, and does nothing can still lose unsaved work.

Mitigations shipped in v1:

* the countdown window states the force-close behaviour in plain language;
* an audible alert plays when the trigger fires;
* the countdown length is configurable (15–600 seconds, default 60);
* the abort button is always available and always live.

The alternative — doing the countdown inside the app and then issuing
`shutdown /s /t 0` (which does *not* imply `/f`) — was rejected because it
removes `shutdown /a` as an external escape hatch and leaves a shutdown in a
state Windows cannot cancel. Documented, not hidden.

### 6.2 Selecting a window by title is self-defeating

The brief's step 1 is "the user selects the specific window where the download
is happening". The obvious implementation — remember the window's title and
look for that title — breaks precisely when it matters, because the title is
the thing being watched.

Instead the target is bound to its **window handle (HWND)**, which is stable
for the life of the window, and re-resolved by process name if the handle dies
(Steam recreates windows, which produces a new handle).

Corollary: **a lost window is never a trigger.** If the watched window
disappears, Cheski says so and keeps waiting; it does not assume the download
finished.

## 7. Definition of done for v1

* All *must have* requirements implemented and covered by tests.
* `python -m pytest` green, with no test able to reach the real `shutdown.exe`.
* A dry-run end-to-end rehearsal passes using `tests/manual/title_simulator.py`.
* One supervised real shutdown trial, with the abort button verified.
* The user can explain, from the UI alone, what command will run and how to
  stop it.
