"""Per-map-name Glicko-2 ratings with an empirical-Bayes shrinkage prior.

A team's strength on, say, de_nuke is rated by replaying only its de_nuke games
through Glicko-2 — but seeded from the team's *global* map rating as a prior
(a moderate starting RD), so a team with little history on a map stays near its
overall map skill and only moves as map-specific evidence accumulates. This
keeps thin per-map samples from producing wild ratings.

Map names come from bo3.gg veto data (`match_vetos`); per-map winners come from
our authoritative PandaScore `games` (winner by position). The i-th played map
(veto pick order, decider last) is aligned to the i-th game (by position).

Usage:
    python -m pickem.ratings.mapname --cutoff 2026-06-11
    python -m pickem.ratings.mapname --cutoff 2026-06-11 --team 3455   # inspect
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime

from ..data.db import connect
from .build import DEFAULT_TIER_WEIGHT, TIER_WEIGHT, build_ratings
from .glicko2 import DEFAULT_VOL, Rating, Result, rate

# Prior strength: starting RD for a team's map rating, anchored at its global
# map rating. Lower => stronger shrinkage toward overall skill. Calibrated in P4.
PRIOR_RD = 180.0
RATING_SYSTEM = "glicko2_map"   # global anchor system


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load_map_results(conn: sqlite3.Connection, cutoff_iso: str):
    """Rows of (a, b, w, ts, tier, map_name) for each named, played map.

    Aligns the n-th played veto map (pick order, decider last) to the n-th
    game by position; a 2-0 series simply drops the unplayed decider.
    """
    return conn.execute(
        """
        WITH pv AS (
            SELECT match_id, map_name,
                   ROW_NUMBER() OVER (PARTITION BY match_id ORDER BY order_idx) rn
            FROM match_vetos WHERE played = 1
        ),
        pg AS (
            SELECT match_id, winner_id,
                   ROW_NUMBER() OVER (PARTITION BY match_id ORDER BY position) rn
            FROM games WHERE winner_id IS NOT NULL AND forfeit = 0
        )
        SELECT m.team_a_id AS a, m.team_b_id AS b, pg.winner_id AS w,
               m.begin_at AS ts, COALESCE(tr.tier, '?') AS tier, pv.map_name
        FROM pg
        JOIN pv ON pv.match_id = pg.match_id AND pv.rn = pg.rn
        JOIN matches m ON m.id = pg.match_id
        LEFT JOIN tournaments tr ON tr.id = m.tournament_id
        WHERE m.begin_at IS NOT NULL AND m.begin_at < ?
          AND m.team_a_id IS NOT NULL AND m.team_b_id IS NOT NULL
          AND pg.winner_id IN (m.team_a_id, m.team_b_id)
        ORDER BY m.begin_at
        """,
        (cutoff_iso,),
    ).fetchall()


def _rate_one_map(rows, global_ratings: dict[int, Rating], prior_rd: float,
                  period_days: int, tier_weights: dict[str, float]):
    """Glicko-2 over one map's results, seeded from the global-map prior."""
    period0 = _parse(rows[0]["ts"])

    def pidx(ts): return (_parse(ts) - period0).days // period_days

    def prior(team: int) -> Rating:
        g = global_ratings.get(team)
        base = g.rating if g else Rating().rating
        return Rating(base, prior_rd, DEFAULT_VOL)

    ratings: dict[int, Rating] = {}
    n_games: dict[int, int] = {}
    periods: dict[int, list] = {}
    for r in rows:
        periods.setdefault(pidx(r["ts"]), []).append(r)

    for p in sorted(periods):
        snapshot = dict(ratings)
        played: set[int] = set()
        pending: dict[int, list[Result]] = {}
        for g in periods[p]:
            a, b, w = g["a"], g["b"], g["w"]
            weight = tier_weights.get(g["tier"], DEFAULT_TIER_WEIGHT)
            ra = snapshot.get(a) or prior(a)
            rb = snapshot.get(b) or prior(b)
            sa = 1.0 if w == a else 0.0
            pending.setdefault(a, []).append(Result(rb, sa, weight))
            pending.setdefault(b, []).append(Result(ra, 1.0 - sa, weight))
            played.update((a, b))
            n_games[a] = n_games.get(a, 0) + 1
            n_games[b] = n_games.get(b, 0) + 1
        for tid, results in pending.items():
            ratings[tid] = rate(ratings.get(tid) or prior(tid), results)
        for tid in list(ratings):
            if tid not in played:
                ratings[tid] = rate(ratings[tid], [])
    return ratings, n_games


