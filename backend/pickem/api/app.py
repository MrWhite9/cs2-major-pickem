"""FastAPI app: serves stage probabilities, recommended picks, and the backtest.

Run:
    cd backend
    uvicorn pickem.api.app:app --reload
then open http://127.0.0.1:8000/
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from ..backtest.harness import actual_outcome
from ..data.db import connect
from ..majors import DEFAULT_MAJOR, MAJORS, StageSpec, stage_format
from ..optimize.format import PickemFormat, score
from ..optimize.optimizer import StageOptimizer
from ..ratings.build import build_ratings
from ..ratings.mapname import build_map_ratings
from ..sim.montecarlo import run, stage_participants
from ..sim.veto import build_series_matrices

FRONTEND_DIR = Path(__file__).resolve().parents[2].parent / "frontend"
RATING_SYSTEM = "glicko2_map"

app = FastAPI(title="CS2 Major Pick'Em Predictor")


def _name(conn, tid: int) -> str:
    r = conn.execute("SELECT name FROM teams WHERE id = ?", (tid,)).fetchone()
    return r["name"] if r else str(tid)


def _stage_by_tid(major_key: str, tid: int) -> StageSpec:
    major = MAJORS.get(major_key)
    if not major:
        raise HTTPException(404, f"unknown major: {major_key}")
    for s in major.stages:
        if s.tournament_id == tid:
            return s
    raise HTTPException(404, f"stage {tid} not in major {major_key}")


def _ensure_ratings(cutoff: str) -> None:
    """Build+save map ratings frozen at `cutoff` if they aren't cached yet."""
    iso = cutoff if "T" in cutoff else f"{cutoff}T00:00:00Z"
    conn = connect()
    n = conn.execute(
        "SELECT COUNT(*) FROM ratings WHERE as_of = ? AND system = ?",
        (iso, RATING_SYSTEM),
    ).fetchone()[0]
    m = conn.execute(
        "SELECT COUNT(*) FROM map_ratings WHERE as_of = ?", (iso,)
    ).fetchone()[0]
    conn.close()
    if n == 0:
        build_ratings(cutoff, level="map", save=True)
    if m == 0:
        build_map_ratings(cutoff, save=True)


@lru_cache(maxsize=32)
def _series_prob(major_key: str, tid: int):
    """Cached map-aware series matrices (+veto model) for a stage."""
    spec = _stage_by_tid(major_key, tid)
    _ensure_ratings(spec.cutoff)
    conn = connect()
    parts = stage_participants(conn, tid, spec.cutoff, system=RATING_SYSTEM)
    sp = build_series_matrices(conn, parts, spec.cutoff, tid, n_veto=1200, seed=1)
    conn.close()
    return sp


@lru_cache(maxsize=128)
def _stage_payload(major_key: str, tid: int, threshold: int, n_sims: int,
                   model: str = "flat") -> dict:
    spec = _stage_by_tid(major_key, tid)
    _ensure_ratings(spec.cutoff)
    conn = connect()
    fmt = PickemFormat(threshold=threshold)
    all_bo3, round1 = stage_format(conn, tid)
    parts = stage_participants(conn, tid, spec.cutoff, system=RATING_SYSTEM)
    series_prob = _series_prob(major_key, tid) if model == "map_aware" else None
    summary = run(parts, n=n_sims, seed=1, round1_pairs=round1, all_bo3=all_bo3,
                  series_prob=series_prob)
    team_ids = [p.team_id for p in parts]
    opt = StageOptimizer(summary, team_ids, fmt)
    thr = opt.optimize(seed=1)
    ev = opt.max_ev()
    actual = actual_outcome(conn, tid)
    has_actual = bool(actual.advanced)

    rating = {p.team_id: p.rating for p in parts}
    teams = []
    for t in team_ids:
        teams.append({
            "id": t, "name": _name(conn, t), "rating": round(rating[t].rating),
            "p_advance": round(summary.p_advance[t], 4),
            "p_3_0": round(summary.p_3_0[t], 4),
            "p_0_3": round(summary.p_0_3[t], 4),
            "rec_three_0": t in thr.picks.three_0,
            "rec_advance": t in thr.picks.advance,
            "rec_zero_3": t in thr.picks.zero_3,
            "act_advanced": t in actual.advanced,
            "act_three_oh": t in actual.three_oh,
            "act_zero_three": t in actual.zero_three,
        })
    teams.sort(key=lambda x: x["p_advance"], reverse=True)

    def rec_block(rec):
        c = score(rec.picks, actual, fmt) if has_actual else None
        return {
            "strategy": rec.strategy,
            "three_0": sorted(_name(conn, t) for t in rec.picks.three_0),
            "advance": sorted(_name(conn, t) for t in rec.picks.advance),
            "zero_3": sorted(_name(conn, t) for t in rec.picks.zero_3),
            "p_clear": round(rec.p_clear, 4),
            "e_correct": round(rec.e_correct, 2),
            "correct": c,
            "cleared": (c >= threshold) if has_actual else None,
        }

    payload = {
        "major": major_key, "tournament_id": tid, "label": spec.label,
        "cutoff": spec.cutoff, "threshold": threshold, "n_picks": fmt.n_picks,
        "n_sims": n_sims, "all_bo3": all_bo3, "has_real_seeding": round1 is not None,
        "model": model,
        "has_actual": has_actual, "teams": teams,
        "recommendations": [rec_block(thr), rec_block(ev)],
        "actual": {
            "three_oh": sorted(_name(conn, t) for t in actual.three_oh),
            "zero_three": sorted(_name(conn, t) for t in actual.zero_three),
            "advanced": sorted(_name(conn, t) for t in actual.advanced),
        } if has_actual else None,
    }
    conn.close()
    return payload


