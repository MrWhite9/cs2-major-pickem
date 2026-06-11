"""Offline tests for bo3.gg veto parsing (no network)."""
from __future__ import annotations

from pickem.data.bo3 import parse_vetos

# A Bo3 match_maps detail: team1=667 (ps 3455), team2=8171 (ps NULL on bo3).
# Mirrors the real shape — bans (choice_type 2), picks (1), decider (3).
SAMPLE_DETAIL = {
    "team1": {"ps_id": 3455},
    "maps_score": [True, False, True],
    "match_maps": [
        {"order": 1, "choice_type": 2, "team_id": 667,
         "teams": {"ps_id": 3455}, "maps": {"map_name": "de_nuke"}},
        {"order": 2, "choice_type": 2, "team_id": 8171,
         "teams": {"ps_id": None}, "maps": {"map_name": "de_inferno"}},
        {"order": 3, "choice_type": 1, "team_id": 667,
         "teams": {"ps_id": 3455}, "maps": {"map_name": "de_mirage"}},
        {"order": 4, "choice_type": 1, "team_id": 8171,
         "teams": {"ps_id": None}, "maps": {"map_name": "de_ancient"}},
        {"order": 5, "choice_type": 2, "team_id": 667,
         "teams": {"ps_id": 3455}, "maps": {"map_name": "de_dust2"}},
        {"order": 6, "choice_type": 2, "team_id": 8171,
         "teams": {"ps_id": None}, "maps": {"map_name": "de_overpass"}},
        {"order": 7, "choice_type": 3, "team_id": None,
         "teams": {}, "maps": {"map_name": "de_anubis"}},
    ],
}

BO3_TO_PS = {667: 3455, 8171: 133719}


def test_parse_vetos_orders_and_labels():
    steps = parse_vetos(SAMPLE_DETAIL, BO3_TO_PS)
    assert [s["order_idx"] for s in steps] == [1, 2, 3, 4, 5, 6, 7]
    assert [s["action"] for s in steps] == \
        ["ban", "ban", "pick", "pick", "ban", "ban", "decider"]
    assert [s["played"] for s in steps] == [0, 0, 1, 1, 0, 0, 1]
    assert [s["map_name"] for s in steps[:2]] == ["de_nuke", "de_inferno"]


def test_parse_vetos_resolves_actor_without_bo3_ps_id():
    """A team with ps_id NULL on bo3 must still attribute via the id map."""
    steps = parse_vetos(SAMPLE_DETAIL, BO3_TO_PS)
    by_order = {s["order_idx"]: s for s in steps}
    assert by_order[2]["actor_ps_id"] == 133719   # resolved via bo3_to_ps
    assert by_order[6]["actor_ps_id"] == 133719
    assert by_order[1]["actor_ps_id"] == 3455
    assert by_order[7]["actor_ps_id"] is None      # decider has no actor


def test_parse_vetos_falls_back_to_nested_ps_id():
    """Without an id map, the nested teams.ps_id still resolves known teams."""
    steps = parse_vetos(SAMPLE_DETAIL)
    by_order = {s["order_idx"]: s for s in steps}
    assert by_order[1]["actor_ps_id"] == 3455
    assert by_order[2]["actor_ps_id"] is None       # no ps_id available anywhere