def build_map_ratings(cutoff: str, conn: sqlite3.Connection | None = None,
                      period_days: int = 7, prior_rd: float = PRIOR_RD,
                      tier_weights: dict[str, float] | None = None,
                      save: bool = True) -> dict[str, dict[int, Rating]]:
    """Per-map-name ratings frozen at `cutoff`. Returns {map_name: {team: Rating}}."""
    own = conn is None
    conn = conn or connect()
    tier_weights = tier_weights or TIER_WEIGHT
    cutoff_iso = cutoff if "T" in cutoff else f"{cutoff}T00:00:00Z"

    # Global map ratings = the shrinkage prior (built fresh, not persisted here).
    global_ratings = build_ratings(cutoff, conn=conn, level="map", save=False)

    rows = load_map_results(conn, cutoff_iso)
    by_map: dict[str, list] = {}
    for r in rows:
        by_map.setdefault(r["map_name"], []).append(r)

    out: dict[str, dict[int, Rating]] = {}
    counts: dict[str, dict[int, int]] = {}
    for map_name, mrows in by_map.items():
        ratings, n_games = _rate_one_map(
            mrows, global_ratings, prior_rd, period_days, tier_weights)
        out[map_name] = ratings
        counts[map_name] = n_games

    if save:
        _save(conn, out, counts, cutoff_iso)
    if own:
        conn.close()
    return out


def _save(conn: sqlite3.Connection, out, counts, as_of: str) -> None:
    conn.execute("DELETE FROM map_ratings WHERE as_of = ?", (as_of,))
    payload = [
        (tid, as_of, map_name, r.rating, r.rd, r.vol, counts[map_name].get(tid, 0))
        for map_name, ratings in out.items()
        for tid, r in ratings.items()
    ]
    conn.executemany(
        "INSERT INTO map_ratings (team_id, as_of, map_name, rating, deviation, "
        "volatility, n_games) VALUES (?, ?, ?, ?, ?, ?, ?)", payload,
    )
    conn.commit()


# --- query helpers (consumed by the veto sim in P3) -----------------------

def map_rating(conn: sqlite3.Connection, team_id: int, as_of_iso: str,
               map_name: str, global_fallback: Rating | None = None
               ) -> Rating | None:
    """A team's rating on `map_name`, or the global-map fallback if unrated."""
    row = conn.execute(
        "SELECT rating, deviation, volatility FROM map_ratings "
        "WHERE team_id = ? AND as_of = ? AND map_name = ?",
        (team_id, as_of_iso, map_name),
    ).fetchone()
    if row:
        return Rating(row["rating"], row["deviation"], row["volatility"])
    return global_fallback


def main() -> None:
    p = argparse.ArgumentParser(description="Build per-map-name Glicko-2 ratings.")
    p.add_argument("--cutoff", required=True)
    p.add_argument("--prior-rd", type=float, default=PRIOR_RD)
    p.add_argument("--team", type=int, help="inspect one team's map ratings")
    p.add_argument("--no-save", action="store_true")
    args = p.parse_args()

    conn = connect()
    out = build_map_ratings(args.cutoff, conn=conn, prior_rd=args.prior_rd,
                            save=not args.no_save)
    total = sum(len(v) for v in out.values())
    print(f"Rated {total} (team,map) pairs across {len(out)} maps as of {args.cutoff}.")

    if args.team:
        name = conn.execute("SELECT name FROM teams WHERE id = ?",
                            (args.team,)).fetchone()
        print(f"\n{name['name'] if name else args.team} per-map rating "
              f"(prior_rd={args.prior_rd}):")
        glob = build_ratings(args.cutoff, conn=conn, level="map", save=False)
        g = glob.get(args.team)
        print(f"  global map rating: {g.rating:.0f}" if g else "  (no global rating)")
        rows = []
        for map_name, ratings in out.items():
            r = ratings.get(args.team)
            if r:
                rows.append((map_name, r.rating, r.rd))
        for map_name, rt, rd in sorted(rows, key=lambda x: x[1], reverse=True):
            delta = rt - (g.rating if g else 1500)
            print(f"  {map_name:12} {rt:6.0f} (RD {rd:3.0f}, {delta:+.0f} vs global)")
    conn.close()


if __name__ == "__main__":
    main()
