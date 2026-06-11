"""Tests for the Glicko-2 core, including Glickman's worked example."""
from __future__ import annotations

import math

from pickem.ratings.glicko2 import Rating, Result, rate, win_probability


def test_glickman_reference_example():
    """Reproduce the canonical example from Glickman's Glicko-2 paper.

    Player (1500, RD 200) plays three opponents and wins/loses/loses;
    expected output is rating ~1464.06, RD ~151.52, vol ~0.05999.
    """
    player = Rating(1500, 200, 0.06)
    results = [
        Result(Rating(1400, 30, 0.06), 1.0),
        Result(Rating(1550, 100, 0.06), 0.0),
        Result(Rating(1700, 300, 0.06), 0.0),
    ]
    new = rate(player, results)
    assert math.isclose(new.rating, 1464.06, abs_tol=0.1)
    assert math.isclose(new.rd, 151.52, abs_tol=0.5)
    assert math.isclose(new.vol, 0.05999, abs_tol=1e-4)


def test_no_games_inflates_rd_only():
    r = Rating(1600, 80, 0.06)
    new = rate(r, [])
    assert new.rating == 1600
    assert new.rd > 80           # uncertainty grows
    assert new.rd <= 350         # but stays capped


def test_win_probability_symmetry_and_bounds():
    a, b = Rating(1800, 50), Rating(1500, 50)
    pab = win_probability(a, b)
    pba = win_probability(b, a)
    assert pab > 0.5 > pba
    assert math.isclose(pab + pba, 1.0, abs_tol=1e-9)


def test_higher_rating_favored():
    strong, weak = Rating(2000, 60), Rating(1400, 60)
    assert win_probability(strong, weak) > 0.8


def test_weight_scales_rating_movement():
    """A weighted win should move the rating more than an unweighted one."""
    base = Rating(1500, 200, 0.06)
    opp = Rating(1500, 200, 0.06)
    light = rate(base, [Result(opp, 1.0, weight=0.25)])
    heavy = rate(base, [Result(opp, 1.0, weight=1.0)])
    assert heavy.rating > light.rating > 1500
