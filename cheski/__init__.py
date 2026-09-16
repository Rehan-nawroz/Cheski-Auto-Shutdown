"""Cheski Auto Shutdown - automatic PC shutdown when a download finishes.

The tool watches the *title bar* of a user-chosen window and triggers a
delayed, cancellable shutdown once a trigger word appears.  See ``docs/`` for
the full product, architecture and execution plans.

Nothing in this package ever shuts the machine down unless
``PowerController.dry_run`` is False, and tests inject a fake command runner
so the real ``shutdown.exe`` is never touched.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
