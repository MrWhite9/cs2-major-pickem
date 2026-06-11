"""Backfill historical CS2 match results into the local SQLite store.

Usage:
    python -m pickem.data.backfill --months 12
    python -m pickem.data.backfill --since 2024-01-01 --until 2025-01-01
    python -m pickem.data.backfill --tournament 16969
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from .db import connect, get_log, ingest_matches, set_log
from .pandascore import PandaScore


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def backfill_range(since: str, until: str, batch_log_every: int = 500) -> int:
    client = PandaScore()
    conn = connect()
    total = 0
    buffer: list[dict] = []
    try:
        for match in client.past_matches(since=since, until=until):
            buffer.append(match)
            if len(buffer) >= batch_log_every:
                total += ingest_matches(conn, buffer)
                last = buffer[-1].get("begin_at") or ""
                set_log(conn, "matches_through", last)
                conn.commit()
                print(f"  ingested {total} matches (through {last})")
                buffer.clear()
        if buffer:
            total += ingest_matches(conn, buffer)
            last = buffer[-1].get("begin_at") or until
            set_log(conn, "matches_through", last)
            conn.commit()
    finally:
        conn.close()
    return total


def backfill_tournament(tournament_id: int) -> int:
    client = PandaScore()
    conn = connect()
    try:
        n = ingest_matches(conn, client.tournament_matches(tournament_id))
    finally:
        conn.close()
    print(f"Ingested {n} matches for tournament {tournament_id}")
    return n


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill CS2 match history.")
    p.add_argument("--months", type=int, help="Backfill the last N months.")
    p.add_argument("--since", help="ISO date, e.g. 2024-01-01.")
    p.add_argument("--until", help="ISO date, e.g. 2025-01-01.")
    p.add_argument("--tournament", type=int, help="Backfill a single tournament id.")
    args = p.parse_args()

    if args.tournament:
        backfill_tournament(args.tournament)
        return

    if args.months:
        until = datetime.now(timezone.utc)
        since = until - timedelta(days=30 * args.months)
        since_s, until_s = _iso(since), _iso(until)
    elif args.since:
        since_s = args.since
        until_s = args.until or _iso(datetime.now(timezone.utc))
    else:
        p.error("provide --months, --since/--until, or --tournament")
        return

    print(f"Backfilling CS2 matches {since_s} -> {until_s}")
    total = backfill_range(since_s, until_s)
    print(f"Done. {total} matches in store.")


if __name__ == "__main__":
    main()
