# 03 — Execution plan

The build was sequenced so that everything testable without a GUI was tested
without a GUI, and so that the destructive code path (`shutdown.exe`) only
became reachable once it was wrapped in an injectable seam.

## Milestone summary

| # | Milestone | Deliverable | Status |
| --- | --- | --- | --- |
| M0 | Reconnaissance and skeleton | Verified environment facts, docs set, packaging files | ✅ Done |
| M1 | Core domain, no GUI | `config`, `logging_setup`, `windows`, `triggers`, `power` + tests | ✅ Done |
| M2 | Monitoring loop | `monitor.py` + tests against fakes | ✅ Done |
| M3 | GUI and harness | `ui/app.py`, title simulator | ✅ Done |
| M4 | Safety hardening | Arming state, dwell, countdown dialog, command preview, persistence, logging | ✅ Done |
| M5 | Verification | Full suite, entry-point checks, dry-run rehearsal | ✅ Done |
| M6 | Supervised live trial | One real shutdown, abort verified | ⏸ Requires the user present |

---

## M0 — Reconnaissance and skeleton

**Goal:** do not write a plan for a machine you have not inspected.

Tasks and findings:

| Task | Result |
| --- | --- |
| Identify the project directory | `C:\Users\Rana\Documents\Project-cheski`, empty, not a git repository. |
| Check the interpreter | Python 3.12.10 (Microsoft Store build), pip 25.0.1. |
| Check tkinter | Present. |
| Check `pygetwindow` | 0.0.9; windows expose both `.title` and `._hWnd`. |
| Check for `pywin32` | **Absent** — so verify `pygetwindow` does not need it. It does not: the Windows backend is ctypes-based. |
| Check `shutdown.exe` flags | `/s /t /a /c /f` all available. |
| Check `pytest`, `psutil`, `pystray`, Pillow | Only Pillow present; `pytest` installed as a dev prerequisite. |
| Prove `HWND → PID → process` in pure ctypes | Returned `328488 → (9060, 'Freebuff.exe')`, no admin needed. |

**Acceptance criteria:** every claim in the plan traceable to a command that
was actually run. Met — see the table above.

Deliverables: this documentation set, `README.md`, `requirements.txt`,
`requirements-dev.txt`, `.gitignore`, `run_cheski.bat`, `pytest.ini`.

---

## M1 — Core domain (no GUI)

**Goal:** all decision-making logic testable without a screen.

| Task | File | Notes |
| --- | --- | --- |
| Settings dataclass, atomic save, forgiving load, range clamping | `cheski/config.py` | Corrupt file ⇒ defaults, never a crash. |
| Rotating file + console logging | `cheski/logging_setup.py` | `forgiving`: an unwritable log dir is not fatal. |
| Window enumeration, labels, ctypes integration, re-attach | `cheski/windows.py` | `filter_windows()` is shared with the fake source so the filter rules are unit-testable. |
| Trigger parsing, normalisation, `any`/`all`, guard, dwell, latching | `cheski/triggers.py` | The safety heart of the project. |
| Command construction, sanitisation, dry run, return-code translation | `cheski/power.py` | Injectable runner from day one. |

**Acceptance criteria**
* Every module imports with no side effects.
* `shutdown.exe` is unreachable from any test (`RecordingRunner` + autouse guard).
* Numeric ranges clamped on both load and GUI read.

**Evidence**
```
python -m pytest tests/test_config.py tests/test_triggers.py tests/test_power.py tests/test_windows.py
```
31 + 27 + 26 + 13 = 97 tests.

---

## M2 — Monitoring loop

**Goal:** prove the loop is correct, cancellable and unkillable.

| Task | Notes |
| --- | --- |
| `MonitorThread` with `stop_event.wait(interval)` | Instant cancellation. |
| Event vocabulary: `tick`, `match`, `target_lost`, `target_reattached`, `error`, `stopped` | The GUI only ever reacts to events. |
| Lost-window handling | Reported once, retried, never a trigger. |
| Re-attach path | Covered with the liveness check monkeypatched so results do not depend on the developer's desktop. |
| Error resilience | One poll raising must not stop the loop. |

**Acceptance criteria**
* Stop takes effect in well under one interval, even with a 30 s interval.
* A raising poll produces an `error` event and polling continues.
* Repeated identical titles do not produce repeated events (log-spam guard).
* A lost target never produces a `match`.

