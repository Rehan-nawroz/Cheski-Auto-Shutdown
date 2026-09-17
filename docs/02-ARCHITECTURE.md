# 02 — Architecture

## 1. Module map

```
cheski/
├── __main__.py          python -m cheski
├── config.py            Settings dataclass, JSON persistence, all numeric ranges
├── logging_setup.py     rotating file log + optional console log
├── windows.py           window enumeration, handle binding, ctypes helpers, re-attach
├── triggers.py          trigger parsing, normalised matching, arming state machine
├── monitor.py           MonitorThread: the polling loop, emits events
├── power.py             PowerController: the only place that can shut the PC down
└── ui/
    ├── theme.py         Terminal Precision palette, font fallbacks, blend maths, ttk theme
    ├── icons.py         real window icons via ctypes+PIL, geometric fallback avatars (LRU)
    ├── processes.py     process rows: windows + psutil memory, UWP naming, search text
    ├── widgets.py       canvas widgets: picker, chips, segmented, metric cards, ring, terminal log, abort pill, toast
    ├── chrome.py        borderless window chrome: header, window controls, drag, resize grip
    ├── state.py         run states + widget policy + countdown text (no tkinter)
    └── app.py           CheskiApp: wiring, event pump, persistence, main()
```

Dependencies flow one way and there are no cycles:

```
ui/app.py ──> monitor.py ──> triggers.py
    │  │          │
    │  │          └───────> windows.py
    │  └─> ui/state.py, ui/countdown.py
    ├──> power.py ────────> config.py ──> triggers.py
    └──> config.py, logging_setup.py
```

`ui/state.py` holds the run-state vocabulary and the widget policy each state
implies (can Start be pressed? is the picker frozen?) as plain functions and
dicts with no tkinter import, so the decisions are unit-testable headlessly.

Only `power.py` executes anything destructive. Only `ui/app.py` wires
widgets to behaviour; the visual components themselves live in `ui/widgets.py`
and know nothing about shutdown semantics.

### 1.1 The Terminal Precision redesign

The interface is the "Terminal Precision" design system (dark telemetry
theme, cyan `#06b6d4` primary, amber warnings, crimson abort).  How it maps
to code:

* **`ui/theme.py`** owns every visual constant: the palette tokens, the font
  fallback chains (the design names Geist / Inter / JetBrains Mono; on a
  stock Windows box these resolve to Segoe UI / Consolas, and the resolved
  family is published for canvases), RGB blend helpers for animation
  in-betweens, alpha-over-surface compositing (tk has no real alpha), and the
  private ttk theme.
* **`ui/widgets.py`** is the canvas-drawn component set: the searchable
  process picker (double-click to select, animated reload, scanning pulse),
  trigger chips (removable pills), the ANY/ALL segmented control, metric
  cards (large number, +/- steppers, count-up animation, amber pulse when the
  grace period drops below 30 s), the dry-run banner (flask icon, SAFE/LIVE
  badge, amber tint when live), the status ring (idle dim, slow spin when
  armed, fast when executing), the terminal log (coloured levels,
  auto-scroll lock, copy-all, bounded to 600 lines), the abort pill (hover
  fill, press shake) and toasts.
* **`ui/icons.py`** extracts real per-process window icons (HWND,
  WM_GETICON/GetClassLongPtrW, module path, SHGetFileInfoW, decoded with
  Pillow) and falls back to deterministic geometric avatars; results are
  LRU-capped so an overnight run cannot accumulate them.
* **`ui/processes.py`** builds picker rows: windows enriched with psutil
  memory (optional at runtime), UWP hosts named by their window title, own
  process excluded, sorted by display name.
* **`ui/chrome.py`** replaces the native title bar with a styled header
  (logomark, name, version badge, window controls) on a borderless window,
  with edge-drag movement and a resize grip; it falls back to the native
  frame when the window manager refuses the override.
* **The countdown moved out of the pop-up dialog** into the status strip:
  large mm:ss readout, red, above the always-visible abort pill.  Closing the
  window while triggered still aborts the pending shutdown for the user.

