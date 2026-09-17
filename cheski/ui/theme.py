"""The Terminal Precision theme: palette, typography, and widget styling.

One home for every visual constant the new UI uses, transcribed from the
design system (``colors``/``typography``/``rounded``/``spacing``), plus the
small amount of engineering the medium forces:

* **Fonts** — the design names Geist / Inter / JetBrains Mono, none of which
  ship with Windows, so each resolves through a fallback chain and the
  resolved family is published (``mono_family`` etc.) for canvases that need
  it.
* **Colour maths** — animated components need intermediate colours, so
  :func:`blend` interpolates in RGB.
* **ttk** — stock themes ignore background options, so a private theme
  ("cheski") configures the styles the UI actually uses; anything needing
  pixel control (rings, pills, chips, terminal) is drawn on Canvas instead.

This module imports tkinter but owns no widgets; it can be exercised
headlessly (one hidden root) and is covered by :mod:`tests.test_ui_theme`.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ---------------------------------------------------------------------------
# Palette — "Terminal Precision" (verbatim token names from the design system)
# ---------------------------------------------------------------------------

SURFACE = "#111317"
SURFACE_DIM = "#111317"
SURFACE_BRIGHT = "#37393e"
SURFACE_LOWEST = "#0c0e12"
SURFACE_CONTAINER_LOW = "#1a1c20"
SURFACE_CONTAINER = "#1e2024"
SURFACE_CONTAINER_HIGH = "#282a2e"
SURFACE_CONTAINER_HIGHEST = "#333539"
ON_SURFACE = "#e2e2e8"
ON_SURFACE_VARIANT = "#bcc9cd"
OUTLINE = "#869397"
OUTLINE_VARIANT = "#3d494c"

PRIMARY = "#4cd7f6"
PRIMARY_CONTAINER = "#06b6d4"
ON_PRIMARY = "#003640"
ON_PRIMARY_CONTAINER = "#00424f"

SECONDARY = "#4fdbc8"
SECONDARY_CONTAINER = "#04b4a2"
ON_SECONDARY = "#003731"

TERTIARY = "#ffb95f"
TERTIARY_CONTAINER = "#e79400"
ON_TERTIARY = "#472a00"

ERROR = "#ffb4ab"
ERROR_CONTAINER = "#93000a"
ON_ERROR_CONTAINER = "#ffdad6"
CRIMSON = "#ef4444"

TEXT_HIGH = "#f8fafc"
TEXT_SECONDARY = "#94a3b8"
TEXT_MUTED = "#475569"

# 8-digit RGBA is invalid in Tk; these are the design's hairline borders
# pre-composited over the app surface (same effect, valid colour strings).
BORDER = "#1e2126"          # white 8%  over SURFACE
BORDER_FOCUS = "#2a2e35"    # white 16% over SURFACE
HEADER_BORDER = "#191c21"   # white 5%  over SURFACE

# ---------------------------------------------------------------------------
# Typography — design tokens resolved through Windows-available fallbacks
# ---------------------------------------------------------------------------

#: (design family, [fallbacks]) — first installed family wins.
FONT_CHAINS = {
    "display": ("Geist", ["Segoe UI", "Arial"]),
    "body": ("Inter", ["Segoe UI", "Arial"]),
    "mono": ("JetBrains Mono", ["Consolas", "Courier New"]),
}

_THEME_CACHE: dict = {}


def _pick_family(chain: tuple[str, list[str]]) -> str:
    """First installed family of ``chain`` (a root is required once)."""
    name, fallbacks = chain
    root = _THEME_CACHE.get("root")
    if root is None:
        return fallbacks[-1]
    installed = _THEME_CACHE.setdefault(
        "installed", {f.lower() for f in tkfont.families(root)}
    )
    for candidate in (name, *fallbacks):
        if candidate.lower() in installed:
            return candidate
    return fallbacks[-1]


def font(token: str) -> tuple:
    """One of the design's font tokens as a tkinter font tuple.

    Tokens: ``display``, ``title``, ``body``, ``body_small``, ``mono_data``,
    ``label_caps``, ``timer``.  Sizes follow the design's px scale (tk picks
    the nearest point size).
    """
    kind = "display"
    if token in _THEME_CACHE:
        kind = _THEME_CACHE[token]
    return _THEME_CACHE["fonts"][kind]


def init_fonts(root: tk.Misc) -> None:
    """Resolve the font chains against ``root`` and build every token.

    Must be called once before :func:`font` / :func:`mono_family`.
    """
    _THEME_CACHE["root"] = root
    resolved = {name: _pick_family(chain) for name, chain in FONT_CHAINS.items()}
    _THEME_CACHE["mono_family"] = resolved["mono"]
    _THEME_CACHE["fonts"] = {
        "display": (resolved["display"], 15, "bold"),
        "title": (resolved["display"], 12, "bold"),
        "body": (resolved["body"], 10),
        "body_small": (resolved["body"], 9),
        "mono_data": (resolved["mono"], 10),
        "label_caps": (resolved["mono"], 9, "bold"),
        "timer": (resolved["mono"], 40, "bold"),
        "timer_idle": (resolved["mono"], 22, "bold"),
    }
    for token, kind in {
        "display": "display", "title": "title", "body": "body",
        "body_small": "body_small", "mono_data": "mono_data",
        "label_caps": "label_caps", "timer": "timer",
        "timer_idle": "timer_idle",
    }.items():
        _THEME_CACHE[token] = kind


def mono_family() -> str:
    return _THEME_CACHE.get("mono_family", "Consolas")


# ---------------------------------------------------------------------------
# Colour maths
# ---------------------------------------------------------------------------


def blend(a: str, b: str, t: float) -> str:
    """Interpolate two ``#rrggbb`` colours; ``t=0`` → ``a``, ``t=1`` → ``b``."""
    ta, tb = a.lstrip("#"), b.lstrip("#")
    ra, ga, ba = int(ta[0:2], 16), int(ta[2:4], 16), int(ta[4:6], 16)
    rb, gb, bb = int(tb[0:2], 16), int(tb[2:4], 16), int(tb[4:6], 16)
    t = max(0.0, min(1.0, t))
    return "#{:02x}{:02x}{:02x}".format(
        round(ra + (rb - ra) * t),
        round(ga + (gb - ga) * t),
        round(ba + (bb - ba) * t),
    )


