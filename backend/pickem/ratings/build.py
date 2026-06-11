"""Build time-frozen Glicko-2 team ratings from stored match history.

Processes matches in weekly rating periods up to a cutoff date (the Major's
start), so no in-Major result ever influences the ratings used to predict it.
Match impact is weighted by the tournament's tier.

Usage:
    python -m pickem.ratings.build --cutoff 2025-11-24 --top 25
    python -m pickem.ratings.build --cutoff 2025-11-24 --serie 9822   # validate vs a Major
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

from ..data.db import connect
from .glicko2 import DEFAULT_RD, Rating, Result, rate

# Tournament tier -> information weight. Higher tiers carry more signal; the
# low-tier tail still contributes connectivity between the team graph.
TIER_WEIGHT = {"s": 1.0, "a": 0.85, "b": 0.7, "c": 0.45, "d": 0.25}
DEFAULT_TIER_WEIGHT = 0.25


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load_results(conn: sqlite3.Connection, cutoff_iso: str, level: str):
    """Rows of (a, b, w, ts, tier) for matches strictly before the cutoff.

    level='match' uses the match winner; level='map' expands to one row per
    played map (winner from the games table), giving ~2x the data and a clean
    "probability of winning a single map" interpretation for the simulator.
    """
    if level == "match":
        return conn.execute(
            """SELECT m.team_a_id AS a, m.team_b_id AS b, m.winner_id AS w,
                      m.begin_at AS ts, COALESCE(tr.tier, '?') AS tier
               FROM matches m
               LEFT JOIN tournaments tr ON tr.id = m.tournament_id
               WHERE m.begin_at IS NOT NULL AND m.begin_at < ?
                 AND m.winner_id IS NOT NULL
                 AND m.team_a_id IS NOT NULL AND m.team_b_id IS NOT NULL
               ORDER BY m.begin_at""",
            (cutoff_iso,),
        ).fetchall()
    if level == "map":
        # A map is contested by the parent match's two teams; winner is the
        # game's winner. Skip forfeits and games without a recorded winner.
        return conn.execute(
            """SELECT m.team_a_id AS a, m.team_b_id AS b, g.winner_id AS w,
                      m.begin_at AS ts, COALESCE(tr.tier, '?') AS tier
               FROM games g
               JOIN matches m ON m.id = g.match_id
               LEFT JOIN tournaments tr ON tr.id = m.tournament_id
               WHERE m.begin_at IS NOT NULL AND m.begin_at < ?
                 AND g.winner_id IS NOT NULL AND g.forfeit = 0
                 AND m.team_a_id IS NOT NULL AND m.team_b_id IS NOT NULL
                 AND g.winner_id IN (m.team_a_id, m.team_b_id)
               ORDER BY m.begin_at""",
            (cutoff_iso,),
        ).fetchall()
    raise ValueError(f"unknown level: {level!r} (use 'match' or 'map')")


def build_ratings(cutoff: str, conn: sqlite3.Connection | None = None,
                  period_days: int = 7, system: str | None = None,
                  level: str = "map",
                  tier_weights: dict[str, float] | None = None,
                  save: bool = True) -> dict[int, Rating]:
    """Compute ratings for every team using matches strictly before `cutoff`.

    level='map' (default) rates per-map win skill; the simulator turns this into
    series win probabilities via the Bo formula. system defaults to
    f"glicko2_{level}" so map and match snapshots can coexist in the table.
    """
    own_conn = conn is None
    conn = conn or connect()
    tier_weights = tier_weights or TIER_WEIGHT
    system = system or f"glicko2_{level}"
    cutoff_iso = cutoff if "T" in cutoff else f"{cutoff}T00:00:00Z"

    rows = load_results(conn, cutoff_iso, level)

    ratings: dict[int, Rating] = {}
    if not rows:
        if own_conn:
            conn.close()
        return ratings

    period0 = _parse(rows[0]["ts"])

    def period_index(ts: str) -> int:
        return (_parse(ts) - period0).days // period_days

    # Bucket matches into rating periods.
    periods: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        periods.setdefault(period_index(r["ts"]), []).append(r)

    for pidx in sorted(periods):
        games = periods[pidx]
        # Opponent ratings are read from the snapshot at the START of the period.
        snapshot = dict(ratings)
        played: set[int] = set()
        pending: dict[int, list[Result]] = {}

        for g in games:
            a, b, w = g["a"], g["b"], g["w"]
            weight = tier_weights.get(g["tier"], DEFAULT_TIER_WEIGHT)
            ra = snapshot.get(a, Rating())
            rb = snapshot.get(b, Rating())
            sa = 1.0 if w == a else 0.0
            pending.setdefault(a, []).append(Result(rb, sa, weight))
            pending.setdefault(b, []).append(Result(ra, 1.0 - sa, weight))
            played.update((a, b))

        # Apply updates for teams that played this period.
        for tid, results in pending.items():
            ratings[tid] = rate(ratings.get(tid, Rating()), results)
        # Inflate RD for known teams that sat out (decay of certainty).
        for tid in list(ratings):
            if tid not in played:
                ratings[tid] = rate(ratings[tid], [])

    if save:
        _save(conn, ratings, cutoff_iso, system)

    if own_conn:
        conn.close()
    return ratings


def _save(conn: sqlite3.Connection, ratings: dict[int, Rating],
          as_of: str, system: str) -> None:
    conn.execute("DELETE FROM ratings WHERE as_of = ? AND system = ?", (as_of, system))
    conn.executemany(
        "INSERT INTO ratings (team_id, as_of, system, rating, deviation, volatility)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(tid, as_of, system, r.rating, r.rd, r.vol) for tid, r in ratings.items()],
    )
    conn.commit()


def team_name(conn: sqlite3.Connection, tid: int) -> str:
    row = conn.execute("SELECT name FROM teams WHERE id = ?", (tid,)).fetchone()
    return row["name"] if row else f"#{tid}"


def serie_team_ids(conn: sqlite3.Connection, serie_id: int) -> set[int]:
    rows = conn.execute(
        """SELECT DISTINCT team_id FROM (
               SELECT team_a_id AS team_id FROM matches m
                 JOIN tournaments t ON t.id = m.tournament_id WHERE t.serie_id = ?
               UNION
               SELECT team_b_id FROM matches m
                 JOIN tournaments t ON t.id = m.tournament_id WHERE t.serie_id = ?
           ) WHERE team_id IS NOT NULL""",
        (serie_id, serie_id),
    ).fetchall()
    return {r["team_id"] for r in rows}


def main() -> None:
    p = argparse.ArgumentParser(description="Build time-frozen Glicko-2 ratings.")
    p.add_argument("--cutoff", required=True, help="ISO date; ratings use matches before it.")
    p.add_argument("--level", choices=["map", "match"], default="map",
                   help="Rate per map (default) or per match.")
    p.add_argument("--top", type=int, default=25, help="How many top teams to print.")
    p.add_argument("--serie", type=int, help="Restrict the printout to a serie's teams.")
    p.add_argument("--no-save", action="store_true", help="Don't write to the ratings table.")
    args = p.parse_args()

    conn = connect()
    ratings = build_ratings(args.cutoff, conn=conn, level=args.level, save=not args.no_save)
    print(f"Rated {len(ratings)} teams as of {args.cutoff}.")

    if args.serie:
        ids = serie_team_ids(conn, args.serie) & set(ratings)
        ranked = sorted(ids, key=lambda t: ratings[t].rating, reverse=True)
        print(f"\nTeams in serie {args.serie}, by rating:")
    else:
        ranked = sorted(ratings, key=lambda t: ratings[t].rating, reverse=True)
        print(f"\nTop {args.top} teams:")
        ranked = ranked[: args.top]

    print(f"{'#':>3}  {'rating':>7} {'RD':>5}  team")
    for i, tid in enumerate(ranked, 1):
        r = ratings[tid]
        print(f"{i:>3}  {r.rating:7.0f} {r.rd:5.0f}  {team_name(conn, tid)}")
    conn.close()


if __name__ == "__main__":
    main()
