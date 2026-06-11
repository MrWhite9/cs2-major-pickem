"""Map-aware series probabilities via a simulated veto.

This is the map-aware model (P3). For a stage it precomputes, for every pair of
teams, P(a beats b) for a Bo1 and a Bo3 — folding in (1) each team's per-map
strength and (2) the veto, simulated from each team's map affinities. The result
is a `SeriesProb` callable that the Swiss Monte-Carlo consumes in place of the
flat average-map win probability, so the simulator itself is unchanged.

Affinity for team T on map m blends skill and style:
    a(T,m) = z(map_rating(T,m))  +  lam * z(pick_rate - ban_rate)
z-scored across the active pool. Vetoes are sampled with a softmax (temperature
`tau`); bans target the map where the opponent has the biggest edge, picks take
the team's highest-affinity map. The played pool's per-map win probabilities
(map-specific ratings, optional picker bump) feed the distinct-prob Bo formula.
"""
from __future__ import annotations

import math
import random
import sqlite3
from dataclasses import dataclass

from ..ratings.glicko2 import Rating, win_probability
from .bo import series_win_prob_distinct
from .swiss import Participant

# Current Active Duty pool; used only if a stage has no stored veto maps.
DEFAULT_POOL = ["de_ancient", "de_anubis", "de_dust2", "de_inferno",
                "de_mirage", "de_nuke", "de_overpass"]

# Calibrated on Cologne+Budapest vetos (P4): style weight lam=1.0 maximises Bo3
# pool hit-rate (55% vs 43% random); the empirical picker residual was -0.055
# (no home-map advantage), so side_bump stays 0.
DEFAULT_LAMBDA = 1.0    # weight of style vs skill in affinity
DEFAULT_TAU = 0.9       # veto softmax temperature (lower = chalkier)
DEFAULT_SIDE_BUMP = 0.0 # added to the picker's per-map win prob


def active_pool(conn: sqlite3.Connection, tournament_id: int) -> list[str]:
    """The map pool actually used in a stage (from its own vetos)."""
    rows = conn.execute(
        "SELECT DISTINCT map_name FROM match_vetos v JOIN matches m "
        "ON m.id = v.match_id WHERE m.tournament_id = ? ORDER BY map_name",
        (tournament_id,),
    ).fetchall()
    pool = [r["map_name"] for r in rows]
    return pool if pool else list(DEFAULT_POOL)


def _zscore(values: dict[str, float]) -> dict[str, float]:
    xs = list(values.values())
    mu = sum(xs) / len(xs)
    var = sum((x - mu) ** 2 for x in xs) / len(xs)
    sd = math.sqrt(var)
    if sd == 0:
        return {k: 0.0 for k in values}
    return {k: (v - mu) / sd for k, v in values.items()}


@dataclass
class VetoModel:
    """Per-team map ratings + affinities for one stage, frozen at a cutoff."""
    pool: list[str]
    ratings: dict[int, dict[str, Rating]]      # team -> map -> Rating
    affinity: dict[int, dict[str, float]]      # team -> map -> affinity score
    lam: float
    tau: float
    side_bump: float

    def _ban(self, team: int, opp: int, remaining: list[str],
             rng: random.Random) -> str:
        # Ban where the opponent has the biggest relative edge.
        scores = {m: self.affinity[opp][m] - self.affinity[team][m] for m in remaining}
        return _softmax_choice(scores, self.tau, rng)

    def _pick(self, team: int, remaining: list[str], rng: random.Random) -> str:
        scores = {m: self.affinity[team][m] for m in remaining}
        return _softmax_choice(scores, self.tau, rng)

    def simulate_played(self, a: int, b: int, n_games: int, rng: random.Random
                        ) -> list[tuple[str, int | None]]:
        """Return the played maps as (map, picker) in play order."""
        first, second = (a, b) if rng.random() < 0.5 else (b, a)
        remaining = list(self.pool)

        def drop(m): remaining.remove(m)

        if n_games == 1:                       # Bo1: alternate bans, leftover plays
            turn = first
            while len(remaining) > 1:
                other = second if turn == first else first
                drop(self._ban(turn, other, remaining, rng))
                turn = other
            return [(remaining[0], None)]

        # Bo3 (and Bo5 deciders handled the same up to the played set):
        drop(self._ban(first, second, remaining, rng))
        drop(self._ban(second, first, remaining, rng))
        fp = self._pick(first, remaining, rng); drop(fp)
        sp = self._pick(second, remaining, rng); drop(sp)
        drop(self._ban(second, first, remaining, rng))
        drop(self._ban(first, second, remaining, rng))
        decider = remaining[0]
        # Play order: first team's pick, second team's pick, decider.
        return [(fp, first), (sp, second), (decider, None)]

    def _map_p(self, a: int, b: int, m: str, picker: int | None) -> float:
        p = win_probability(self.ratings[a][m], self.ratings[b][m])
        if self.side_bump and picker is not None:
            p += self.side_bump if picker == a else -self.side_bump
        return min(1.0, max(0.0, p))

    def series_prob(self, a: int, b: int, n_games: int, rng: random.Random,
                    n_veto: int) -> float:
        """Monte-Carlo P(a beats b) over simulated vetos."""
        total = 0.0
        for _ in range(n_veto):
            played = self.simulate_played(a, b, n_games, rng)
            probs = [self._map_p(a, b, m, picker) for m, picker in played]
            total += probs[0] if n_games == 1 else series_win_prob_distinct(probs)
        return total / n_veto

    def pool_probabilities(self, a: int, b: int, n_games: int,
                           rng: random.Random, n_veto: int) -> dict[str, float]:
        """P(each map is played) between a and b — for the dashboard veto panel."""
        counts = {m: 0 for m in self.pool}
        for _ in range(n_veto):
            for m, _picker in self.simulate_played(a, b, n_games, rng):
                counts[m] += 1
        return {m: counts[m] / n_veto for m in self.pool}


