# Cheski Auto Shutdown — documentation

This folder is the written record of the project: what it is, why it is built
this way, how it is put together, how it was built, and what comes next.

| Document | Read it when you want to know… |
| --- | --- |
| [01 — Product plan](01-PRODUCT-PLAN.md) | What the tool does, who it is for, what "done" means, and what is deliberately out of scope. |
| [02 — Architecture](02-ARCHITECTURE.md) | How the pieces fit together: modules, threads, state machine, and every safety rule. |
| [03 — Execution plan](03-EXECUTION-PLAN.md) | How the build was sequenced into milestones, what each milestone had to prove, and the current status. |
| [04 — Test plan](04-TEST-PLAN.md) | What is tested, how the suite is kept from ever shutting down a machine, and how to run an end-to-end trial. |
| [05 — Roadmap](05-ROADMAP.md) | v2 ideas: tray icon, network-idle detection, multi-target groups, packaging, non-Windows. |

## Status at a glance

* **Version:** 1.0.0
* **Milestones M0–M5:** complete
* **Test suite:** 139 tests, all passing (`python -m pytest`)
* **Playtest:** driven through the real GUI (clicks, keystrokes, real second process); its findings and fixes are in [04 — Test plan](04-TEST-PLAN.md#10-gui-playtest-and-what-it-found)
* **Platform:** Windows, Python 3.11+ (developed against 3.12.10)
* **Runtime dependencies:** `pygetwindow` only

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
python -m cheski --dry-run
```
