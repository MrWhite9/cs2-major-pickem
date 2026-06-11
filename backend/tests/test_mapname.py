"""Offline tests for per-map result alignment and rating build."""
from __future__ import annotations

from pickem.data.db import connect
from pickem.ratings.mapname import build_map_ratings, load_map_results


def _seed(conn):
    conn.execute("INSERT INTO teams (id, name) VALUES (1,'A'),(2,'B')")
    conn.execute(
        "INSERT INTO tournaments (id, name, tier) VALUES (9,'T','s')")

    def match(mid, w, n, games, vetos):
        conn.execute(
            "INSERT INTO matches (id, tournament_id, n_games, status, begin_at, "
            "team_a_id, team_b_id, winner_id) VALUES (?,9,?,?,?,1,2,?)",
            (mid, n, "finished", "2025-01-01T00:00:00Z", w))
        for pos, gw in games:
            conn.execute(
                "INSERT INTO games (id, match_id, position, winner_id, forfeit) "
                "VALUES (?,?,?,?,0)", (mid * 10 + pos, mid, pos, gw))
        for order_idx, action, actor, mp, played in vetos:
            conn.execute(
                "INSERT INTO match_vetos (match_id, order_idx, action, "
                "actor_ps_id, map_name, played) VALUES (?,?,?,?,?,?)",
                (mid, order_idx, action, actor, mp, played))

    # 2-1 series: A wins g1(mirage) & g3(decider ancient), loses g2(nuke).
    match(100, 1, 3,
          games=[(1, 1), (2, 2), (3, 1)],
          vetos=[(1, "ban", 1, "de_dust2", 0), (2, "ban", 2, "de_inferno", 0),
                 (3, "pick", 1, "de_mirage", 1), (4, "pick", 2, "de_nuke", 1),
                 (5, "ban", 1, "de_overpass", 0), (6, "ban", 2, "de_anubis", 0),
                 (7, "decider", None, "de_ancient", 1)])
    # 2-0 series: B wins both played maps; decider train must be dropped.
    match(101, 2, 3,
          games=[(1, 2), (2, 2)],
          vetos=[(1, "ban", 1, "de_dust2", 0), (2, "ban", 2, "de_inferno", 0),
                 (3, "pick", 1, "de_mirage", 1), (4, "pick", 2, "de_nuke", 1),
                 (5, "ban", 1, "de_overpass", 0), (6, "ban", 2, "de_anubis", 0),
                 (7, "decider", None, "de_train", 1)])
    conn.commit()


def test_alignment_pairs_map_to_position_winner():
    conn = connect(":memory:")
    _seed(conn)
    rows = load_map_results(conn, "2025-02-01T00:00:00Z")
    got = {(r["map_name"], r["w"]) for r in rows}
    # 2-1 match: mirage->A(1), nuke->B(2), ancient->A(1)
    assert ("de_mirage", 1) in got
    assert ("de_nuke", 2) in got
    assert ("de_ancient", 1) in got


def test_2_0_series_drops_unplayed_decider():
    conn = connect(":memory:")
    _seed(conn)
    rows = load_map_results(conn, "2025-02-01T00:00:00Z")
    maps = [r["map_name"] for r in rows]
    assert "de_train" not in maps                 # decider never played
    assert maps.count("de_mirage") == 2           # both matches' g1 pick
    # both played maps of match 101 went to B
    m101 = [(r["map_name"], r["w"]) for r in rows
            if r["map_name"] in ("de_mirage", "de_nuke")]
    assert ("de_nuke", 2) in m101


def test_build_map_ratings_smoke():
    conn = connect(":memory:")
    _seed(conn)
    out = build_map_ratings("2025-02-01", conn=conn, save=True)
    assert "de_mirage" in out and 1 in out["de_mirage"]
    # persisted
    n = conn.execute("SELECT COUNT(*) FROM map_ratings").fetchone()[0]
    assert n > 0
