# CS2 Major Pick'Em — Predictor & Tracker

Predicts and tracks CS2 Major Pick'Em picks. The objective is to **clear each
stage's threshold** (keep/upgrade the event coin), not to maximize total correct
picks — so the optimizer maximizes `P(correct picks >= threshold)`, a
variance/correlation-aware objective rather than expected value.

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