Only the *wiring* changed.  The engine, monitor, power layer and their
guarantees (arming guard, dwell, handle binding, argv-only commands) are
untouched./app.py` touches
widgets.

## 2. Run-time data flow

```
        ┌─────────────────────── GUI thread (tkinter) ───────────────────────┐
        │                                                                    │
        │  CheskiApp._pump  ── drains ──> queue.Queue[MonitorEvent]          │
        │        ▲                                     ▲                     │
        │        │ root.after(150 ms)                   │ .put()              │
        └────────┼─────────────────────────────────────┼─────────────────────┘
                 │                                     │
                 │                                     │
        ┌────────┴─────────────────────────────────────┴─────────────────────┐
        │  Monitoring thread (cheski.monitor.MonitorThread)                  │
        │                                                                    │
        │  loop: get_title(hwnd) -> TriggerEngine.evaluate(title) -> event   │
        │        stop_event.wait(interval)                                   │
        └──────────┬───────────────────────────────────────┬─────────────────┘
                   │                                       │
        cheski.windows.get_title                  cheski.power.PowerController
        (user32!GetWindowTextW)                   (shutdown.exe /s /t 60 /c …)
```

The two threads share exactly three things: a `queue.Queue` (worker → GUI), a
`threading.Event` (GUI → worker) and the `TriggerEngine` (owned by the worker;
the GUI only reads the booleans that arrive inside events).

## 3. Threading contract

* **The worker never touches a widget.** tkinter is not thread-safe; calling
  into it from another thread produces crashes that are hard to reproduce.
  Everything crosses the boundary as an immutable `MonitorEvent`.
* **The GUI never blocks.** `_pump` drains at most 200 events every 150 ms and
  reschedules itself with `root.after`.
* **Cancellation is instantaneous.** The loop waits with
  `stop_event.wait(interval)` instead of `time.sleep(interval)`, so Stop is
  immediate rather than "up to one interval late".
* **The worker cannot die silently.** The whole loop body is wrapped in
  `except Exception`, which emits an `error` event and keeps polling. A monitor
  thread that dies quietly at 3am is the worst possible failure.
* **Shutdown runs on the GUI thread.** Scheduling only happens after a `match`
  event has been drained, so there is exactly one place that can start a
  countdown.

## 4. State machine

```
                 Start                     trigger seen absent
   IDLE ───────────────────▶ WARMING ─────────────────────────▶ MONITORING
     ▲                         │                                   │
     │                         │ trigger already present,          │ trigger seen
     │                         │ so it is never allowed to fire    │ (after dwell)
     │                         │                                   ▼
     │      Stop               │                              TRIGGERED
     └─────────────────────────┴──────────────────────────────────┤
                                                                   │ popup + beep
                                                     ┌─────────────┴─────────────┐
                                                     │                           │
                                              Abort (shutdown /a)         timer expires
                                                     │                           │
                                                     ▼                           ▼
                                                 ABORTED                  powers off
