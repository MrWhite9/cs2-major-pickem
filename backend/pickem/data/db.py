"""SQLite connection, schema setup, and upsert helpers."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import DB_PATH

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open a connection with sane defaults and the schema applied."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def _upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    cols = list(row.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[c] for c in cols])


def upsert_team(conn: sqlite3.Connection, team: dict[str, Any] | None) -> int | None:
    if not team:
        return None
    _upsert(conn, "teams", {
        "id": team["id"],
        "name": team.get("name"),
        "acronym": team.get("acronym"),
        "slug": team.get("slug"),
        "location": team.get("location"),
        "image_url": team.get("image_url"),
        "modified_at": team.get("modified_at"),
    })
    return team["id"]


def upsert_tournament(conn: sqlite3.Connection, t: dict[str, Any] | None) -> int | None:
    if not t:
        return None
    league = t.get("league") or {}
    _upsert(conn, "tournaments", {
        "id": t["id"],
        "name": t.get("name"),
        "slug": t.get("slug"),
        "tier": t.get("tier"),
        "region": t.get("region"),
        "serie_id": t.get("serie_id"),
        "league_id": t.get("league_id"),
        "league_name": league.get("name"),
        "begin_at": t.get("begin_at"),
        "end_at": t.get("end_at"),
        "has_bracket": 1 if t.get("has_bracket") else 0,
    })
    return t["id"]


def upsert_match(conn: sqlite3.Connection, m: dict[str, Any]) -> None:
    """Insert/update a match plus its referenced teams, tournament, and games."""
    upsert_tournament(conn, m.get("tournament"))

    opponents = m.get("opponents") or []
    team_ids: list[int | None] = []
    for opp in opponents[:2]:
        team_ids.append(upsert_team(conn, opp.get("opponent")))
    while len(team_ids) < 2:
        team_ids.append(None)

    # results carry per-team scores keyed by team_id
    scores = {r["team_id"]: r["score"] for r in (m.get("results") or []) if r.get("team_id")}

    _upsert(conn, "matches", {
        "id": m["id"],
        "tournament_id": m.get("tournament_id"),
        "name": m.get("name"),
        "slug": m.get("slug"),
        "match_type": m.get("match_type"),
        "n_games": m.get("number_of_games"),
        "status": m.get("status"),
        "scheduled_at": m.get("scheduled_at"),
        "begin_at": m.get("begin_at"),
        "end_at": m.get("end_at"),
        "team_a_id": team_ids[0],
        "team_b_id": team_ids[1],
        "score_a": scores.get(team_ids[0]) if team_ids[0] else None,
        "score_b": scores.get(team_ids[1]) if team_ids[1] else None,
        "winner_id": m.get("winner_id"),
        "modified_at": m.get("modified_at"),
    })

    for g in m.get("games") or []:
        if g.get("id") is None:
            continue
        winner = g.get("winner") or {}
        _upsert(conn, "games", {
            "id": g["id"],
            "match_id": m["id"],
            "position": g.get("position"),
            "status": g.get("status"),
            "complete": 1 if g.get("complete") else 0,
            "forfeit": 1 if g.get("forfeit") else 0,
            "winner_id": winner.get("id"),
        })


def set_log(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO ingest_log (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, datetime.now(timezone.utc).isoformat()),
    )


def get_log(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM ingest_log WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def ingest_matches(conn: sqlite3.Connection, matches: Iterable[dict[str, Any]]) -> int:
    n = 0
    for m in matches:
        upsert_match(conn, m)
        n += 1
    conn.commit()
    return n
