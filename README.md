# Cheski Auto Shutdown

Leave the PC downloading overnight; let it power itself off when the download
finishes.

Cheski Auto Shutdown watches the **title bar of the window you pick — and every
other window owned by the same app**. When a trigger word (`100%`,
`complete`, …) appears, the PC is shut down after a 60-second countdown you can
abort. No AI, no screenshots, no CPU load — reading a title bar is a few
microseconds of work.

## Why process-wide watching matters

Some downloaders never put progress in their main window's title. IDM is the
classic case: its title stays `Internet Download Manager 6.43` forever, and the
`Download complete` message appears in a **separate popup window** that did not
exist when you pressed Start. A watcher locked to one window can never see it.

Cheski therefore reads the **union of every window title owned by the target's
process** on each check. When that popup appears, it belongs to the same
process as your target, so its title is seen the instant it pops. You can turn
this off with the **⤢ watch all windows of the app** toggle if you want the
old exact-window behavior.

And for apps that never publish progress in *any* title bar — qBittorrent and
Steam are the classic cases — there is **⇵ network idle mode**: it watches the
target process's activity (bytes written + read) and fires when the app
**transferred data and then sat at zero** for the idle limit (default 5
minutes). Two-part rule, so it is safe: an app that never transfers can never
fire, and a stall inside the limit just resumes.

## Requirements

* Windows (the tool drives `shutdown.exe`)
* Python 3.11+ with tkinter (bundled with the python.org and Microsoft Store
  installers)
* `pygetwindow` and `psutil` (installed by the requirements file)

```
python -m pip install -r requirements.txt
```

## Quickstart

1. Start the download (Steam, Epic, IDM, Chrome, qBittorrent, …).
2. Launch Cheski — double-click `run_cheski.bat`, or:

   ```
   python -m cheski
   ```

3. In **Window to watch**, find the app in the process list (the search box
   filters by name) and click it.
4. Leave the **trigger words** as they are — the six suggested words
   (`100%`, `complete`, `finished`, `done`, `downloaded`, `seeding`) already
   cover IDM's `Download complete` and almost every downloader's finish text.
   Matching is a case-insensitive substring test: `complete` matches both
   `Download complete` and `completed`.
   *Watching qBittorrent, Steam, or another app with no title progress? Turn
   on **⇵ network idle mode** instead — trigger words are then optional.*
5. Your first launch starts in **Dry run mode** (badge says SAFE): the
   shutdown command is logged, not executed. Press **Start monitoring** and
   watch the log.
6. When you have seen a full run, flip the banner to **LIVE** and leave it
   running. From now on a real countdown starts when the trigger appears.

### How the tool decides to fire

* Every `Check interval` seconds (default 0.5–2) it reads the titles.
* A trigger word must be present for `Confirm passes` consecutive checks
  (default 3) before firing — a title that flashes past in one poll is
  ignored on purpose.
* **The trigger must clear once after Start before a match counts.** If the
  title already says `100%` when you press Start, Cheski sits in *Arming*
  and refuses to fire until it sees the trigger disappear and come back.
  This is what makes an accidental Start harmless.
* With **⇵ network idle mode** on, it also samples the target process's
  disk activity each poll. Firing needs activity seen first, then zero for
  the whole idle limit (the `quiet` card next to the toggle, 30–1800 s,
  default 300). A download that stalls resumes; an app that never wrote
  anything never fires.

## The interface

| Control | What it does |
| --- | --- |
| **Process picker** | Searchable list of running apps with icon, process name, PID and memory. The ⟳ button rescans. |
| **Trigger words** | Chips. Type and press Enter to add; ✕ on a chip removes it. The `suggested:` row adds missing words with one click. The ANY/ALL switch picks whether one word is enough or every word must appear; the `Aa` button toggles case sensitivity. |
| **Check interval / Confirm passes / Grace period** | The three timing cards. Grace period is the abort window in seconds (15–600, default 60); it pulses amber below 30. |
| **Dry run mode banner** | SAFE (teal) logs the command; LIVE (amber) really schedules it. |
| **Status ring** | Idle / Active / Confirmed / Executing, with the watched window, its last title, and a big red `mm:ss` countdown when a shutdown is pending. |
| **⤢ / ⇵ toggles** | `⤢` watches all windows of the app (default on). `⇵` enables network idle mode with its `quiet` seconds card — the trigger for apps with no title progress. |
| **⬡ Abort shutdown** | The red pill. Cancels the pending Windows shutdown immediately (`shutdown /a`). |
| **Event log** | Timestamped record of every poll event, with copy-all and auto-scroll lock. |

The **⤢ watch all windows of the app** toggle above the dry-run banner keeps
the process-wide watching described above on (default) or off.

## Status meanings

| Status | Meaning |
| --- | --- |
| Idle | Not monitoring. |
| Arming | The trigger is already visible. Cheski refuses to fire until it clears once. |
| Monitoring | Armed and watching. |
| TRIGGERED | A shutdown has been scheduled; the red countdown is running. |
| Shutdown aborted | Nothing is scheduled. |

## Safety notes

* **Windows force-closes apps at zero.** `shutdown /s /t 60` implies `/f`
  whenever the timer is above zero, so save your work if you are still at the
  PC. Cheski says so in the countdown.
* **Abort is always available** during the countdown — the red pill, or
  `shutdown /a` from any terminal.
* **Closing the Cheski window during a countdown cancels the pending
  shutdown**, rather than leaving a scheduled shutdown you cannot see.
* A vanished window never fires. If the target disappears, Cheski waits for
  it (or re-attaches to a new window of the same process) and keeps watching.
* Closing Cheski never kills your download; it only stops watching.

## Command line

```
python -m cheski [--dry-run] [--trigger WORD] [--seconds 60] [--interval 2]
```

| Flag | Effect |
| --- | --- |
| `--dry-run` | Log the command instead of executing it; locks the banner on SAFE. |
| `--trigger` | Trigger word. Repeat the flag or pass a comma-separated list. |
| `--seconds` | Countdown length, 15–600 (default 60). |
| `--interval` | Seconds between title checks, 0.5–60 (default 2). |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

Settings are remembered in `%APPDATA%\CheskiAutoShutdown\config.json`, and
every run is appended to `%APPDATA%\CheskiAutoShutdown\logs\cheski.log` so an
unattended night can be checked afterwards.

## Known limitations

* Windows only.
* Some apps show download progress only in a notification bubble, never in
  any window title; use **⇵ network idle mode** for those (qBittorrent, some
  Steam builds). Seeding keeps disk reads alive, so with torrents the idle
  timer effectively waits for seeding to stop too — raise the limit or turn
  the toggle off if you want the PC down the moment the transfer ends.
* Windows Store / UWP apps report `ApplicationFrameHost.exe` as their process,
  so process-wide watching and re-attach are less reliable for them.
* The tool aborts a pending shutdown but cannot stop one whose timer already
  ran out.
