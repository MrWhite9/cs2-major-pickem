"""Offline tests for match parsing/upsert (no network)."""
from __future__ import annotations

from pickem.data.db import connect, ingest_matches

SAMPLE_MATCH = {
    "id": 1200600,
    "tournament_id": 16969,
    "name": "BESTIA Academy vs RED Canids Academy",
    "slug": "bestia-vs-red-canids",
    "match_type": "best_of",
    "number_of_games": 3,
    "status": "finished",
    "scheduled_at": "2025-06-24T21:00:00Z",
    "begin_at": "2025-06-24T21:05:00Z",
    "end_at": "2025-06-24T22:00:00Z",
    "winner_id": 135505,
    "modified_at": "2025-06-24T22:00:00Z",
    "tournament": {"id": 16969, "name": "Playoffs", "tier": "d", "region": "SA",
                   "begin_at": "2025-06-24T21:00:00Z", "has_bracket": True,
                   "league": {"name": "Gamers Club Liga Serie A"}},
    "opponents": [
        {"opponent": {"id": 135743, "name": "BESTIA Academy", "acronym": "BES.A"}},
        {"opponent": {"id": 135505, "name": "RED Canids Academy", "acronym": "RED.A"}},
    ],
    "results": [
        {"team_id": 135743, "score": 0},
        {"team_id": 135505, "score": 2},
    ],
    "games": [
        {"id": 1, "position": 1, "status": "finished", "complete": True,
         "forfeit": False, "winner": {"id": 135505}},
        {"id": 2, "position": 2, "status": "finished", "complete": True,
         "forfeit": False, "winner": {"id": 135505}},
    ],
}


def test_ingest_parses_match_teams_and_scores():
    conn = connect(":memory:")
    n = ingest_matches(conn, [SAMPLE_MATCH])
    assert n == 1

    row = conn.execute(
        "SELECT team_a_id, team_b_id, score_a, score_b, winner_id, n_games "
        "FROM matches WHERE id = ?", (1200600,)
    ).fetchone()
    assert row["team_a_id"] == 135743
    assert row["team_b_id"] == 135505
    assert row["score_a"] == 0
    assert row["score_b"] == 2
    assert row["winner_id"] == 135505
    assert row["n_games"] == 3

    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
    assert conn.execute(
        "SELECT name FROM tournaments WHERE id = 16969"
    ).fetchone()["name"] == "Playoffs"


def test_ingest_is_idempotent():
    conn = connect(":memory:")
    ingest_matches(conn, [SAMPLE_MATCH])
    ingest_matches(conn, [SAMPLE_MATCH])  # re-ingest must not duplicate
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2


def test_score_mapping_survives_opponent_order():
    """score_a/score_b must track the actual team in each slot."""
    conn = connect(":memory:")
    ingest_matches(conn, [SAMPLE_MATCH])
    row = conn.execute(
        "SELECT team_a_id, score_a, team_b_id, score_b FROM matches WHERE id = ?",
        (1200600,)
    ).fetchone()
    # team_a is BESTIA (0), team_b is RED (2)
    assert (row["team_a_id"], row["score_a"]) == (135743, 0)
    assert (row["team_b_id"], row["score_b"]) == (135505, 2)
