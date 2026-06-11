"""Tests for Bo-series math and Swiss structural invariants."""
from __future__ import annotations

import math
import random

from pickem.ratings.glicko2 import Rating
from pickem.sim.bo import series_win_prob
from pickem.sim.swiss import Participant, bo_for, simulate_stage


def test_bo1_is_identity():
    assert series_win_prob(0.63, 1) == 0.63


def test_bo3_amplifies_favorite():
    assert math.isclose(series_win_prob(0.60, 3), 0.648, abs_tol=1e-3)
    assert series_win_prob(0.60, 3) > 0.60
    assert series_win_prob(0.40, 3) < 0.40


def test_series_probabilities_complementary():
    for p in (0.3, 0.5, 0.55, 0.8):
        for n in (1, 3, 5):
            assert math.isclose(
                series_win_prob(p, n) + series_win_prob(1 - p, n), 1.0, abs_tol=1e-9)


def test_bo_format_rule():
    assert bo_for((0, 0), (0, 0)) == 1     # opening round
    assert bo_for((1, 0), (1, 1)) == 1
    assert bo_for((2, 0), (2, 1)) == 3     # advancement
    assert bo_for((0, 2), (1, 2)) == 3     # elimination
    assert bo_for((2, 2), (2, 2)) == 3     # decider


def _equal_field(n=16):
    return [Participant(team_id=i, seed=i, rating=Rating()) for i in range(1, n + 1)]


def test_swiss_produces_valid_standings():
    rng = random.Random(123)
    for _ in range(200):
        res = simulate_stage(_equal_field(), rng)
        assert len(res.advanced) == 8
        assert len(res.eliminated) == 8
        assert res.advanced.isdisjoint(res.eliminated)
        # a 16-team Buchholz Swiss yields exactly two 3-0 and two 0-3 teams
        assert len(res.three_oh) == 2 and len(res.zero_three) == 2
        assert res.three_oh <= res.advanced and res.zero_three <= res.eliminated
        for t, (w, l) in res.record.items():
            assert (w == 3) != (l == 3)        # finished at 3W xor 3L
            assert w <= 3 and l <= 3
        # advancing teams have 3 wins; eliminated have 3 losses
        assert all(res.record[t][0] == 3 for t in res.advanced)
        assert all(res.record[t][1] == 3 for t in res.eliminated)
        # standings distribution is the canonical 2/3/3 split
        from collections import Counter
        dist = Counter(res.record.values())
        assert dist == {(3, 0): 2, (3, 1): 3, (3, 2): 3,
                        (2, 3): 3, (1, 3): 3, (0, 3): 2}


def test_swiss_is_deterministic_with_seed():
    a = simulate_stage(_equal_field(), random.Random(7))
    b = simulate_stage(_equal_field(), random.Random(7))
    assert a.advanced == b.advanced and a.three_oh == b.three_oh


def test_played_results_are_respected():
    """Conditioning: a completed matchup always resolves to its real winner."""
    field = _equal_field()
    # force team 1 to have beaten team 9 and team 2 to have beaten team 10
    played = [(1, 9), (2, 10)]
    rng = random.Random(5)
    for _ in range(300):
        res = simulate_stage(field, rng, round1_pairs=list(zip(range(1, 9), range(9, 17))),
                             played=played)
        # the conditioned losers can never finish 3-0; winners can never finish 0-3
        assert 9 not in res.three_oh and 10 not in res.three_oh
        assert 1 not in res.zero_three and 2 not in res.zero_three
        # every team still finishes 3W xor 3L
        for t, (w, l) in res.record.items():
            assert (w == 3) != (l == 3)


def test_full_conditioning_is_deterministic():
    """If every match is pre-decided, the stage outcome is fixed regardless of rng."""
    field = _equal_field(4)  # 4-team toy Swiss: 2 advance, 2 out
    # R1: 1>3, 2>4 -> 1,2 at 1-0 and 3,4 at 0-1
    # R2: 1>2 (1 advances 2-0), 3>4 (4 out 0-2); 2 and 3 both 1-1
    # R3: 2>3 -> 2 advances, 3 out.  Every match pre-decided -> fixed outcome.
    played = [(1, 3), (2, 4), (1, 2), (3, 4), (2, 3)]
    a = simulate_stage(field, random.Random(1), advance_at=2, eliminate_at=2,
                       round1_pairs=[(1, 3), (2, 4)], played=played)
    b = simulate_stage(field, random.Random(999), advance_at=2, eliminate_at=2,
                       round1_pairs=[(1, 3), (2, 4)], played=played)
    assert a.advanced == b.advanced == frozenset({1, 2})
    assert a.eliminated == b.eliminated == frozenset({3, 4})


def test_strong_seeds_advance_more_often():
    field = [Participant(team_id=i, seed=i,
                         rating=Rating(1900 - (i - 1) * 50, 50))
             for i in range(1, 17)]
    rng = random.Random(0)
    adv = {p.team_id: 0 for p in field}
    for _ in range(2000):
        for t in simulate_stage(field, rng).advanced:
            adv[t] += 1
    # the strongest team should advance far more than the weakest
    assert adv[1] > adv[16]
    assert adv[1] > 1500   # ~top seed advances most of the time
