"""Backfill bo3.gg veto + map data for matches already in our store.

Pipeline:
  1. build/refresh the PandaScore<->bo3 team id map (exact ps_id, then name).
  2. for each of our matches (with both teams + a date), find the bo3 match,
     pull its detail, and store the veto sequence + bo3 link.

Usage:
    python -m pickem.data.bo3_ingest --tournament 20708 20709 21115
    python -m pickem.data.bo3_ingest --major cologne --major budapest --report
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone

from .bo3 import NAME_ALIASES, Bo3, parse_vetos
from .db import connect, set_log

# Major -> stage tournament ids (kept here to avoid importing the API layer).
MAJOR_TOURNAMENTS = {
    "cologne": [20708, 20709, 21115],
    "budapest": [17897, 17898, 17899],
}


# --- team id map ----------------------------------------------------------

def needed_team_ids(conn: sqlite3.Connection, tournament_ids: list[int]) -> dict[int, str]:
    ids: dict[int, str] = {}
    for tid in tournament_ids:
        for r in conn.execute(
            "SELECT DISTINCT team_a_id a, team_b_id b FROM matches WHERE tournament_id = ?",
            (tid,),
        ):
            for t in (r["a"], r["b"]):
                if t is not None:
                    ids[t] = None
    for t in list(ids):
        row = conn.execute("SELECT name FROM teams WHERE id = ?", (t,)).fetchone()
        ids[t] = row["name"] if row else str(t)
    return ids


def _store_team_map(conn: sqlite3.Connection, ps_id: int, bo3: dict, method: str) -> None:
    conn.execute(
        "INSERT INTO team_id_map (ps_id, bo3_id, bo3_name, bo3_slug, method) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(ps_id) DO UPDATE SET "
        "bo3_id=excluded.bo3_id, bo3_name=excluded.bo3_name, "
        "bo3_slug=excluded.bo3_slug, method=excluded.method",
        (ps_id, bo3["id"], bo3.get("name"), bo3.get("slug"), method),
    )


def build_team_map(conn: sqlite3.Connection, client: Bo3,
                   needed: dict[int, str]) -> dict[int, int]:
    """Ensure every needed ps_id maps to a bo3 id. Returns ps_id -> bo3_id."""
    have = {r["ps_id"]: r["bo3_id"] for r in conn.execute(
        "SELECT ps_id, bo3_id FROM team_id_map")}
    missing = {ps: name for ps, name in needed.items() if ps not in have}

    if missing:
        # Pass 1: exact ps_id from a single full team page.
        by_ps = {}
        for t in client.iter_cs_teams():
            ps = t.get("ps_id")
            if ps is not None:
                by_ps[int(ps)] = t
        for ps in list(missing):
            if ps in by_ps:
                _store_team_map(conn, ps, by_ps[ps], "ps_id")
                have[ps] = by_ps[ps]["id"]
                del missing[ps]

        # Pass 2: name / alias lookup for the rest (newer orgs, ps_id NULL).
        for ps, name in list(missing.items()):
            for candidate, method in ((NAME_ALIASES.get(ps), "alias"), (name, "name")):
                if not candidate:
                    continue
                t = client.team_by_name(candidate)
                if t:
                    _store_team_map(conn, ps, t, method)
                    have[ps] = t["id"]
                    del missing[ps]
                    break
        conn.commit()

    if missing:
        names = ", ".join(f"{n} ({ps})" for ps, n in missing.items())
        print(f"  WARNING: {len(missing)} team(s) unmatched on bo3: {names}")
    return {ps: have[ps] for ps in needed if ps in have}


# --- veto ingest ----------------------------------------------------------

def _store_match_veto(conn: sqlite3.Connection, match_id: int, found: dict,
                      detail: dict, bo3_to_ps: dict[int, int]) -> bool:
    """Persist one match's veto steps + bo3 link. Returns True if anything stored."""
    steps = parse_vetos(detail, bo3_to_ps)
    if not steps:
        return False
    conn.execute("DELETE FROM match_vetos WHERE match_id = ?", (match_id,))
    for s in steps:
        conn.execute(
            "INSERT INTO match_vetos (match_id, order_idx, action, actor_ps_id, "
            "map_name, played) VALUES (?, ?, ?, ?, ?, ?)",
            (match_id, s["order_idx"], s["action"], s["actor_ps_id"],
             s["map_name"], s["played"]),
        )
    conn.execute(
        "INSERT INTO match_bo3 (match_id, bo3_id, bo3_slug, bo3_team1_ps, "
        "maps_score, fetched_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(match_id) DO UPDATE SET bo3_id=excluded.bo3_id, "
        "bo3_slug=excluded.bo3_slug, bo3_team1_ps=excluded.bo3_team1_ps, "
        "maps_score=excluded.maps_score, fetched_at=excluded.fetched_at",
        (match_id, found["id"], found["slug"],
         bo3_to_ps.get(detail.get("team1_id")),
         json.dumps(detail.get("maps_score")),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return True


def ingest_match_vetos(conn: sqlite3.Connection, client: Bo3, match: sqlite3.Row,
                       ps_to_bo3: dict[int, int]) -> str:
    """Find + store one match's veto (our-match-first). Returns a status string."""
    a, b = match["team_a_id"], match["team_b_id"]
    date = (match["begin_at"] or match["scheduled_at"] or "")[:10]
    if a is None or b is None or not date:
        return "skip_no_teams"
    if a not in ps_to_bo3 or b not in ps_to_bo3:
        return "skip_no_bo3_team"

    found = client.find_match(ps_to_bo3[a], ps_to_bo3[b], date)
    if not found:
        return "miss_no_match"
    detail = client.match_detail(found["slug"])
    bo3_to_ps = {ps_to_bo3[a]: a, ps_to_bo3[b]: b}
    if not _store_match_veto(conn, match["id"], found, detail, bo3_to_ps):
        return "miss_no_veto"
    return "ok"


# --- broad history ingest (team-centric, for map-name ratings) ------------

def populate_team_map_full(conn: sqlite3.Connection, client: Bo3) -> int:
    """Map every bo3 CS team whose ps_id is one of our teams (one full sweep).

    This lets the history ingest resolve arbitrary opponents, not just the
    Major rosters. Newer teams (ps_id NULL on bo3) stay unmapped.
    """
    our_ids = {r["id"] for r in conn.execute("SELECT id FROM teams")}
    have = {r["ps_id"] for r in conn.execute("SELECT ps_id FROM team_id_map")}
    n = 0
    for t in client.iter_cs_teams():
        ps = t.get("ps_id")
        if ps is not None and int(ps) in our_ids and int(ps) not in have:
            _store_team_map(conn, int(ps), t, "ps_id")
            have.add(int(ps))
            n += 1
    conn.commit()
    return n


def _bo3_to_ps(conn: sqlite3.Connection) -> dict[int, int]:
    return {r["bo3_id"]: r["ps_id"]
            for r in conn.execute("SELECT ps_id, bo3_id FROM team_id_map")}


def find_our_match(conn: sqlite3.Connection, ps_a: int, ps_b: int,
                   date: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM matches WHERE winner_id IS NOT NULL "
        "AND ((team_a_id=? AND team_b_id=?) OR (team_a_id=? AND team_b_id=?)) "
        "AND substr(COALESCE(begin_at, scheduled_at), 1, 10) = ?",
        (ps_a, ps_b, ps_b, ps_a, date),
    ).fetchone()
    return row["id"] if row else None


def ingest_team_history(conn: sqlite3.Connection, bo3_team_ids: list[int],
                        since: str, until: str, client: Bo3 | None = None,
                        skip_existing: bool = True) -> dict[str, int]:
    """Ingest veto/map data for matches (already in our DB) that the given bo3
    teams played in [since, until]. Joins each bo3 match back to our match id
    by team pair + date, so only matches we already store get enriched."""
    client = client or Bo3()
    b2p = _bo3_to_ps(conn)
    done = {r["match_id"] for r in conn.execute("SELECT match_id FROM match_bo3")}
    stats: dict[str, int] = {}
    seen: set[int] = set()

    def bump(k):
        stats[k] = stats.get(k, 0) + 1

    for i, bid in enumerate(bo3_team_ids, 1):
        for m in client.iter_team_matches(bid, since, until):
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            ps1, ps2 = b2p.get(m.get("team1_id")), b2p.get(m.get("team2_id"))
            if ps1 is None or ps2 is None:
                bump("skip_unmapped_team"); continue
            mid = find_our_match(conn, ps1, ps2, (m.get("start_date") or "")[:10])
            if mid is None:
                bump("miss_not_in_db"); continue
            if skip_existing and mid in done:
                bump("already"); continue
            detail = client.match_detail(m["slug"])
            if _store_match_veto(conn, mid, m, detail,
                                 {m["team1_id"]: ps1, m["team2_id"]: ps2}):
                done.add(mid); bump("ok")
            else:
                bump("no_veto")
        print(f"    [{i}/{len(bo3_team_ids)}] team {bid}: "
              + ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    set_log(conn, "bo3_history_through", datetime.now(timezone.utc).isoformat())
    return stats


def major_bo3_team_ids(conn: sqlite3.Connection, major: str) -> list[int]:
    needed = needed_team_ids(conn, MAJOR_TOURNAMENTS[major])
    b2p = _bo3_to_ps(conn)
    ps2b = {ps: b for b, ps in b2p.items()}
    return [ps2b[ps] for ps in needed if ps in ps2b]


def ingest_tournaments(conn: sqlite3.Connection, tournament_ids: list[int],
                       client: Bo3 | None = None) -> dict[str, int]:
    client = client or Bo3()
    needed = needed_team_ids(conn, tournament_ids)
    ps_to_bo3 = build_team_map(conn, client, needed)
    print(f"  team map: {len(ps_to_bo3)}/{len(needed)} teams resolved on bo3")

    stats: dict[str, int] = {}
    for tid in tournament_ids:
        rows = conn.execute(
            "SELECT id, team_a_id, team_b_id, begin_at, scheduled_at "
            "FROM matches WHERE tournament_id = ? ORDER BY begin_at", (tid,)
        ).fetchall()
        for m in rows:
            status = ingest_match_vetos(conn, client, m, ps_to_bo3)
            stats[status] = stats.get(status, 0) + 1
    set_log(conn, "bo3_veto_through",
            datetime.now(timezone.utc).isoformat())
    return stats


# --- reporting ------------------------------------------------------------

def coverage_report(conn: sqlite3.Connection, tournament_ids: list[int]) -> None:
    print("\nbo3 veto coverage")
    print("=" * 56)
    for tid in tournament_ids:
        total = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE tournament_id = ?", (tid,)
        ).fetchone()[0]
        withveto = conn.execute(
            "SELECT COUNT(DISTINCT v.match_id) FROM match_vetos v "
            "JOIN matches m ON m.id = v.match_id WHERE m.tournament_id = ?", (tid,)
        ).fetchone()[0]
        name = conn.execute(
            "SELECT name FROM tournaments WHERE id = ?", (tid,)
        ).fetchone()
        label = name["name"] if name else tid
        print(f"  {label!s:32} {withveto:>3}/{total:<3} matches have veto")
    nsteps = conn.execute("SELECT COUNT(*) FROM match_vetos").fetchone()[0]
    nmaps = conn.execute(
        "SELECT COUNT(DISTINCT map_name) FROM match_vetos").fetchone()[0]
    print("-" * 56)
    print(f"  {nsteps} veto steps stored across {nmaps} distinct maps")


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill bo3.gg veto/map data.")
    p.add_argument("--tournament", type=int, nargs="*", default=[],
                   help="tournament ids to ingest (stage matches)")
    p.add_argument("--major", action="append", default=[],
                   choices=list(MAJOR_TOURNAMENTS), help="ingest a Major's stages")
    p.add_argument("--history-major", action="append", default=[],
                   choices=list(MAJOR_TOURNAMENTS),
                   help="ingest the Major rosters' broad match history (for map ratings)")
    p.add_argument("--since", help="history window start (YYYY-MM-DD)")
    p.add_argument("--until", help="history window end (YYYY-MM-DD)")
    p.add_argument("--report", action="store_true",
                   help="print stage coverage and exit (no fetching)")
    args = p.parse_args()

    conn = connect()

    if args.history_major:
        if not (args.since and args.until):
            p.error("--history-major requires --since and --until")
        client = Bo3()
        added = populate_team_map_full(conn, client)
        print(f"  team map: +{added} teams from full bo3 sweep")
        for mk in args.history_major:
            ids = major_bo3_team_ids(conn, mk)
            print(f"  history {mk}: {len(ids)} rosters, window {args.since}..{args.until}")
            stats = ingest_team_history(conn, ids, args.since, args.until, client)
            print(f"  {mk} done:", ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))
        conn.close()
        return

    tids = list(args.tournament)
    for mk in args.major:
        tids += MAJOR_TOURNAMENTS[mk]
    tids = list(dict.fromkeys(tids))
    if not tids:
        p.error("specify --tournament, --major, or --history-major")

    if not args.report:
        stats = ingest_tournaments(conn, tids)
        print("  ingest:", ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    coverage_report(conn, tids)
    conn.close()


if __name__ == "__main__":
    main()
