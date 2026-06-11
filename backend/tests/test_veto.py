"""Tests for the distinct-prob Bo formula and the veto simulator."""
from __future__ import annotations

import random

from pickem.ratings.glicko2 import Rating
from pickem.sim.bo import series_win_prob, series_win_prob_distinct
from pickem.sim.veto import VetoModel


def test_distinct_matches_constant_case():
    # Equal per-map probs must equal the constant-p Bo formula.
    for p in (0.3, 0.5, 0.6, 0.75):
        assert abs(series_win_prob_distinct([p, p, p]) - series_win_prob(p, 3)) < 1e-9
        assert abs(series_win_prob_distinct([p]) - p) < 1e-12


def test_distinct_known_value():
    # P(win Bo3) with maps [0.8, 0.4, 0.6] in play order.
    p1, p2, p3 = 0.8, 0.4, 0.6
    expected = p1 * p2 + p1 * (1 - p2) * p3 + (1 - p1) * p2 * p3
    assert abs(series_win_prob_distinct([p1, p2, p3]) - expected) < 1e-12


def _toy_model(tau=0.2, side_bump=0.0):
    pool = ["m1", "m2", "m3", "m4", "m5", "m6", "m7"]
    # Team 1 strong on m1; team 2 strong on m2. Equal elsewhere.
    def rat(strong):
        return {m: Rating(1700 if m == strong else 1500) for m in pool}
    ratings = {1: rat("m1"), 2: rat("m2")}
    # Affinity: each team most wants its strong map, dislikes opponent's.
    aff = {
        1: {m: (2.0 if m == "m1" else (-2.0 if m == "m2" else 0.0)) for m in pool},
        2: {m: (2.0 if m == "m2" else (-2.0 if m == "m1" else 0.0)) for m in pool},
    }
    return VetoModel(pool, ratings, aff, lam=0.5, tau=tau, side_bump=side_bump)


def test_simulate_played_shapes():
    m = _toy_model()
    rng = random.Random(0)
    assert len(m.simulate_played(1, 2, 1, rng)) == 1          # Bo1: one map
    bo3 = m.simulate_played(1, 2, 3, rng)
    assert len(bo3) == 3                                       # Bo3: three maps
    assert {pick for _, pick in bo3} <= {1, 2, None}
    assert bo3[2][1] is None                                  # decider has no picker


def test_bans_remove_opponents_best_map():
    """With low temperature, each team's signature map gets vetoed by the other."""
    m = _toy_model(tau=0.1)
    rng = random.Random(0)
    pool_prob = m.pool_probabilities(1, 2, 3, rng, 2000)
    # m1 (team1 strong) and m2 (team2 strong) should rarely survive to be played.
    assert pool_prob["m1"] < 0.15
    assert pool_prob["m2"] < 0.15


def test_side_bump_helps_picker():
    base = _toy_model(side_bump=0.0)
    bumped = _toy_model(side_bump=0.1)
    rng1, rng2 = random.Random(3), random.Random(3)
    p0 = base.series_prob(1, 2, 3, rng1, 3000)
    p1 = bumped.series_prob(1, 2, 3, rng2, 3000)
    assert p1 >= p0           # giving the picker a bump can't hurt the team overall
