# Cheski Auto Shutdown

Leave the PC downloading overnight; let it power itself off when the download
finishes.

Cheski Auto Shutdown watches the **title bar** of a window you choose. When a
trigger word (``100%``, ``Complete``, …) appears in it, the PC is shut down
after a cancellable countdown. No AI, no screenshots, no CPU load — reading a
title bar is a few microseconds of work.

Full plans and design notes live in [`docs/`](docs/README.md).

## Requirements

* Windows (the tool drives `shutdown.exe`)
* Python 3.11+ with tkinter (bundled with the python.org and Microsoft Store
  installers)
* `pygetwindow`

```
python -m pip install -r requirements.txt
```

## Quickstart

1. Start the download (Steam, Epic, Chrome, qBittorrent, …).
2. Launch Cheski — double-click `run_cheski.bat`, or:

   ```
   python -m cheski
   ```

3. Press **Refresh list** and pick the downloading app.
4. Leave the trigger words as `100%` or type your own.
5. Tick **Dry run** the first time. Press **Start monitoring** and watch the
   Status box.
6. When you are happy, untick **Dry run** and leave it running.

The Status box shows one of:

| Status | Meaning |
| --- | --- |
| Arming | The trigger is already in the title bar. Cheski refuses to fire until it sees the trigger disappear once. |
| Monitoring | Armed and watching. |
| TRIGGERED | A shutdown has been scheduled; the countdown window is up. |
| Shutdown aborted | Nothing is scheduled. |

## Safety notes

* **Windows force-closes apps at zero.** `shutdown /s /t 60` implies the `/f`
  flag whenever the timer is greater than zero, so save your work if you are
  still at the PC. Cheski says so in the countdown window.
* **The trigger has to clear once before it can fire.** Starting Cheski while
  the title bar already reads `100%` will not shut the PC down; it waits for
  the trigger to disappear and come back. This is what makes an accidental
  click on Start harmless.
* **Dry run** logs the exact command instead of running it. It is on for the
  first launch, and can be forced with `--dry-run` or `CHESKI_DRY_RUN=1`.
* **Closing the window during the countdown cancels the pending shutdown**,
  rather than leaving a scheduled shutdown you cannot see.
* Closing Cheski never kills your download; it only stops watching.

## Command line

```
python -m cheski [--dry-run] [--trigger WORD] [--seconds 60] [--interval 2]
```

| Flag | Effect |
| --- | --- |
| `--dry-run` | Log the command instead of executing it; locks the checkbox on. |
| `--trigger` | Trigger word. Repeat the flag or pass a comma-separated list. |
| `--seconds` | Countdown length, 15–600 (default 60). |
| `--interval` | Seconds between title checks, 0.5–60 (default 2). |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

Settings are remembered in `%APPDATA%\CheskiAutoShutdown\config.json`, and every
run is appended to `%APPDATA%\CheskiAutoShutdown\logs\cheski.log` so an
unattended night can be checked afterwards.

## Testing

```
python -m pip install -r requirements-dev.txt
python -m pytest
```

The suite never touches the real `shutdown.exe`: tests inject a recording
command runner, and an autouse guard fails any test that tries to launch it.

For a hands-on end-to-end run, start the fake downloader in
[`tests/manual/title_simulator.py`](tests/manual/title_simulator.py) — it is a
window that does nothing but change its own title bar, so the whole flow can be
tested in seconds instead of hours.

`python tests/manual/live_probe.py` is a scripted version of the same idea
against the real desktop: it opens a real window, watches its real title bar and
verifies both that a genuine `100%` fires and that an already-finished title
does not. It never imports the power layer, so nothing can be shut down.

## Known limitations

* Windows only.
* Windows Store / UWP apps report `ApplicationFrameHost.exe` as their process,
  so automatic re-attach after a window is recreated is less reliable for them.
* Some apps (notably browsers) show download progress in a bubble instead of
  the title bar; those need a trigger that they really do put in the title.
* The tool shuts the PC down but cannot stop a shutdown that has already run
  out its timer.
