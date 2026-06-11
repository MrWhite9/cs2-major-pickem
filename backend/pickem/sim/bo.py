"""Best-of-N series probabilities from a per-map win probability.

A BoN series (N odd) is won by the first team to (N+1)/2 map wins. Given a
constant per-map probability `p`, the chance of taking the series is the
probability of reaching w wins before the opponent does (negative binomial).
A Bo3 amplifies the favorite: p=0.60 -> ~0.648; p=0.70 -> ~0.784.
"""
from __future__ import annotations

from math import comb


def series_win_prob(map_p: float, n_games: int) -> float:
    """P(win a Bo`n_games` series) given per-map win probability `map_p`."""
    if n_games <= 1:
        return map_p
    w = n_games // 2 + 1                 # maps needed to win the series
    total = 0.0
    for losses in range(w):             # opponent's map wins when we clinch
        total += comb(w - 1 + losses, losses) * (map_p ** w) * ((1 - map_p) ** losses)
    return total


def series_win_prob_distinct(probs: list[float]) -> float:
    """P(win the series) given per-map win probabilities in play order.

    Unlike `series_win_prob`, each map has its own probability (the map-aware
    model: a Bo3's three maps differ in how the two teams match up). `probs`
    lists P(win map i) in the order the maps are played; the series is first to
    len(probs)//2 + 1. Maps after the series is clinched don't change the
    winner, so the result is order-sensitive only through which map is decisive.
    """
    if not probs:
        return 0.0
    if len(probs) == 1:
        return probs[0]
    w = len(probs) // 2 + 1
    state: dict[tuple[int, int], float] = {(0, 0): 1.0}
    for p in probs:
        nxt: dict[tuple[int, int], float] = {}
        for (aw, bw), prob in state.items():
            if aw >= w or bw >= w:       # already decided; carry forward
                nxt[(aw, bw)] = nxt.get((aw, bw), 0.0) + prob
                continue
            nxt[(aw + 1, bw)] = nxt.get((aw + 1, bw), 0.0) + prob * p
            nxt[(aw, bw + 1)] = nxt.get((aw, bw + 1), 0.0) + prob * (1.0 - p)
        state = nxt
    return sum(prob for (aw, _), prob in state.items() if aw >= w)
