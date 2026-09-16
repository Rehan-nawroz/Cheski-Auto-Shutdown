# 04 — Test plan

## 1. The prime directive

**No test may ever shut down the machine running it.**

Two independent mechanisms enforce that:

1. **Injectable runner.** `PowerController` takes a `runner` callable. Every
   test passes a `RecordingRunner` that records the argv and returns a canned
   `CompletedProcess`. No test constructs a controller without one.
2. **Autouse guard.** `tests/conftest.py::no_real_shutdown` monkeypatches
   `subprocess.run` and raises an `AssertionError` if the first argument
   mentions `shutdown`.

A third, smaller guard: `PowerController.dry_run` is honoured before the runner
is ever consulted, and `tests/conftest.py::no_ambient_dry_run` deletes
`CHESKI_DRY_RUN` from the environment so a developer's shell cannot change what
the suite is testing.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
# 139 passed
```

## 2. Test layers

| Layer | File | Tests | What it pins down |
| --- | --- | --- | --- |
| Trigger logic | `tests/test_triggers.py` | 27 | Matching, normalisation, arming, dwell, latching |
| Command layer | `tests/test_power.py` | 25 | Exact argv, clamping, dry run, return codes, sanitising |
| Window identity | `tests/test_windows.py` | 14 | Filtering, labels, re-attach, live enumeration |
| Monitoring loop | `tests/test_monitor.py` | 9 | Events, loss, re-attach, prompt stop, resilience |
| GUI integration | `tests/test_ui_smoke.py` | 19 | Real Tk root, arm → fire → abort, close-safety, first-launch dry run, duplicate windows, target locking |
| Run-state policy | `tests/test_ui_state.py` | 10 | Tk-free state machine policy: running/live truth table, status text/colour, countdown body |
| Countdown dialog | `tests/test_ui_countdown.py` | 4 | The pending-shutdown window in isolation: contents, tick-to-zero, abort callback, idempotent destroy |
| Settings | `tests/test_config.py` | 31 | Round trip, corruption tolerance, clamping |
| **Total** | | **139** | |

Every defect found by the GUI playtest (§10) has a named regression test in
`tests/test_ui_smoke.py` or `tests/test_windows.py`; that is the durable form of
the playtest's findings.

Fakes replace every external dependency: `FakeWindowSource` for the desktop,
`RecordingRunner` for `shutdown.exe`, a `BoxRecorder` for modal dialogs, and a
monkeypatched `is_window_alive` so results do not depend on which handles
happen to exist on the developer's desktop.

## 3. Safety-critical cases (read these first)

| Test | Property it protects |
| --- | --- |
| `test_already_matching_title_never_fires_with_the_guard_on` | Ten consecutive polls of an already-`100%` title produce no firing. |
| `test_guard_arms_once_the_trigger_clears_then_fires` | …and then a genuine finish *does* fire. |
| `test_already_finished_download_never_fires` | The same guarantee through the real GUI object, including "no command was ever built". |
| `test_lost_window_is_reported_and_never_treated_as_a_trigger` | A vanished window cannot be mistaken for a finished download. |
| `test_dwell_streak_resets_when_the_title_flaps` | `100%` → `Verifying` → `100%` never accumulates to a firing streak. |
| `test_dry_run_never_executes_the_command` | Dry run cannot reach the runner even if the runner explodes. |
| `test_closing_while_triggered_cancels_the_pending_shutdown` | Closing the window cancels rather than orphaning a scheduled shutdown. |
| `test_abort_with_nothing_pending_is_explained` | `shutdown /a` errors are translated, not swallowed. |
| `test_failed_command_reports_not_ok` | A denied shutdown is surfaced, and no countdown dialog is shown. |
| `test_unchanged_titles_are_not_re_logged` | An all-night run cannot fill the log pane one line per poll. |

## 4. Matching rules under test

| Case | Expected |
| --- | --- |
| `100%` in `Steam - Downloading 100%` | Match |
| `100%` in `Downloading (100 %)` | Match (space-free trigger ignores title spaces) |
| `１００％` (full width) with trigger `100%` | Match (NFKC) |
| `100%` in `Downloading 1000%` | **No** match (no contiguous `100%`) |
| `complete` in `Download COMPLETE` | Match (case-insensitive default) |
| `complete` in `COMPLETE` with *exact case* on | No match |
| `download complete` in `Download    Complete` | Match (whitespace collapsed) |
| `Downloading` + `100%` in `Downloading 47%`, mode `all` | No match, `missing == ("100%",)` |
| `Downloading` + `100%` in `Downloading 47%`, mode `any` | Match |
| Empty trigger list | Never matches |

## 5. Determinism rules for the suite

* Wait for **observable events**, never for guessed durations
  (`wait_for(events, kind)`).
* Before flipping a title to the trigger word in a `require_transition=True`
  test, wait for the *armed* `tick`. Otherwise the test is racing the safety
  guard and will flake — this exact mistake was made once during development.
* Monkeypatch liveness rather than assuming a handle number is invalid.
* GUI tests withdraw the Tk root and replace `messagebox`, so nothing appears
  on screen and no modal dialog can block the suite.

## 6. Live probe (verified against the real desktop)

The pytest suite fakes the desktop, so one scripted check runs against the
**real** thing: it opens a genuine Tk window, finds it through `pygetwindow`,
reads its title through `user32!GetWindowTextW`, and lets a real
`MonitorThread` drive a real `TriggerEngine`. It never imports `cheski.power`,
so no shutdown can happen.

```
python tests/manual/live_probe.py
```

Output from this machine:

```
PASS: found the probe window (handle=2884312, pid=9832, process=python3.12.exe)

