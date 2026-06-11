"""Faithful Valve "Buchholz" Swiss-stage simulator (16 teams, 3W advance / 3L out).

Rules reproduced (CS Major Swiss since 2022):
  * Round 1: seed i vs seed i+8 (1v9, 2v10, ... 8v16).
  * Rounds 2+: teams grouped by current W-L record; within a group they are
    seeded by Buchholz (sum of opponents' W-L differential) desc, then initial
    seed asc. The best seed plays the worst, etc., avoiding rematches.
  * A match is Bo3 if either team is at 2 wins or 2 losses (advancement /
    elimination), otherwise Bo1.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from ..ratings.glicko2 import Rating, win_probability
from .bo import series_win_prob

# A map-aware override: P(team a beats team b) in a Bo`n_games` series.
SeriesProb = Callable[[int, int, int], float]


@dataclass(frozen=True)
class Participant:
    team_id: int
    seed: int          # initial seed, 1..16
    rating: Rating


@dataclass
class StageResult:
    advanced: frozenset[int]
    eliminated: frozenset[int]
    three_oh: frozenset[int]    # the 3-0 teams (a 16-team Swiss yields two)
    zero_three: frozenset[int]  # the 0-3 teams (two)
    record: dict[int, tuple[int, int]]   # team_id -> (wins, losses)


def bo_for(rec_a: tuple[int, int], rec_b: tuple[int, int]) -> int:
    """Bo3 if either team can advance (2 wins) or be eliminated (2 losses)."""
    (wa, la), (wb, lb) = rec_a, rec_b
    return 3 if 2 in (wa, la, wb, lb) else 1


def _pair_no_rematch(order: list[int], opponents: dict[int, set[int]]
                     ) -> list[tuple[int, int]] | None:
    """Pair a seed-ordered group best-vs-worst, avoiding rematches (backtracking)."""
    if not order:
        return []
    a, rest = order[0], order[1:]
    for i in range(len(rest) - 1, -1, -1):   # try worst-ranked partner first
        b = rest[i]
        if b in opponents[a]:
            continue
        sub = _pair_no_rematch(rest[:i] + rest[i + 1:], opponents)
        if sub is not None:
            return [(a, b)] + sub
    return None


def simulate_stage(participants: list[Participant], rng: random.Random,
                   advance_at: int = 3, eliminate_at: int = 3,
                   round1_pairs: list[tuple[int, int]] | None = None,
                   all_bo3: bool = False,
                   series_prob: SeriesProb | None = None,
                   played: list[tuple[int, int]] | None = None) -> StageResult:
    """Simulate one Swiss stage.

    round1_pairs: real opening-round matchups (overrides seed-based pairing) --
        use when the actual bracket seeding is known.
    all_bo3: every match is Bo3 (some Majors, e.g. Cologne 2026 Stage 3, drop
        the Bo1 opening/middle rounds) instead of the Bo1-until-2W/2L rule.
    series_prob: optional map-aware P(a beats b, n_games). When given it replaces
        the average-map win_probability+Bo formula (the map-aware model, P3).
    played: completed matches as (winner_id, loser_id). When the Buchholz pairing
        re-derives one of these matchups it is resolved deterministically instead
        of simulated -- this conditions the run on results so far (live tracking).
    """
    known: dict[frozenset[int], int] = {}
    if played:
        known = {frozenset((w, l)): w for w, l in played}
    wins = {p.team_id: 0 for p in participants}
    losses = {p.team_id: 0 for p in participants}
    opponents: dict[int, set[int]] = {p.team_id: set() for p in participants}
    seed = {p.team_id: p.seed for p in participants}
    rating = {p.team_id: p.rating for p in participants}

    first_round = True
    while True:
        live = [t for t in wins
                if wins[t] < advance_at and losses[t] < eliminate_at]
        if not live:
            break

        pairs: list[tuple[int, int]] = []
        if first_round:
            if round1_pairs is not None:
                pairs = list(round1_pairs)
            else:
                order = sorted(live, key=lambda t: seed[t])
                half = len(order) // 2
                pairs = list(zip(order[:half], order[half:]))
            first_round = False
        else:
            groups: dict[tuple[int, int], list[int]] = {}
            for t in live:
                groups.setdefault((wins[t], losses[t]), []).append(t)
            for rec, teams in groups.items():
                def buchholz(t: int) -> int:
                    return sum(wins[o] - losses[o] for o in opponents[t])
                ordered = sorted(teams, key=lambda t: (-buchholz(t), seed[t]))
                group_pairs = _pair_no_rematch(ordered, opponents)
                if group_pairs is None:   # extremely rare; fall back to adjacent
                    group_pairs = list(zip(ordered[0::2], ordered[1::2]))
                pairs.extend(group_pairs)

        for a, b in pairs:
            res = known.get(frozenset((a, b)))
            if res is not None:
                winner, loser = (a, b) if res == a else (b, a)
            else:
                n_games = 3 if all_bo3 else bo_for((wins[a], losses[a]), (wins[b], losses[b]))
                if series_prob is not None:
                    p_series = series_prob(a, b, n_games)
                else:
                    p_series = series_win_prob(win_probability(rating[a], rating[b]), n_games)
                winner, loser = (a, b) if rng.random() < p_series else (b, a)
            wins[winner] += 1
            losses[loser] += 1
            opponents[a].add(b)
            opponents[b].add(a)

    advanced = frozenset(t for t in wins if wins[t] >= advance_at)
    eliminated = frozenset(t for t in wins if losses[t] >= eliminate_at)
    three_oh = frozenset(t for t in advanced if losses[t] == 0)
    zero_three = frozenset(t for t in eliminated if wins[t] == 0)
    return StageResult(advanced, eliminated, three_oh, zero_three,
                       {t: (wins[t], losses[t]) for t in wins})