def _softmax_choice(scores: dict[str, float], tau: float,
                    rng: random.Random) -> str:
    keys = list(scores)
    mx = max(scores[k] for k in keys)
    weights = [math.exp((scores[k] - mx) / tau) for k in keys]
    total = sum(weights)
    r = rng.random() * total
    upto = 0.0
    for k, w in zip(keys, weights):
        upto += w
        if r <= upto:
            return k
    return keys[-1]


# --- assembling a model from stored data ---------------------------------

def _load_ratings(conn, team_ids, as_of_iso, pool):
    """team -> map -> Rating, falling back to the global map rating then default."""
    out: dict[int, dict[str, Rating]] = {}
    for t in team_ids:
        g = conn.execute(
            "SELECT rating, deviation, volatility FROM ratings "
            "WHERE team_id = ? AND as_of = ? AND system = 'glicko2_map'",
            (t, as_of_iso),
        ).fetchone()
        fallback = Rating(g["rating"], g["deviation"], g["volatility"]) if g else Rating()
        per_map = {}
        for m in pool:
            row = conn.execute(
                "SELECT rating, deviation, volatility FROM map_ratings "
                "WHERE team_id = ? AND as_of = ? AND map_name = ?",
                (t, as_of_iso, m),
            ).fetchone()
            per_map[m] = Rating(row["rating"], row["deviation"], row["volatility"]) \
                if row else fallback
        out[t] = per_map
    return out


def _load_style(conn, team_ids, as_of_iso, pool):
    """team -> map -> (pick_count - ban_count) over matches before the cutoff."""
    ph_t = ",".join("?" * len(team_ids))
    ph_m = ",".join("?" * len(pool))
    rows = conn.execute(
        f"""SELECT v.actor_ps_id t, v.map_name m, v.action act, COUNT(*) c
            FROM match_vetos v JOIN matches mt ON mt.id = v.match_id
            WHERE mt.begin_at < ? AND v.actor_ps_id IN ({ph_t})
              AND v.map_name IN ({ph_m}) AND v.action IN ('pick','ban')
            GROUP BY v.actor_ps_id, v.map_name, v.action""",
        (as_of_iso, *team_ids, *pool),
    ).fetchall()
    style = {t: {m: 0.0 for m in pool} for t in team_ids}
    for r in rows:
        style[r["t"]][r["m"]] += r["c"] if r["act"] == "pick" else -r["c"]
    return style


def build_veto_model(conn: sqlite3.Connection, participants: list[Participant],
                     as_of: str, tournament_id: int, lam: float = DEFAULT_LAMBDA,
                     tau: float = DEFAULT_TAU, side_bump: float = DEFAULT_SIDE_BUMP
                     ) -> VetoModel:
    as_of_iso = as_of if "T" in as_of else f"{as_of}T00:00:00Z"
    team_ids = [p.team_id for p in participants]
    pool = active_pool(conn, tournament_id)

    ratings = _load_ratings(conn, team_ids, as_of_iso, pool)
    style = _load_style(conn, team_ids, as_of_iso, pool)

    affinity: dict[int, dict[str, float]] = {}
    for t in team_ids:
        z_skill = _zscore({m: ratings[t][m].rating for m in pool})
        z_style = _zscore({m: style[t][m] for m in pool})
        affinity[t] = {m: z_skill[m] + lam * z_style[m] for m in pool}

    return VetoModel(pool, ratings, affinity, lam, tau, side_bump)


@dataclass
class SeriesProb:
    """Precomputed pairwise series matrices + a callable for the Swiss sim."""
    p_bo1: dict[int, dict[int, float]]
    p_bo3: dict[int, dict[int, float]]
    model: VetoModel

    def __call__(self, a: int, b: int, n_games: int) -> float:
        table = self.p_bo1 if n_games == 1 else self.p_bo3
        return table[a][b]


def build_series_matrices(conn: sqlite3.Connection,
                          participants: list[Participant], as_of: str,
                          tournament_id: int, n_veto: int = 1500,
                          seed: int = 7, lam: float = DEFAULT_LAMBDA,
                          tau: float = DEFAULT_TAU,
                          side_bump: float = DEFAULT_SIDE_BUMP) -> SeriesProb:
    """Precompute P(a beats b) for Bo1 and Bo3 over every pair in the stage."""
    model = build_veto_model(conn, participants, as_of, tournament_id,
                             lam=lam, tau=tau, side_bump=side_bump)
    ids = [p.team_id for p in participants]
    rng = random.Random(seed)
    p_bo1 = {a: {b: 0.5 for b in ids} for a in ids}
    p_bo3 = {a: {b: 0.5 for b in ids} for a in ids}

    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            pa1 = model.series_prob(a, b, 1, rng, n_veto)
            pa3 = model.series_prob(a, b, 3, rng, n_veto)
            p_bo1[a][b], p_bo1[b][a] = pa1, 1.0 - pa1
            p_bo3[a][b], p_bo3[b][a] = pa3, 1.0 - pa3
    return SeriesProb(p_bo1, p_bo3, model)
