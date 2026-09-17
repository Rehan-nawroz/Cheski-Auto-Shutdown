"""Unit tests for the Terminal Precision theme module (headless, hidden root)."""

from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from cheski.ui import theme


@pytest.fixture(scope="module")
def root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless environments
        pytest.skip(f"Tk is not available here: {exc}")
    root.withdraw()
    yield root
    root.destroy()


def test_blend_endpoints_and_midpoint():
    assert theme.blend("#000000", "#ffffff", 0.0) == "#000000"
    assert theme.blend("#000000", "#ffffff", 1.0) == "#ffffff"
    assert theme.blend("#000000", "#ffffff", 0.5) == "#808080"


def test_blend_clamps_out_of_range_t():
    assert theme.blend("#102030", "#ffffff", 5.0) == "#ffffff"
    assert theme.blend("#102030", "#ffffff", -1.0) == "#102030"


def test_with_alpha_composites_over_the_surface():
    assert theme.with_alpha(theme.PRIMARY_CONTAINER, 0.0) == theme.SURFACE
    assert theme.with_alpha(theme.PRIMARY_CONTAINER, 1.0) == theme.PRIMARY_CONTAINER


def test_init_fonts_resolves_every_token(root):
    theme.init_fonts(root)
    for token in ("display", "title", "body", "body_small", "mono_data",
                  "label_caps", "timer", "timer_idle"):
        assert theme.font(token), token
    assert theme.mono_family()


def test_apply_ttk_theme_is_idempotent(root):
    theme.apply_ttk_theme(root)
    theme.apply_ttk_theme(root)  # must not raise on the second call
    from tkinter import ttk

    assert ttk.Style(root).theme_use() == "cheski"
