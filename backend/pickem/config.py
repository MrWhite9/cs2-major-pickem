"""Configuration and paths, loaded from the environment (.env)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
DB_PATH = DATA_DIR / "pickem.db"

load_dotenv(BACKEND_DIR / ".env")

PANDASCORE_TOKEN = os.getenv("PANDASCORE_TOKEN", "")
PANDASCORE_BASE_URL = "https://api.pandascore.co"

# bo3.gg public REST API (no auth) — used for map veto + named map data.
BO3_BASE_URL = "https://api.bo3.gg/api/v1"


def require_token() -> str:
    if not PANDASCORE_TOKEN:
        raise RuntimeError(
            "PANDASCORE_TOKEN is not set. Copy backend/.env.example to "
            "backend/.env and add your PandaScore key."
        )
    return PANDASCORE_TOKEN
