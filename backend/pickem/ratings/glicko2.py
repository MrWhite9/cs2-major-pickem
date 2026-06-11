"""Glicko-2 rating system (Glickman 2013), with optional per-result weights.

Standard Glicko-2 treats every game as equally informative. We add a `weight`
per result so a win over a tier-S team can move a rating more than a win over a
tier-D team (see `pickem.ratings.build`). Weights enter the variance/score sums
linearly, which is equivalent to treating a match as a fractional/multiple game.

Ratings are stored on the familiar Glicko scale (rating ~1500, RD), and the
algorithm converts to/from the internal Glicko-2 scale (mu, phi) internally.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SCALE = 173.7178          # Glicko <-> Glicko-2 conversion constant
DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0
DEFAULT_VOL = 0.06
TAU = 0.5                 # system constant: constrains volatility change
EPSILON = 1e-6            # convergence tolerance for the volatility solver


@dataclass
class Rating:
    rating: float = DEFAULT_RATING
    rd: float = DEFAULT_RD
    vol: float = DEFAULT_VOL

    @property
    def mu(self) -> float:
        return (self.rating - DEFAULT_RATING) / SCALE

    @property
    def phi(self) -> float:
        return self.rd / SCALE


@dataclass
class Result:
    """A single game outcome against `opp`. score: 1 win, 0 loss, 0.5 draw."""
    opp: Rating
    score: float
    weight: float = 1.0


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _expected(mu: float, opp: Rating) -> float:
    return 1.0 / (1.0 + math.exp(-_g(opp.phi) * (mu - opp.mu)))


def win_probability(a: Rating, b: Rating) -> float:
    """P(a beats b) folding in both teams' rating deviations.

    Uses the combined RD so that uncertainty pulls the probability toward 0.5.
    """
    phi = math.sqrt(a.phi ** 2 + b.phi ** 2)
    return 1.0 / (1.0 + math.exp(-_g(phi) * (a.mu - b.mu)))


def _new_volatility(phi: float, vol: float, v: float, delta: float,
                    tau: float = TAU) -> float:
    """Illinois-algorithm root find for the updated volatility (Glickman p.3)."""
    a = math.log(vol * vol)
    delta2 = delta * delta
    phi2 = phi * phi

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta2 - phi2 - v - ex)
        den = 2.0 * (phi2 + v + ex) ** 2
        return num / den - (x - a) / (tau * tau)

    A = a
    if delta2 > phi2 + v:
        B = math.log(delta2 - phi2 - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    fA, fB = f(A), f(B)
    while abs(B - A) > EPSILON:
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA /= 2.0
        B, fB = C, fC
    return math.exp(A / 2.0)


def rate(r: Rating, results: list[Result], tau: float = TAU) -> Rating:
    """Return the updated rating after one rating period of `results`."""
    if not results:
        # No games: rating unchanged, RD inflates with volatility (capped).
        phi_star = math.sqrt(r.phi ** 2 + r.vol ** 2)
        return Rating(r.rating, min(phi_star * SCALE, DEFAULT_RD), r.vol)

    mu = r.mu
    v_inv = 0.0
    score_sum = 0.0
    for res in results:
        g = _g(res.opp.phi)
        e = _expected(mu, res.opp)
        v_inv += res.weight * g * g * e * (1.0 - e)
        score_sum += res.weight * g * (res.score - e)

    v = 1.0 / v_inv
    delta = v * score_sum
    sigma = _new_volatility(r.phi, r.vol, v, delta, tau)

    phi_star = math.sqrt(r.phi ** 2 + sigma ** 2)
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star ** 2) + 1.0 / v)
    mu_new = mu + phi_new ** 2 * score_sum

    return Rating(mu_new * SCALE + DEFAULT_RATING, phi_new * SCALE, sigma)
