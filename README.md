# CS2 Major Pick'Em — Predictor & Tracker

Predicts and tracks CS2 Major Pick'Em picks. The objective is to **clear each
stage's threshold** (keep/upgrade the event coin), not to maximize total correct
picks — so the optimizer maximizes `P(correct picks >= threshold)`, a
variance/correlation-aware objective rather than expected value.

## Current pick — IEM Cologne 2026, Stage 3 (after Round 1)

Round 1 is complete (8 series played). Both original 3-0 picks — **TheMongolz**
and **Legacy** — lost their openers, so they can no longer go 3-0; the pick is
re-optimised **conditioned on the results so far**. Threshold-optimal Pick'Em from
the default model, ratings frozen **2026-06-11**, live results conditioned, 50k
sims. Modeled **P(clear ≥ 5/10) ≈ 91%** (up from ~77% pre-event), E[correct] ≈ 5.8.

Standings after Round 1 — **1-0:** Vitality, Spirit, Falcons, MOUZ, FURIA, 9z,
BetBoom, Aurora · **0-1:** Natus Vincere, Legacy, G2, FUT, TheMongolz, PARIVISION,
B8, Monte.

| Slot | Picks |
| --- | --- |
| **3-0** | Aurora Gaming, BetBoom Team |
| **Advance** | Vitality, Spirit, Team Falcons, MOUZ, FURIA, 9z |
| **0-3** | B8, Monte |

Conditioning lifts modeled `P(clear)` from ~77% to ~91%: five of the six advance
picks already hold a 1-0 record (Vitality 99% / Spirit 97% to advance) and both
0-3 picks (B8, Monte) are 0-1 as hoped. As before, the optimizer parks the
near-locked favourites in the **advance** slots and spends the two **3-0** slots
on lottery tickets (Aurora / BetBoom, ~3–4% each) — because the eight advance+0-3
picks already nearly clear the 5/10 threshold on their own, so it maximizes
`P(clear the threshold)`, not expected correct picks. A chalk alternative that puts
Vitality & Spirit in the 3-0 slots scores an essentially identical P(clear) (≈90%)
with higher E[correct] (≈6.1) — pick that if you prefer fewer dead picks. A
map-aware veto-simulation model is also available in the dashboard as a toggle
(counter-pick aware), but the average-map model remains the default pending a
larger backtest.

## Approach

1. **Data** — pull match history, brackets, and results from the PandaScore API
   into a local SQLite store. (Free "Fixtures Only" tier is sufficient.)
2. **Ratings** — derive Glicko-2 team ratings from match history, frozen at each
   tournament's start date (no peeking at future results — keeps backtests honest).
3. **Simulator** — Monte-Carlo the 16-team Swiss stages (Buchholz seeding,
   Bo1/Bo3 aware) to get `P(3-0)`, `P(0-3)`, `P(advance)` plus the joint outcome
   distribution.
4. **Optimizer** — search the pick space to maximize `P(correct >= threshold)`.
5. **Backtest** — replay completed Majors and compare against actual outcomes and
   a naive-chalk baseline.
6. **Dashboard** — FastAPI backend + React/Next.js frontend (built once the
   engine is validated).

## Status

- [x] M1 — Data spine (SQLite schema + PandaScore client + backfill)
- [x] M2 — Glicko-2 ratings (time-frozen, tier-weighted, map + match level)
- [x] M3 — Swiss Monte-Carlo simulator (Buchholz, Bo1/Bo3 aware)
- [x] M4 — Pick optimizer (maximize P(clear)) + backtest harness
- [x] M5 — FastAPI + web dashboard
- [ ] M6 — Go live on the next Major (+ multi-Major backtest for rigor)

## Setup

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env   # then add your PandaScore token
```

### Backfill match history

```bash
python -m pickem.data.backfill --months 12
```

### Build ratings (frozen at a Major's start)

```bash
python -m pickem.ratings.build --cutoff 2025-11-24 --level map
```

### Backtest a completed Major

```bash
python -m pickem.backtest.harness --cutoff 2025-11-24 --serie 9822
```

### Run the dashboard

```bash
uvicorn pickem.api.app:app --reload      # then open http://127.0.0.1:8000/
```
