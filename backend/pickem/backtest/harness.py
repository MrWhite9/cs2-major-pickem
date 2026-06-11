"""Backtest the predictor against a completed Major.

For each Swiss stage: freeze ratings at the Major's start, simulate the stage,
choose picks with each strategy, then score those picks against what actually
happened and report whether the stage threshold was cleared.

Usage:
    python -m pickem.backtest.harness --serie 9822 --cutoff 2025-11-24
"""
from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass, field

from ..data.db import connect
from ..majors import stage_format
from ..optimize.format import Outcome, PickemFormat, Picks, score
from ..optimize.optimizer import StageOptimizer, Recommendation
from ..sim.montecarlo import run, stage_participants
from ..sim.veto import build_series_matrices

# Default stage layout for a StarLadder/BLAST-style 3-stage Major.
DEFAULT_STAGES = [(17897, "Stage 1"), (17898, "Stage 2"), (17899, "Stage 3")]


@dataclass
class StrategyResult:
    strategy: str
    picks: Picks
    p_clear: float
    e_correct: float
    correct: int
    cleared: bool


@dataclass
class StageBacktest:
    tournament_id: int
    label: str
    actual: Outcome
    results: list[StrategyResult] = field(default_factory=list)


def actual_outcome(conn: sqlite3.Connection, tournament_id: int) -> Outcome:
    wins: dict[int, int] = {}
    losses: dict[int, int] = {}
    for m in conn.execute(
        "SELECT team_a_id a, team_b_id b, winner_id w FROM matches WHERE tournament_id = ?",
        (tournament_id,),
    ):
        a, b, w = m["a"], m["b"], m["w"]
        for t in (a, b):
            if t is not None:
                wins.setdefault(t, 0)
                losses.setdefault(t, 0)
        if w is None or a is None or b is None:
            continue                       # unplayed match (e.g. upcoming stage)
        if w == a:
            wins[a] += 1; losses[b] += 1
        elif w == b:
            wins[b] += 1; losses[a] += 1
    advanced = frozenset(t for t in wins if wins[t] >= 3)
    three_oh = frozenset(t for t in wins if (wins[t], losses[t]) == (3, 0))
    zero_three = frozenset(t for t in wins if (wins[t], losses[t]) == (0, 3))
    return Outcome(three_oh, zero_three, advanced)


def backtest_stage(conn: sqlite3.Connection, tournament_id: int, label: str,
                   cutoff: str, fmt: PickemFormat, n_sims: int = 50000,
                   system: str = "glicko2_map", seed: int = 1,
                   map_aware: bool = False, n_veto: int = 1200) -> StageBacktest:
    parts = stage_participants(conn, tournament_id, cutoff, system=system)
    all_bo3, round1 = stage_format(conn, tournament_id)
    series_prob = None
    if map_aware:
        series_prob = build_series_matrices(conn, parts, cutoff, tournament_id,
                                            n_veto=n_veto, seed=seed)
    summary = run(parts, n=n_sims, seed=seed, round1_pairs=round1,
                  all_bo3=all_bo3, series_prob=series_prob)
    team_ids = [p.team_id for p in parts]
    opt = StageOptimizer(summary, team_ids, fmt)

    recs = [opt.optimize(seed=seed), opt.max_ev()]
    actual = actual_outcome(conn, tournament_id)

    bt = StageBacktest(tournament_id, label, actual)
    for rec in recs:
        c = score(rec.picks, actual, fmt)
        bt.results.append(StrategyResult(
            strategy=rec.strategy, picks=rec.picks, p_clear=rec.p_clear,
            e_correct=rec.e_correct, correct=c, cleared=c >= fmt.threshold))
    return bt


def backtest_major(conn: sqlite3.Connection, stages, cutoff: str,
                   fmt: PickemFormat | None = None, n_sims: int = 50000
                   ) -> list[StageBacktest]:
    fmt = fmt or PickemFormat()
    return [backtest_stage(conn, tid, label, cutoff, fmt, n_sims)
            for tid, label in stages]


# --- reporting -----------------------------------------------------------

def _name(conn: sqlite3.Connection, tid: int) -> str:
    r = conn.execute("SELECT name FROM teams WHERE id = ?", (tid,)).fetchone()
    return r["name"] if r else str(tid)


def print_report(conn: sqlite3.Connection, backtests: list[StageBacktest],
                 fmt: PickemFormat) -> None:
    def names(ids):
        return ", ".join(sorted(_name(conn, t) for t in ids))

    totals: dict[str, list[int]] = {}
    print(f"\nPick'Em backtest  (threshold = {fmt.threshold} correct / "
          f"{fmt.n_picks} picks per stage)\n" + "=" * 64)
    for bt in backtests:
        print(f"\n{bt.label}")
        print(f"  actual 3-0: {names(bt.actual.three_oh)}")
        print(f"  actual 0-3: {names(bt.actual.zero_three)}")
        for r in bt.results:
            totals.setdefault(r.strategy, []).append(r.correct)
            flag = "CLEARED" if r.cleared else "missed "
            print(f"  [{r.strategy:9}] {r.correct:>2}/{fmt.n_picks} correct  "
                  f"{flag}  (model P(clear)={r.p_clear:.0%}, E[correct]={r.e_correct:.1f})")
            print(f"      3-0: {names(r.picks.three_0)}")
            print(f"      adv: {names(r.picks.advance)}")
            print(f"      0-3: {names(r.picks.zero_3)}")

    print("\n" + "=" * 64)
    n_stages = len(backtests)
    for strat, corrects in totals.items():
        cleared = sum(1 for bt in backtests
                      for r in bt.results if r.strategy == strat and r.cleared)
        print(f"  {strat:9}: cleared {cleared}/{n_stages} stages, "
              f"{sum(corrects)} total correct picks")


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest the Pick'Em predictor.")
    p.add_argument("--cutoff", required=True, help="Rating cutoff (Major start), ISO date.")
    p.add_argument("--serie", type=int, help="(informational) serie id of the Major.")
    p.add_argument("--sims", type=int, default=50000)
    p.add_argument("--threshold", type=int, default=5)
    args = p.parse_args()

    conn = connect()
    fmt = PickemFormat(threshold=args.threshold)
    backtests = backtest_major(conn, DEFAULT_STAGES, args.cutoff, fmt, n_sims=args.sims)
    print_report(conn, backtests, fmt)
    conn.close()


if __name__ == "__main__":
    main()
