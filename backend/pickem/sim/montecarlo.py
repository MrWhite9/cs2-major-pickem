"""Run many Swiss simulations and aggregate outcome probabilities.

Keeps per-simulation outcomes (3-0 team, 0-3 team, advancing set) so the M4
optimizer can exploit correlations, not just marginal probabilities.
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field

from ..ratings.glicko2 import Rating
from .swiss import Participant, StageResult, simulate_stage


@dataclass
class SimSummary:
    n: int
    p_advance: dict[int, float]
    p_3_0: dict[int, float]
    p_0_3: dict[int, float]
    # Per-sim outcomes for the optimizer: (three_oh_set, zero_three_set, advanced_set).
    sims: list[tuple[frozenset[int], frozenset[int], frozenset[int]]] = field(repr=False)


def run(participants: list[Participant], n: int = 50000,
        seed: int | None = 0,
        round1_pairs: list[tuple[int, int]] | None = None,
        all_bo3: bool = False, series_prob=None,
        played: list[tuple[int, int]] | None = None) -> SimSummary:
    rng = random.Random(seed)
    ids = [p.team_id for p in participants]
    adv = {i: 0 for i in ids}
    t30 = {i: 0 for i in ids}
    t03 = {i: 0 for i in ids}
    sims: list[tuple[frozenset[int], frozenset[int], frozenset[int]]] = []

    for _ in range(n):
        r: StageResult = simulate_stage(participants, rng,
                                        round1_pairs=round1_pairs, all_bo3=all_bo3,
                                        series_prob=series_prob, played=played)
        for t in r.advanced:
            adv[t] += 1
        for t in r.three_oh:
            t30[t] += 1
        for t in r.zero_three:
            t03[t] += 1
        sims.append((r.three_oh, r.zero_three, r.advanced))

    return SimSummary(
        n=n,
        p_advance={i: adv[i] / n for i in ids},
        p_3_0={i: t30[i] / n for i in ids},
        p_0_3={i: t03[i] / n for i in ids},
        sims=sims,
    )


# --- assembling a stage from stored data ---------------------------------

def stage_participants(conn: sqlite3.Connection, tournament_id: int,
                       as_of: str, system: str = "glicko2_map",
                       seeds: dict[int, int] | None = None) -> list[Participant]:
    """Build the 16 Participants for a tournament, rated as of `as_of`.

    Seeding: explicit `seeds` if given, else by rating (best team = seed 1).
    """
    as_of_iso = as_of if "T" in as_of else f"{as_of}T00:00:00Z"
    team_ids = _tournament_team_ids(conn, tournament_id)

    rated: dict[int, Rating] = {}
    for tid in team_ids:
        row = conn.execute(
            "SELECT rating, deviation, volatility FROM ratings "
            "WHERE team_id = ? AND as_of = ? AND system = ?",
            (tid, as_of_iso, system),
        ).fetchone()
        rated[tid] = Rating(row["rating"], row["deviation"], row["volatility"]) \
            if row else Rating()

    if seeds is None:
        order = sorted(team_ids, key=lambda t: rated[t].rating, reverse=True)
        seeds = {tid: i + 1 for i, tid in enumerate(order)}

    return [Participant(tid, seeds[tid], rated[tid]) for tid in team_ids]


def played_results(conn: sqlite3.Connection, tournament_id: int
                   ) -> list[tuple[int, int]]:
    """Completed matches in a stage as (winner_id, loser_id) for live conditioning."""
    rows = conn.execute(
        """SELECT team_a_id a, team_b_id b, winner_id w FROM matches
           WHERE tournament_id = ? AND status = 'finished'
             AND winner_id IS NOT NULL AND team_a_id IS NOT NULL
             AND team_b_id IS NOT NULL""",
        (tournament_id,),
    ).fetchall()
    out = []
    for r in rows:
        w = r["w"]
        loser = r["b"] if w == r["a"] else r["a"]
        out.append((w, loser))
    return out


def _tournament_team_ids(conn: sqlite3.Connection, tournament_id: int) -> list[int]:
    rows = conn.execute(
        """SELECT DISTINCT team_id FROM (
               SELECT team_a_id AS team_id FROM matches WHERE tournament_id = ?
               UNION
               SELECT team_b_id FROM matches WHERE tournament_id = ?
           ) WHERE team_id IS NOT NULL""",
        (tournament_id, tournament_id),
    ).fetchall()
    return [r["team_id"] for r in rows]