**Evidence**
```
python -m pytest tests/test_monitor.py     # 9 tests
```

Design note discovered while writing these tests: flipping the title to `100%`
before the engine has been armed correctly leaves the app in *Arming* forever.
The first draft of the error-resilience test failed for exactly that reason —
the guard was working, and the test was wrong.

---

## M3 — GUI and manual harness

**Goal:** the same object the user runs, driven by tests.

| Task | Notes |
| --- | --- |
| `CheskiApp` layout | Target picker, trigger box, timing box, controls, status box, log pane. |
| `root.after(150 ms)` pump | The only bridge from worker to widgets. |
| Command preview label | Shows the literal command that will run. |
| Big red emergency abort | Always enabled, never disabled. |
| `tests/manual/title_simulator.py` | A window that does nothing but change its own title bar. |

**Acceptance criteria**
* The app constructs against a fake source with a real (withdrawn) Tk root.
* Start → Arm → Fire → Abort is exercised end to end with a recording runner.
* `python -m cheski --help` and `--version` work.

**Evidence**
```
python -m pytest tests/test_ui_smoke.py     # 12 tests, real Tk root
python -m cheski --version                  # Cheski Auto Shutdown 1.0.0
```

---

## M4 — Safety hardening

**Goal:** make the failure modes imaginable, then make them impossible.

| Task | Notes |
| --- | --- |
| `warming` state | Refuses to fire on a trigger already present at Start. |
| Dwell checks | Absorbs `100%` ⇄ `Verifying` flapping. |
| Countdown dialog, topmost, with the force-close warning | States that Windows will force-close apps at zero. |
| Audible alert | `winsound.MessageBeep`, best-effort. |
| Dry-run default on first launch | Rehearsal before reality. |
| Settings persistence incl. last target | Re-attach by process name after a restart. |
| Abort on window close during countdown | Never leave an unseeable scheduled shutdown. |
| `--dry-run`, `CHESKI_DRY_RUN=1` | Suited to a supervised trial, and to CI. |

**Acceptance criteria**
* Starting with the title already at `100%` never fires (test:
  `test_already_finished_download_never_fires`).
* Closing during the countdown produces a `shutdown /a` call
  (`test_closing_while_triggered_cancels_the_pending_shutdown`).
* Every countdown value is clamped, and the preview always matches the argv.

---

## M5 — Verification

| Check | Command | Result |
| --- | --- | --- |
| Full suite | `python -m pytest` | **139 passed** |
| Byte-compile | `python -m compileall -q cheski tests` | Clean |
| Entry point | `python -m cheski --version` / `--help` | Correct output |
| Live window enumeration | `tests/test_windows.py` integration tests | Real windows enumerated, handles verified alive |
| Live end-to-end probe | `python tests/manual/live_probe.py` | **LIVE PROBE PASSED** — real window, real title read, real match, guard held |
| GUI startup | `tests/test_ui_smoke.py` | Real Tk root, full arm → fire → abort path |
| GUI playtest | throwaway driver, since deleted | Real clicks/keystrokes against the shipping app; 7 defect classes found and fixed, each now locked by a regression test (see [04 — Test plan](04-TEST-PLAN.md#10-gui-playtest-and-what-it-found)) |

---

## M6 — Supervised live trial (user present)

**Why it is separate:** it is the only step that cannot be automated, because
the point is to observe a real machine powering down.

1. `python tests/manual/title_simulator.py` in one window.
2. `python -m cheski` in another; select *Simulated Downloader*; **dry run on**;
   press *Run scenario*. Expect: Arming → Monitoring → TRIGGERED, a logged
   command, and no shutdown.
3. Repeat with dry run **off** and the countdown at 60 s. Press
   **EMERGENCY CANCEL** at ~30 s. Expected: `shutdown /a` is accepted, status
   becomes *Shutdown aborted*, the machine stays on.
4. Repeat once more and let the countdown finish, with nothing unsaved open.

Step 3 is the one that must pass before the tool is trusted unattended.

---

## Deferred by design

Recorded so that the omissions are deliberate rather than forgotten. Details
and designs in [05 — Roadmap](05-ROADMAP.md).

* Tray icon (needs `pystray`)
* Network-idle detection (needs `psutil`)
* Multi-target groups
* Packaging / installer
* Non-Windows backends
* Autostart on login