```

| State | Meaning | Can it shut the PC down? |
| --- | --- | --- |
| `idle` | Not watching anything. | No |
| `warming` | Watching, but the trigger was already in the title bar at Start, so firing is blocked until it clears once. | No |
| `monitoring` | Armed; the next confirmed match fires. | Yes |
| `triggered` | A shutdown is scheduled and the countdown window is up. | Already scheduled |
| `aborted` | The user cancelled; nothing is pending. | No |

`warming` is the single most important state in the design. It converts "I
clicked Start while Steam was showing 100% during verification" from a
catastrophe into a no-op.

## 5. Window identity

### 5.1 Binding

A target is a `WindowInfo(handle, title, pid, process)`. The **handle** is the
identity; the title is only ever data.

Why not bind to the title: the title is the variable being measured, so it
changes constantly and can match several windows at once ("Settings" appears
twice on a typical desktop).

### 5.2 Polling

* Enumeration (`pygetwindow.getAllWindows`) happens only when the user presses
  *Refresh*, and when re-attaching. It is comparatively expensive.
* Polling reads one handle with `user32!GetWindowTextW`, which is what runs
  every 2 seconds.
* `user32!IsWindow` distinguishes "window is gone" (`None`) from "window exists
  with an empty caption" (`""`). The trigger engine never sees `None` — a
  vanished window is reported as `target_lost`, and monitoring continues.

### 5.3 Re-attach

When the handle dies, `resolve_target()` looks for a replacement, preferring
a matching **PID**, then a matching **process name**, and among equals the
window with the most informative (longest) title. A re-created Steam window is
usually caught by PID.

If nothing matches, the target is *lost* — reported once, retried every poll,
and never treated as a completed download.

`pid` and `process` come from `GetWindowThreadProcessId` +
`QueryFullProcessImageNameW` through `ctypes`, so no `pywin32` or `psutil` is
needed. Known limitation: UWP apps report `ApplicationFrameHost.exe`, their
host process.

## 6. Trigger engine

### 6.1 Normalisation

Both the trigger and the title are put through NFKC Unicode normalisation
(which folds full-width `１００％` to `100%` and non-breaking spaces to spaces)
and case-folded unless *Match exact case* is on.

If the trigger contains **no space**, all whitespace is removed from the title
before matching. That makes `100%` match `Downloading (100 %)`, a real
rendering used by several downloaders. It does not create false positives:
`1000%` contains no contiguous `100%`.

If the trigger **does** contain a space, whitespace is collapsed instead, and
the match is a plain substring test.

### 6.2 Modes

* **any** (default) — fires when at least one trigger word is present.
* **all** — fires only when every trigger word is present. Recommended for
  risky triggers, e.g. `Downloading` + `100%`, so an unrelated title change
  cannot fire.

### 6.3 Guard and dwell

* **Transition guard** (`require_transition`, on by default): the engine
  records that the trigger was absent at least once (`armed`) before a match
  can fire. Until then the UI shows *Arming*.
* **Dwell** (`dwell_checks`, default 3 polls ≈ 6 s at the default interval):
  the match must be observed on consecutive polls. A title that flaps between
  `100%` and `Verifying` never accumulates to a firing streak.
* **Latching**: once fired, the engine latches and will not fire again until
  reset, so nothing re-arms during the countdown.

## 7. Power layer

Commands are argv lists executed with `shell=False`; a message containing
quotes or `&` can never become a second command. Message text is additionally
sanitised (unsafe characters removed, length capped at 200) as
belt-and-braces.

```
schedule:  shutdown /s /t 60 /c "<sanitised message>"
abort:     shutdown /a
```

* `/d` (reason code) is deliberately **omitted**: an unplanned reason would be
  an inaccurate event-log entry, and planned reasons require elevation on some
  systems. Omitting it keeps Cheski admin-free.
* `/f` is not passed explicitly, because a non-zero `/t` already implies it.
* Return codes are translated: `1116` → "nothing to abort", `1190` → "a
  shutdown is already scheduled", `5` → "access denied".
* `dry_run` logs the exact command and returns a synthetic success.
* The **runner is injectable**. Every test injects a recorder, and `tests`
  contains an autouse guard that fails the test if the real `shutdown.exe` is
  ever reached. This is the most important test-safety property in the project.

Closing the main window while `triggered` explicitly aborts the pending
shutdown: the countdown dialog is the only way to cancel, so leaving a
scheduled shutdown with no UI would be the worst outcome.

## 8. Configuration and logging

* Settings: `%APPDATA%\CheskiAutoShutdown\config.json`, written atomically
  (temp file + `os.replace`), loaded defensively — a corrupt file falls back to
  defaults instead of preventing startup.
* Logs: `%APPDATA%\CheskiAutoShutdown\logs\cheski.log`, rotating at 512 KB with
  3 backups. Logging failure is never fatal.
* The log records the exact command line, its exit code and the reason, which
  is what makes an unattended night auditable the next morning.

### Ranges

| Setting | Range | Default |
| --- | --- | --- |
| `interval_seconds` | 0.5 – 60 | 2.0 |
| `dwell_checks` | 1 – 20 | 3 |
| `shutdown_delay_seconds` | 15 – 600 | 60 |
| `triggers` | any non-empty strings | `100%`, `complete`, `finished` |

Everything is clamped on load *and* on read from the spinboxes, so neither a
hand-edited file nor a typed value can ask for something absurd.

## 9. Failure modes and the intended response

| Failure | Response |
| --- | --- |
| Target window closed | `target_lost` event, monitor keeps retrying, **no shutdown**. |
| Target window recreated | Re-attach by PID/process, log `target_reattached`. |
| Poll raises (GDI hiccup, permission) | `error` event, loop continues. |
| `shutdown.exe` returns non-zero | Explained in the log; the countdown dialog is not opened because nothing was scheduled. |
| `shutdown /a` finds nothing pending | Treated as "nothing to abort", state returns to idle. |
| Config file corrupt | Defaults, warning in the log. |
| Log directory unwritable | Console logging only; the app still runs. |
| User closes the window mid-countdown | Pending shutdown is aborted for them. |
