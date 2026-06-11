"""Pick'Em format + scoring for a single Major Swiss stage.

Per the current Valve 3-stage Major format, each Swiss stage asks for 10 picks:
  * 2 teams to finish 3-0 (flawless)
  * 6 teams to advance with a 3-1 / 3-2 record
  * 2 teams to finish 0-3 (winless)
The objective is to clear a per-stage correct-pick threshold (coin upgrade),
not to maximise expected correct picks.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PickemFormat:
    name: str = "major_swiss"
    n_3_0: int = 2
    n_advance: int = 6          # advance NOT counting the 3-0 slots
    n_0_3: int = 2
    threshold: int = 5          # correct picks needed to clear the stage
    # If True, a team placed in an "advance" slot that actually goes 3-0 still
    # counts (it did advance). If False, only 3-1/3-2 finishes count there.
    advance_counts_3_0: bool = True

    @property
    def n_picks(self) -> int:
        return self.n_3_0 + self.n_advance + self.n_0_3


@dataclass(frozen=True)
class Picks:
    three_0: frozenset[int]
    advance: frozenset[int]
    zero_3: frozenset[int]

    def all_teams(self) -> frozenset[int]:
        return self.three_0 | self.advance | self.zero_3


@dataclass
class Outcome:
    """A single realised (or simulated) stage result."""
    three_oh: frozenset[int]
    zero_three: frozenset[int]
    advanced: frozenset[int]


def score(picks: Picks, outcome: Outcome, fmt: PickemFormat) -> int:
    """Number of correct picks for one outcome."""
    correct = sum(1 for t in picks.three_0 if t in outcome.three_oh)
    correct += sum(1 for t in picks.zero_3 if t in outcome.zero_three)
    for t in picks.advance:
        if t in outcome.advanced and (fmt.advance_counts_3_0 or t not in outcome.three_oh):
            correct += 1
    return correct