Phase 1: arm at 10%, then finish at 100%
PASS: engine armed while the title had no trigger
    event: tick              streak=1 armed=True title='Cheski E2E Probe - Downloading 100%'
    event: match             streak=1 armed=True title='Cheski E2E Probe - Downloading 100%'
PASS: real title change produced a real match event

Phase 2: start with the title already at 100% (must NOT fire)
    event: tick              streak=0 armed=False title='Cheski E2E Probe - Downloading 100%'
PASS: already-finished title did not fire

LIVE PROBE PASSED
```

This proves the three things unit tests cannot: window enumeration finds a real
window, per-handle title reads see real title changes, and the arming guard
behaves the same way against a real window as it does against a fake.

## 7. Manual end-to-end rehearsal

The fast path is `tests/manual/title_simulator.py` — a window that exists only
to change its own title bar.

| # | Step | Expected result |
| --- | --- | --- |
| 1 | `python tests/manual/title_simulator.py` | A small window titled `Simulated Downloader - Downloading 0%`. |
| 2 | `python -m cheski`, press **Refresh list** | `Simulated Downloader …` appears in the dropdown. |
| 3 | Tick **Dry run**, press **Start monitoring** | Status `Arming` then `Monitoring`; the preview shows `shutdown /s /t 60 /c "…"`. |
| 4 | Press **Run scenario** in the simulator | Titles step 0% → 12% → 47% → 88% → 99% → 100% → `Complete`. Status becomes `TRIGGERED`, a countdown window appears (labelled as a dry run), the log shows the command and **no shutdown occurs**. |
| 5 | Press **Jump straight to 100%** | Nothing fires. Status stays `Arming`, because the trigger was already present when monitoring started. |
| 6 | Press **Reset to Downloading 10%**, then **Jump straight to 100%** | Status arms, then fires. This is the guard working. |
| 7 | Untick **Dry run**; repeat step 4; press **EMERGENCY CANCEL** | Log shows `shutdown /a` accepted, status `Shutdown aborted`, machine stays on. |
| 8 | Untick **Dry run**; repeat step 4; let the countdown run out | Machine powers off. |

Close the simulator before the countdown expires, unless you are deliberately
testing the lost-target path — in that case the expected result is a logged
warning and **no** shutdown.

## 8. Supervised live trial (the one test that cannot be automated)

Only after step 7 above passes:

1. Save all work. Start the simulator.
2. Run Cheski with a real 60-second countdown.
3. Let it reach `TRIGGERED`, then press **EMERGENCY CANCEL** at roughly 30
   seconds. The machine must stay on and the log must show an accepted
   `shutdown /a`.
4. Repeat without cancelling, with nothing unsaved open.

## 10. GUI playtest (and what it found)

The product was used the way its first real user would: the shipping
`CheskiApp` was driven with real input events — clicks on real buttons, real
keystrokes into the real trigger box and number fields, the real dropdown, a
real second process standing in for the downloader, and real title-bar changes —
with `CHESKI_DRY_RUN=1` as a hard backstop so nothing could power off. Native
message boxes were read as text and then dismissed.

The playtest harness itself was throwaway and has been deleted; the findings
are locked down by the regression tests named below.

| Finding | Severity | Fix | Regression test |
| --- | --- | --- | --- |
| **Refresh list overwrote the user's choice.** `refresh_windows()` reselected the first entry in the list, so pressing Refresh silently moved the target to an unrelated window (the alphabetically-first one) while the panel kept claiming a window was selected. The user would go to sleep watching the wrong window and the PC would simply never power off. | High — silent failure of the product's only promise | Selection is preserved; a window is only auto-selected when a *previous session's* target is recognised | `test_refresh_keeps_the_window_the_user_chose`, `test_app_constructs_without_preselecting_a_window`, `test_remembered_target_is_restored_on_launch` |
| **The panel could disagree with the monitor.** The picker's selection and `MonitorThread.target` were separate state with nothing reconciling them, so the Status box could name one window while the monitor read another. | High | The picker and Refresh are locked for the duration of a run, and a `Watching:` line names the window actually being read | `test_picker_is_locked_while_monitoring` |
| **Start stayed clickable during the countdown**, so a careless click could arm a second monitor on top of a scheduled shutdown. | Medium | Start is disabled while a run is live | `test_picker_is_locked_while_monitoring` (asserts the button state) |
| **Number fields advertised values the app would not use** (`99999` displayed while the command used the clamped `600`), and the command preview never updated when a value was typed. | Medium — misleading feedback | Fields settle to the accepted value when left, and the preview follows typing | `test_typed_numbers_settle_to_the_values_actually_used`, `test_number_fields_are_wired_to_the_preview_and_settle` |
| **The app offered its own window as a target**, which can never fire. | Medium | Own-process windows are excluded from the picker | `test_own_window_is_never_offered_as_a_target` |
| **A vanished target could be replaced by one of our own windows.** Process-name matching would happily pick the tool's own window, leaving it watching the wrong thing all night. | Medium | Re-attach skips windows belonging to our own process | `test_resolve_target_never_re_attaches_to_our_own_process` |
| **After an automatic re-attach the status box still named the old window.** | Low | The re-attach event carries the new handle and the status line follows it | `test_re_attached_window_is_named_in_the_status_box` |
| The test suite wrote "First launch detected" lines into the user's real log file. | Low | `main()` tests no longer install the real log handler | (covered by `test_first_launch_starts_in_dry_run`) |

Verified as working during the playtest, for the record: the empty-trigger error
message; alerting on a title that already contains the trigger instead of firing;
the full arm → match → schedule → countdown → cancel path with the force-close
warning visible in the dialog; four rapid Start clicks producing exactly one
monitor thread; Stop twice from idle; a vanished window producing a warning and
no shutdown; a returning downloader being re-attached by process name; the log
file and config being written correctly; and closing the window stopping the
worker.

## 11. Coverage gaps (known and accepted)

* **No test executes the real `shutdown.exe`.** Deliberate; that is what the
  supervised trial is for.
* **Return codes 0/5/1116/1190 are tested via fakes**, not by provoking the real
  errors. Provoking a real "access denied" requires a locked-down account.
* **UWP window identity** (`ApplicationFrameHost.exe`) is unfixable from user
  space and untested.
* **No automated test of the tkinter countdown rendering** — the countdown
  logic is exercised through state transitions; pixel-level rendering is not.
* **Long-running stability** (8+ hour soak) is not covered by the suite; the
  event-vocabulary tests plus rotating logs are the mitigation.
