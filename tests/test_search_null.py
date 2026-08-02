"""The null-corrected search must find nothing in nothing, and something in something.

Both directions are pinned. A test that only checked the second would pass for a
procedure that declares every search a success, which is precisely the failure
mode this experiment exists to catch.
"""
from __future__ import annotations

import importlib.util
import pathlib

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "search_null", ROOT / "scripts" / "research" / "search_null.py")


@pytest.fixture(scope="module")
def sn():
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def _world(rng, signal_strength: float, k=8, t=40, n=400):
    scores = rng.normal(size=(k, t, n))
    forward = rng.normal(0.02, 0.18, size=(t, n))
    if signal_strength:
        forward += signal_strength * scores[0]
    return scores, forward


def _search(sn, scores, forward, rng, samples=600, worlds=200, top_n=8):
    ranked = sn._rank_normalise(scores)
    k, t, n = ranked.shape
    weights = sn._sample_weights(np.random.default_rng(7), samples, k)
    composite = (weights @ ranked.reshape(k, -1)).reshape(-1, t, n)
    picks = sn._select(composite, top_n)

    best = float(np.nanmax(sn.evaluate(picks, forward)))
    nulls = []
    for _ in range(worlds):
        shuffled = forward.copy()
        for i in range(t):
            shuffled[i] = shuffled[i][rng.permutation(n)]
        nulls.append(np.nanmax(sn.evaluate(picks, shuffled)))
    return best, np.array(nulls)


def test_a_large_search_finds_a_large_edge_in_pure_noise(sn):
    """The whole reason the null exists, stated as an assertion.

    With no relationship between score and outcome, the best of several hundred
    combinations still comes back in the high single digits of annual excess
    return — the same order as the +6.6 pp/yr this repo once believed it had
    found. Comparing a maximum against zero measures how hard you searched.
    """
    rng = np.random.default_rng(1)
    scores, forward = _world(rng, signal_strength=0.0)
    best, nulls = _search(sn, scores, forward, rng)

    assert best > 0.04, "a search this size should find a flattering number in noise"
    assert np.median(nulls) > 0.04, "and the null must reproduce that, or it is broken"
    assert (nulls < best).mean() < 0.95, "noise must not be certified as a finding"


def test_a_genuine_signal_clears_its_own_null(sn):
    """The correction must not be so conservative that it rejects everything."""
    rng = np.random.default_rng(2)
    scores, forward = _world(rng, signal_strength=0.05)
    best, nulls = _search(sn, scores, forward, rng)

    assert (nulls < best).mean() > 0.99
    assert best > np.percentile(nulls, 99)


def test_shuffling_leaves_the_benchmark_untouched(sn):
    """Excess is measured against equal weight, which the permutation preserves.

    If the shuffle moved the benchmark, real and null would be measured against
    different yardsticks and the comparison would be meaningless.
    """
    rng = np.random.default_rng(3)
    _, forward = _world(rng, 0.0)
    shuffled = forward.copy()
    for i in range(shuffled.shape[0]):
        shuffled[i] = shuffled[i][rng.permutation(shuffled.shape[1])]

    assert np.allclose(np.nanmean(forward, axis=1), np.nanmean(shuffled, axis=1))


def test_rank_normalisation_puts_every_signal_on_one_scale(sn):
    """Otherwise one 900% momentum print dominates any weighted sum."""
    scores = np.zeros((2, 1, 100))
    scores[0, 0] = np.arange(100.0)
    scores[1, 0] = np.arange(100.0) * 1e6      # same order, absurd magnitude
    ranked = sn._rank_normalise(scores)

    assert np.allclose(ranked[0], ranked[1])
    assert np.nanmin(ranked) == 0.0 and np.nanmax(ranked) == 1.0


def test_a_date_with_too_few_names_is_skipped_not_guessed(sn):
    scores = np.full((1, 2, 100), np.nan)
    scores[0, 0, :10] = np.arange(10.0)        # only ten names — not enough
    scores[0, 1, :] = np.arange(100.0)

    ranked = sn._rank_normalise(scores)
    assert np.isnan(ranked[0, 0]).all()
    assert not np.isnan(ranked[0, 1]).any()


def test_every_signal_in_the_library_has_a_published_basis(sn):
    """Not a fishing expedition over whatever an indicator library exposes."""
    import inspect

    source = inspect.getsource(sn)
    for citation in ("Jegadeesh", "Frazzini", "George & Hwang",
                     "McLean & Pontiff", "Hou, Xue & Zhang", "Harvey"):
        assert citation in source, f"{citation} missing from the reasoning"


def test_a_winner_that_loses_to_noise_out_of_sample_is_called_out(sn):
    """The first run needed two tables put side by side to see this."""
    import inspect

    source = inspect.getsource(sn.main)
    assert "held < null_median" in source
    assert "BELOW what this" in source


def test_the_percentile_reports_its_own_monte_carlo_error(sn):
    """98.4 from 1000 worlds is 16 counts, so it carries about ±0.4."""
    import inspect

    source = inspect.getsource(sn.main)
    assert "pct_se" in source and "null worlds matched or beat it" in source


def test_the_universe_is_index_membership_not_the_dropped_list(sn):
    """The first run searched the 236 names that LEFT the index — the failures."""
    import inspect

    source = inspect.getsource(sn.build_panel)
    assert "by_year" in source
    assert "dropped_names" not in source.split('"""')[2], \
        "dropped_names must not be a symbol source again"


def test_selection_is_restricted_to_members_on_the_day(sn):
    """Buying a 2016 name that only joined in 2023 selects for having succeeded."""
    import inspect

    source = inspect.getsource(sn.build_panel)
    assert "eligible" in source
    assert "np.where(eligible[t], row, np.nan)" in source


def test_an_empty_basket_does_not_become_a_silent_nan_average(sn):
    """nanmean of an empty slice warns and returns NaN; counting says so plainly."""
    import numpy as np

    forward = np.array([[np.nan, np.nan, np.nan], [0.10, 0.20, 0.30]])
    picks = np.array([[[0, 1], [0, 1]]])          # one combination, two dates
    out = sn.evaluate(picks, forward)

    # Date 0 is unpriceable and contributes nothing; date 1 is +15% against a
    # +20% benchmark, so the annualised excess is 4 x (-5pp).
    assert np.isclose(out[0], (0.15 - 0.20) * 4)