def with_alpha(hex_color: str, alpha: float) -> str:
    """Blend ``hex_color`` over the app surface — tkinter has no alpha.

    Used where the design calls for e.g. ``rgba(6,182,212,0.1)`` on dark
    surfaces: the same visual effect, pre-composited.
    """
    return blend(SURFACE, hex_color, alpha)


# ---------------------------------------------------------------------------
# Widget styling
# ---------------------------------------------------------------------------


def apply_ttk_theme(root: tk.Misc) -> None:
    """Configure the private ttk theme with the palette.

    Safe to call again (idempotent within a process).
    """
    init_fonts(root)
    style = ttk.Style(root)
    try:
        style.theme_use("cheski")
    except tk.TclError:
        style.theme_create(
            "cheski",
            parent="clam",
            settings={
                "TCombobox": {
                    "configure": {
                        "fieldbackground": "#0f1219",
                        "background": SURFACE_CONTAINER_HIGH,
                        "foreground": TEXT_HIGH,
                        "arrowcolor": TEXT_SECONDARY,
                        "bordercolor": BORDER,
                        "lightcolor": "#0f1219",
                        "darkcolor": "#0f1219",
                        "borderwidth": 1,
                        "relief": "flat",
                        "selectbackground": PRIMARY_CONTAINER,
                        "selectforeground": ON_PRIMARY,
                    }
                },
                "TCombobox.Listboxf": {
                    "configure": {
                        "background": SURFACE_CONTAINER,
                        "foreground": TEXT_HIGH,
                        "selectbackground": PRIMARY_CONTAINER,
                        "selectforeground": ON_PRIMARY,
                    }
                },
                "TNotebook.Tab": {
                    "configure": {
                        "background": SURFACE_CONTAINER,
                        "foreground": TEXT_SECONDARY,
                        "padding": (10, 4),
                        "borderwidth": 0,
                    },
                    "map": {
                        "background": [("selected", SURFACE_CONTAINER_HIGH)],
                        "foreground": [("selected", TEXT_HIGH)],
                    },
                },
            },
        )
        style.theme_use("cheski")

    common = dict(
        background=SURFACE,
        foreground=TEXT_HIGH,
        fieldbackground="#0f1219",
        bordercolor=BORDER,
        lightcolor="#0f1219",
        darkcolor="#0f1219",
        troughcolor=SURFACE_CONTAINER_LOW,
        arrowcolor=TEXT_SECONDARY,
    )
    style.configure(".", **common)
    style.configure("TFrame", background=SURFACE)
    style.configure("Card.TFrame", background=SURFACE_CONTAINER_LOW, relief="flat")
    style.configure("TLabel", background=SURFACE, foreground=TEXT_HIGH)
    style.configure("Card.TLabel", background=SURFACE_CONTAINER_LOW, foreground=TEXT_HIGH)
    style.configure("Dim.TLabel", background=SURFACE, foreground=TEXT_SECONDARY)
    style.configure("Card.Dim.TLabel", background=SURFACE_CONTAINER_LOW, foreground=TEXT_SECONDARY)
    style.configure("Muted.TLabel", background=SURFACE, foreground=TEXT_MUTED)
    style.configure("Card.Muted.TLabel", background=SURFACE_CONTAINER_LOW, foreground=TEXT_MUTED)
    style.configure("TCheckbutton", **common)
    style.map(
        "TCheckbutton",
        background=[("active", SURFACE_CONTAINER)],
    )
    style.configure("TButton", **common)
    style.configure("TSpinbox", **common)
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=SURFACE_CONTAINER_LOW,
        background=PRIMARY_CONTAINER,
        bordercolor=BORDER,
        lightcolor=PRIMARY_CONTAINER,
        darkcolor=PRIMARY_CONTAINER,
    )
    # Colored chips (pills) need flat colours on a canvas-backed look.
    style.configure(
        "Chip.TFrame",
        background=with_alpha(PRIMARY_CONTAINER, 0.14),
        relief="flat",
    )


def init_theme(root: tk.Misc) -> None:
    """One call for the app: fonts + ttk theme."""
    init_fonts(root)
    apply_ttk_theme(root)
