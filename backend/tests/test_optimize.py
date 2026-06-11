"""Tests for Pick'Em scoring and the threshold optimizer."""
from __future__ import annotations

from pickem.sim.montecarlo import SimSummary
from pickem.optimize.format import Outcome, PickemFormat, Picks, score
from pickem.optimize.optimizer import StageOptimizer


def test_score_counts_each_category():
    fmt = PickemFormat(n_3_0=2, n_advance=2, n_0_3=2, threshold=3)
    picks = Picks(three_0=frozenset({1, 2}),
                  advance=frozenset({3, 4}),
                  zero_3=frozenset({5, 6}))
    out = Outcome(three_oh=frozenset({1, 9}),       # 1 right
                  zero_three=frozenset({6, 8}),     # 6 right
                  advanced=frozenset({1, 9, 3, 7})) # 3 right (4 missed)
    assert score(picks, out, fmt) == 3


def test_advance_pick_for_3_0_team_counts_when_lenient():
    lenient = PickemFormat(n_advance=1, n_3_0=0, n_0_3=0, advance_counts_3_0=True)
    strict = PickemFormat(n_advance=1, n_3_0=0, n_0_3=0, advance_counts_3_0=False)
    picks = Picks(frozenset(), frozenset({1}), frozenset())
    out = Outcome(three_oh=frozenset({1}), zero_three=frozenset(),
                  advanced=frozenset({1}))
    assert score(picks, out, lenient) == 1
    assert score(picks, out, strict) == 0


def _summary(sims):
    return SimSummary(n=len(sims), p_advance={}, p_3_0={}, p_0_3={}, sims=sims)


def test_optimizer_finds_certain_outcome():
    # 6 teams; team 1 always 3-0, team 6 always 0-3, teams 2&3 always advance.
    fmt = PickemFormat(n_3_0=1, n_advance=2, n_0_3=1, threshold=4)
    adv = frozenset({1, 2, 3})
    sims = [(frozenset({1}), frozenset({6}), adv)] * 500
    opt = StageOptimizer(_summary(sims), team_ids=[1, 2, 3, 4, 5, 6], fmt=fmt)
    rec = opt.optimize(restarts=3)
    assert rec.p_clear == 1.0
    assert rec.picks.three_0 == frozenset({1})
    assert rec.picks.zero_3 == frozenset({6})
    assert rec.picks.advance == frozenset({2, 3})


def test_threshold_never_worse_than_max_ev():
    # Correlated outcomes: two scenarios that flip which mid-teams advance.
    fmt = PickemFormat(n_3_0=1, n_advance=3, n_0_3=1, threshold=4)
    teams = [1, 2, 3, 4, 5, 6, 7, 8]
    sims = []
    for k in range(400):
        if k % 2 == 0:
            sims.append((frozenset({1}), frozenset({8}), frozenset({1, 2, 3, 4})))
        else:
            sims.append((frozenset({1}), frozenset({8}), frozenset({1, 5, 6, 7})))
    opt = StageOptimizer(_summary(sims), team_ids=teams, fmt=fmt)
    thr = opt.optimize(restarts=4)
    ev = opt.max_ev()
    assert thr.p_clear >= ev.p_clear - 1e-9
