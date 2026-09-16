"""Trigger engine tests.

These are the highest-value tests in the project: every one of them encodes a
way the app could either shut a PC down by mistake, or silently never fire.
"""

from __future__ import annotations

import pytest

from cheski.triggers import ALL, ANY, TriggerConfig, TriggerEngine, parse_triggers


def make_engine(
    triggers=("100%",),
    *,
    require_transition: bool = False,
    dwell: int = 1,
    mode: str = ANY,
    case_sensitive: bool = False,
) -> TriggerEngine:
    """Engine with the transition guard off unless a test asks for it."""
    engine = TriggerEngine(
        TriggerConfig(
            triggers=tuple(triggers),
            match_mode=mode,
            case_sensitive=case_sensitive,
            dwell_checks=dwell,
            require_transition=require_transition,
        )
    )
    engine.reset()
    return engine


# -- parsing -----------------------------------------------------------


def test_parse_triggers_splits_on_any_separator_and_dedupes():
    assert parse_triggers("100%, complete;100%|Finished\nDone") == (
        "100%",
        "complete",
        "Finished",
        "Done",
    )


def test_parse_triggers_accepts_an_iterable():
    assert parse_triggers(["100%, complete", "Finished"]) == ("100%", "complete", "Finished")


@pytest.mark.parametrize("raw", [None, "", "   ", ",,;", []])
def test_parse_triggers_returns_empty_for_nothing(raw):
    assert parse_triggers(raw) == ()


def test_parse_triggers_collapses_internal_whitespace():
    assert parse_triggers("  download   complete  ") == ("download complete",)


# -- matching ----------------------------------------------------------


def test_plain_substring_match():
    assert make_engine().evaluate("Steam - Downloading 100%").matched is True


def test_matching_is_case_insensitive_by_default():
    assert make_engine(["complete"]).evaluate("Steam - Download COMPLETE").matched is True


def test_case_sensitive_mode_rejects_different_case():
    engine = make_engine(["complete"], case_sensitive=True)
    assert engine.evaluate("Steam - Download COMPLETE").matched is False
    assert make_engine(["COMPLETE"], case_sensitive=True).evaluate("Download COMPLETE").matched


def test_spaceless_trigger_matches_title_with_a_space():
    """Real titles render '100 %' -- that must not be a silent miss."""
    assert make_engine(["100%"]).evaluate("Steam - Downloading (100 %)").matched is True


def test_fullwidth_characters_are_normalised():
    """NFKC folds '１００％' to '100%'."""
    assert make_engine(["100%"]).evaluate("下载 １００％").matched is True


def test_trusted_prefix_is_not_enough():
    """A title that merely contains a number near 100 must not fire."""
    assert make_engine(["100%"]).evaluate("Downloading 1000% of extras").matched is False
    assert make_engine(["100%"]).evaluate("Downloading 199%").matched is False


def test_trigger_containing_a_space_matches_collapsed_title():
    engine = make_engine(["download complete"])
    assert engine.evaluate("Steam - Download    Complete").matched is True


def test_empty_trigger_never_matches():
    engine = make_engine([], require_transition=False)
    assert engine.evaluate("anything at all").matched is False


# -- any / all ---------------------------------------------------------


def test_any_mode_fires_on_the_first_word():
    engine = make_engine(["Downloading", "100%"], mode=ANY)
    result = engine.evaluate("Downloading 47%")
    assert result.matched is True
    assert result.hits == ("Downloading",)


def test_all_mode_requires_every_word():
    engine = make_engine(["Downloading", "100%"], mode=ALL)
    partial = engine.evaluate("Downloading 47%")
    assert partial.matched is False
    assert partial.missing == ("100%",)
    assert engine.evaluate("Downloading 100%").matched is True


def test_all_mode_is_not_satisfied_by_no_triggers():
    assert make_engine([], mode=ALL).evaluate("Downloading 100%").matched is False


# -- the transition guard (the anti-catastrophe rule) ------------------


def test_already_matching_title_never_fires_with_the_guard_on():
    """Clicking Start on a finished download must not shut the PC down."""
    engine = make_engine(["100%"], require_transition=True, dwell=1)
    for _ in range(10):
        result = engine.evaluate("Steam - Downloading 100%")
        assert result.matched is False
    assert engine.armed is False
    assert "already present" in engine.evaluate("Steam - Downloading 100%").reason


def test_guard_arms_once_the_trigger_clears_then_fires():
    engine = make_engine(["100%"], require_transition=True, dwell=1)
    assert engine.evaluate("Steam - Downloading 100%").matched is False  # waiting
    assert engine.evaluate("Steam - Downloading 12%").matched is False  # clears -> armed
    assert engine.armed is True
    assert engine.evaluate("Steam - Downloading 100%").matched is True


def test_guard_ignored_when_disabled():
    engine = make_engine(["100%"], require_transition=False, dwell=1)
    assert engine.evaluate("Steam - Downloading 100%").matched is True


# -- dwell -------------------------------------------------------------


def test_dwell_requires_consecutive_matches():
    engine = make_engine(["100%"], dwell=3)
    assert engine.evaluate("Downloading 100%").matched is False
    assert engine.streak == 1
    assert engine.evaluate("Downloading 100%").matched is False
    assert engine.streak == 2
    assert engine.evaluate("Downloading 100%").matched is True


def test_dwell_streak_resets_when_the_title_flaps():
    """Titles that bounce to 'Verifying' and back must not accumulate."""
    engine = make_engine(["100%"], dwell=3)
    engine.evaluate("Downloading 100%")
    engine.evaluate("Downloading 100%")
    engine.evaluate("Verifying installation")  # flap
    result = engine.evaluate("Downloading 100%")
    assert result.matched is False
    assert engine.streak == 1


# -- latching ----------------------------------------------------------


def test_engine_latches_after_firing():
    engine = make_engine(["100%"], dwell=1)
    assert engine.evaluate("Downloading 100%").matched is True
    assert engine.fired is True
    follow_up = engine.evaluate("Downloading 100%")
    assert follow_up.matched is False
    assert "Already fired" in follow_up.reason


def test_reset_allows_monitoring_again():
    engine = make_engine(["100%"], dwell=1)
    engine.evaluate("Downloading 100%")
    assert engine.fired is True
    engine.reset()
    assert engine.fired is False
    assert engine.streak == 0
    assert engine.evaluate("Downloading 100%").matched is True


# -- not-armed reporting ----------------------------------------------


def test_not_matching_reports_missing_words_and_arms():
    engine = make_engine(["Downloading", "100%"], mode=ALL, require_transition=True)
    result = engine.evaluate("Steam - Library")
    assert result.matched is False
    assert result.missing == ("Downloading", "100%")
    assert engine.armed is True