@app.get("/api/majors")
def majors():
    return [{"key": m.key, "name": m.name,
             "stages": [{"tournament_id": s.tournament_id, "label": s.label,
                         "cutoff": s.cutoff} for s in m.stages]}
            for m in MAJORS.values()]


@app.get("/api/stage/{tid}")
def stage(tid: int, major: str = DEFAULT_MAJOR, threshold: int = 5,
          sims: int = Query(20000, ge=1000, le=200000),
          model: str = Query("flat", pattern="^(flat|map_aware)$")):
    return _stage_payload(major, tid, threshold, sims, model)


@app.get("/api/veto/{tid}")
def veto(tid: int, a: int, b: int, major: str = DEFAULT_MAJOR, bo: int = 3):
    """Map-aware veto prediction for one matchup: pool play % + per-map win %."""
    import random
    sp = _series_prob(major, tid)
    m = sp.model
    if a not in m.ratings or b not in m.ratings:
        raise HTTPException(404, "team not in this stage")
    rng = random.Random(1)
    pool = m.pool_probabilities(a, b, bo, rng, 3000)
    conn = connect()
    rows = []
    for mp in m.pool:
        rows.append({
            "map": mp.replace("de_", ""),
            "played_pct": round(pool[mp], 3),
            "p_a": round(m._map_p(a, b, mp, None), 3),
            "rating_a": round(m.ratings[a][mp].rating),
            "rating_b": round(m.ratings[b][mp].rating),
        })
    rows.sort(key=lambda r: r["played_pct"], reverse=True)
    out = {
        "tournament_id": tid, "bo": bo,
        "a": {"id": a, "name": _name(conn, a)},
        "b": {"id": b, "name": _name(conn, b)},
        "p_a_series": round(sp(a, b, bo), 3),
        "maps": rows,
    }
    conn.close()
    return out


@app.get("/api/backtest")
def backtest(major: str = DEFAULT_MAJOR, threshold: int = 5,
             sims: int = Query(20000, ge=1000, le=200000)):
    m = MAJORS.get(major)
    if not m:
        raise HTTPException(404, f"unknown major: {major}")
    out = []
    for s in m.stages:
        p = _stage_payload(major, s.tournament_id, threshold, sims)
        out.append({
            "label": s.label, "has_actual": p["has_actual"],
            "recommendations": [
                {"strategy": r["strategy"], "correct": r["correct"],
                 "cleared": r["cleared"], "p_clear": r["p_clear"]}
                for r in p["recommendations"]
            ],
        })
    return out


@app.get("/")
def index():
    fp = FRONTEND_DIR / "index.html"
    if fp.exists():
        return FileResponse(fp)
    return JSONResponse({"error": "frontend/index.html not found"}, status_code=404)
