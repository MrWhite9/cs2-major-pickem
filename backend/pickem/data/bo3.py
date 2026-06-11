"""Minimal bo3.gg client: teams, matches, and match detail (with veto + maps).

bo3.gg is a public, unauthenticated JSON API. CS lives under discipline_id 1.
The two things we need that PandaScore's free tier lacks are the full map veto
order and the map names — both present on a match's `match_maps` array.

Teams carry a `ps_id` field that equals our PandaScore team id, so the team
join is exact for established orgs; newer teams (ps_id NULL) join by name.
"""
from __future__ import annotations

import time
from typing import Any, Iterator

import requests

from ..config import BO3_BASE_URL

CS_DISCIPLINE = 1
PAGE_LIMIT = 100  # bo3 caps page[limit] at 100
# choice_type in match_maps -> our action label.
CHOICE_TYPE = {1: "pick", 2: "ban", 3: "decider"}

# bo3 names that drop a suffix our PandaScore names carry (ps_id NULL on bo3).
# Keyed by PandaScore team id so the join stays exact and explicit.
NAME_ALIASES: dict[int, str] = {
    133458: "BetBoom",          # BetBoom Team
    136955: "FUT",              # FUT Esports
    133900: "The Huns",         # The Huns Esports
}


class Bo3:
    def __init__(self, base_url: str = BO3_BASE_URL, pause: float = 0.15):
        self.base_url = base_url.rstrip("/")
        self.pause = pause
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (pickem-research)",
        })

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(5):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code in (429, 502, 503):
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            if self.pause:
                time.sleep(self.pause)
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    # --- teams ------------------------------------------------------------

    def iter_cs_teams(self) -> Iterator[dict[str, Any]]:
        """Yield every CS team object (paged).

        Drives pagination off the reported total count rather than stopping on
        the first empty page, so a transient blank response can't truncate the
        sweep (which would silently drop teams on later pages).
        """
        offset, total = 0, None
        while total is None or offset < total:
            d = self._get("teams", {
                "filter[teams.discipline_id][eq]": CS_DISCIPLINE,
                "page[limit]": PAGE_LIMIT, "page[offset]": offset,
            })
            if total is None:
                total = d.get("total", {}).get("count", 0)
            yield from d.get("results", [])
            offset += PAGE_LIMIT

    def team_by_name(self, name: str) -> dict[str, Any] | None:
        d = self._get("teams", {
            "filter[teams.name][eq]": name,
            "filter[teams.discipline_id][eq]": CS_DISCIPLINE,
            "page[limit]": 5,
        })
        results = d.get("results", [])
        return results[0] if results else None

    # --- matches ----------------------------------------------------------

    def find_match(self, bo3_a: int, bo3_b: int, date: str) -> dict[str, Any] | None:
        """A match between two bo3 team ids whose start_date is on `date` (YYYY-MM-DD)."""
        for t1, t2 in ((bo3_a, bo3_b), (bo3_b, bo3_a)):
            d = self._get("matches", {
                "filter[matches.team1_id][eq]": t1,
                "filter[matches.team2_id][eq]": t2,
                "sort": "-start_date", "page[limit]": 20,
            })
            for m in d.get("results", []):
                if (m.get("start_date") or "").startswith(date):
                    return m
        return None

    def match_detail(self, slug: str) -> dict[str, Any]:
        """Full match incl. `match_maps` (veto order + map names + maps_score)."""
        return self._get(f"matches/{slug}")

    def iter_team_matches(self, bo3_team_id: int, since: str, until: str
                          ) -> Iterator[dict[str, Any]]:
        """Yield a team's finished matches with start_date in [since, until].

        bo3 ignores server-side date filters, so we page newest-first and stop
        once we pass `since`. Covers both team1 and team2 slots and de-dups.
        """
        seen: set[int] = set()
        for side in ("team1_id", "team2_id"):
            offset = 0
            while True:
                d = self._get("matches", {
                    f"filter[matches.{side}][eq]": bo3_team_id,
                    "filter[matches.status][eq]": "finished",
                    "sort": "-start_date", "page[limit]": PAGE_LIMIT,
                    "page[offset]": offset,
                })
                results = d.get("results", [])
                if not results:
                    break
                stop = False
                for m in results:
                    date = (m.get("start_date") or "")[:10]
                    if not date or date > until:
                        continue
                    if date < since:
                        stop = True
                        break
                    if m["id"] not in seen:
                        seen.add(m["id"])
                        yield m
                if stop or len(results) < PAGE_LIMIT:
                    break
                offset += PAGE_LIMIT


def parse_vetos(detail: dict[str, Any],
                bo3_to_ps: dict[int, int] | None = None) -> list[dict[str, Any]]:
    """Flatten a match detail's `match_maps` into ordered veto steps.

    Actor ps id is resolved from the step's bo3 `team_id` via `bo3_to_ps`
    (so teams without a bo3 ps_id still attribute correctly), falling back to
    the nested `teams.ps_id`. Decider steps have no actor.
    """
    bo3_to_ps = bo3_to_ps or {}
    steps = []
    for x in detail.get("match_maps") or []:
        action = CHOICE_TYPE.get(x.get("choice_type"))
        maps = x.get("maps") or {}
        map_name = maps.get("map_name") or maps.get("slug")
        if not action or not map_name:
            continue
        actor = bo3_to_ps.get(x.get("team_id")) or (x.get("teams") or {}).get("ps_id")
        steps.append({
            "order_idx": x.get("order"),
            "action": action,
            "actor_ps_id": actor,
            "map_name": map_name,
            "played": 1 if action in ("pick", "decider") else 0,
        })
    steps.sort(key=lambda s: (s["order_idx"] is None, s["order_idx"]))
    return steps
