"""Registry of Majors and per-stage configuration.

Each stage has its own rating cutoff (the stage's start date) so ratings are
frozen correctly per stage with no leakage. Bo format and real round-1 pairings
are auto-detected from the stored matches, so adding a Major usually only means
backfilling its stages and listing the tournament ids here.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class StageSpec:
    tournament_id: int
    label: str
    cutoff: str            # ISO date; ratings use matches strictly before it


@dataclass(frozen=True)
class MajorSpec:
    key: str
    name: str
    stages: tuple[StageSpec, ...]


MAJORS: dict[str, MajorSpec] = {
    "cologne": MajorSpec("cologne", "IEM Cologne Major 2026", (
        StageSpec(20708, "Stage 1", "2026-06-02"),
        StageSpec(20709, "Stage 2", "2026-06-06"),
        StageSpec(21115, "Stage 3", "2026-06-11"),
    )),
    "budapest": MajorSpec("budapest", "StarLadder Budapest Major 2025", (
        StageSpec(17897, "Stage 1", "2025-11-24"),
        StageSpec(17898, "Stage 2", "2025-11-29"),
        StageSpec(17899, "Stage 3", "2025-12-04"),
    )),
}

DEFAULT_MAJOR = "cologne"


def stage_format(conn: sqlite3.Connection, tournament_id: int
                 ) -> tuple[bool, list[tuple[int, int]] | None]:
    """Auto-detect (all_bo3, round1_pairs) for a stage from stored matches.

    all_bo3   : True when the stage has no Bo1 matches (e.g. Cologne S3).
    round1_pairs: the real opening matchups when only round 1 is seeded so far
                  (an upcoming stage); None once the whole stage is drawn/played,
                  in which case the simulator falls back to rating-based seeding.
    """
    rows = conn.execute(
        "SELECT team_a_id a, team_b_id b, n_games FROM matches WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchall()
    if not rows:
        return False, None

    all_bo3 = all(r["n_games"] != 1 for r in rows)
    teams = {t for r in rows for t in (r["a"], r["b"]) if t is not None}
    paired = [(r["a"], r["b"]) for r in rows if r["a"] is not None and r["b"] is not None]
    round1 = paired if teams and len(paired) == len(teams) // 2 else None
    return all_bo3, round1
