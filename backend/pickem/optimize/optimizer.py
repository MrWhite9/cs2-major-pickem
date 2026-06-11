"""Choose Pick'Em picks that maximise the probability of clearing the stage.

The objective is P(correct picks >= threshold) over the simulated joint outcome
distribution -- NOT expected correct picks. Because exactly-8-advance and the
two 3-0 / two 0-3 slots are correlated, the threshold-optimal picks can differ
from the naive "pick the most likely team in each slot" (max-EV / chalk) choice.

Approach: precompute, for every (team, slot) pair, a 0/1 vector over simulations
indicating whether that pick would be correct in that sim. Any candidate pick's
per-sim correct count is then the sum of 10 such vectors, and the objective is a
cheap mean over the threshold. A greedy marginal start is improved by local
search (replace / cross-slot swap) with random restarts.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

from ..sim.montecarlo import SimSummary
from .format import Outcome, PickemFormat, Picks, score

THREE_0, ADVANCE, ZERO_3 = 0, 1, 2


@dataclass
class Recommendation:
    picks: Picks
    p_clear: float          # P(correct >= threshold) on the simulated dist
    e_correct: float        # expected correct picks
    strategy: str


class StageOptimizer:
    """Holds the simulated correctness matrices for one stage."""

    def __init__(self, summary: SimSummary, team_ids: list[int], fmt: PickemFormat):
        self.fmt = fmt
        self.team_ids = list(team_ids)
        self.idx = {t: i for i, t in enumerate(self.team_ids)}
        self.n_sims = summary.n
        m = len(self.team_ids)
        # correctness[slot] is an (m, n_sims) int8 matrix
        self.correct = [np.zeros((m, summary.n), dtype=np.int8) for _ in range(3)]
        for s, (t30, t03, adv) in enumerate(summary.sims):
            for t in t30:
                self.correct[THREE_0][self.idx[t], s] = 1
            for t in t03:
                self.correct[ZERO_3][self.idx[t], s] = 1
            for t in adv:
                self.correct[ADVANCE][self.idx[t], s] = 1
            if not fmt.advance_counts_3_0:
                for t in t30:
                    self.correct[ADVANCE][self.idx[t], s] = 0
        # marginal P(correct) per (slot, team)
        self.marginal = [c.mean(axis=1) for c in self.correct]

    # --- objective ------------------------------------------------------
    def _total(self, sel: list[list[int]]) -> np.ndarray:
        total = np.zeros(self.n_sims, dtype=np.int32)
        for slot in range(3):
            if sel[slot]:
                total += self.correct[slot][sel[slot]].sum(axis=0)
        return total

    def _objective(self, sel: list[list[int]]) -> tuple[float, float]:
        total = self._total(sel)
        p_clear = float((total >= self.fmt.threshold).mean())
        return p_clear, float(total.mean())

    # --- starting point: greedy marginal (== max-EV / chalk) ------------
    def _greedy(self) -> list[list[int]]:
        sizes = [self.fmt.n_3_0, self.fmt.n_advance, self.fmt.n_0_3]
        used: set[int] = set()
        sel: list[list[int]] = [[], [], []]
        # Fill the scarce/decisive slots first: 3-0, then 0-3, then advance.
        for slot in (THREE_0, ZERO_3, ADVANCE):
            order = np.argsort(-self.marginal[slot])
            for i in order:
                if len(sel[slot]) >= sizes[slot]:
                    break
                if i not in used:
                    sel[slot].append(int(i))
                    used.add(int(i))
        return sel

    # --- local search ---------------------------------------------------
    def _neighbours(self, sel: list[list[int]]):
        """Yield candidate moves as new selection states."""
        used = {i for slot in sel for i in slot}
        free = [i for i in range(len(self.team_ids)) if i not in used]
        # replace: swap a selected team for an unused team in the same slot
        for slot in range(3):
            for pos, cur in enumerate(sel[slot]):
                for j in free:
                    nxt = [list(s) for s in sel]
                    nxt[slot][pos] = j
                    yield nxt
        # cross-slot swap: two selected teams trade slots
        for a in range(3):
            for b in range(a + 1, 3):
                for pa, ta in enumerate(sel[a]):
                    for pb, tb in enumerate(sel[b]):
                        nxt = [list(s) for s in sel]
                        nxt[a][pa], nxt[b][pb] = tb, ta
                        yield nxt

    def _hill_climb(self, sel: list[list[int]]) -> tuple[list[list[int]], tuple[float, float]]:
        best = sel
        best_obj = self._objective(sel)
        improved = True
        while improved:
            improved = False
            for cand in self._neighbours(best):
                obj = self._objective(cand)
                if obj > best_obj:           # lexicographic: p_clear then e_correct
                    best, best_obj, improved = cand, obj, True
        return best, best_obj

    def optimize(self, restarts: int = 6, seed: int = 0) -> Recommendation:
        rng = random.Random(seed)
        start = self._greedy()
        best, best_obj = self._hill_climb(start)
        m = len(self.team_ids)
        sizes = [self.fmt.n_3_0, self.fmt.n_advance, self.fmt.n_0_3]
        for _ in range(restarts):
            teams = rng.sample(range(m), sum(sizes))
            sel = [teams[0:sizes[0]],
                   teams[sizes[0]:sizes[0] + sizes[1]],
                   teams[sizes[0] + sizes[1]:]]
            cand, obj = self._hill_climb(sel)
            if obj > best_obj:
                best, best_obj = cand, obj
        return self._to_rec(best, "threshold", best_obj)

    def max_ev(self) -> Recommendation:
        """Baseline: maximise expected correct picks (== greedy marginal)."""
        sel = self._greedy()
        return self._to_rec(sel, "max_ev", self._objective(sel))

    def _to_rec(self, sel, strategy, obj) -> Recommendation:
        names = [[self.team_ids[i] for i in slot] for slot in sel]
        picks = Picks(frozenset(names[THREE_0]),
                      frozenset(names[ADVANCE]),
                      frozenset(names[ZERO_3]))
        return Recommendation(picks, obj[0], obj[1], strategy)


def score_against_actual(picks: Picks, actual: Outcome, fmt: PickemFormat) -> int:
    return score(picks, actual, fmt)
