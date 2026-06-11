"""Minimal PandaScore CS2 client: paginated GET with rate-limit handling.

Only the read endpoints the predictor needs. CS2 lives under the historical
`/csgo/` prefix in PandaScore's API.
"""
from __future__ import annotations

import time
from typing import Any, Iterator

import requests

from ..config import PANDASCORE_BASE_URL, require_token

MAX_PER_PAGE = 100
# Stay a little below the plan's hourly budget; back off when the window is low.
LOW_REMAINING_THRESHOLD = 5


class PandaScore:
    def __init__(self, token: str | None = None, base_url: str = PANDASCORE_BASE_URL):
        self.token = token or require_token()
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _get(self, path: str, params: dict[str, Any]) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        params = {**params, "token": self.token}
        for attempt in range(5):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            self._respect_rate_limit(resp)
            return resp
        resp.raise_for_status()
        return resp

    @staticmethod
    def _respect_rate_limit(resp: requests.Response) -> None:
        remaining = resp.headers.get("X-Rate-Limit-Remaining")
        if remaining is not None and int(remaining) <= LOW_REMAINING_THRESHOLD:
            # Window resets hourly; pause briefly to avoid a hard 429 wall.
            time.sleep(5)

    def paginate(self, path: str, params: dict[str, Any] | None = None,
                 per_page: int = MAX_PER_PAGE, max_pages: int | None = None
                 ) -> Iterator[dict[str, Any]]:
        """Yield every item across pages for a list endpoint."""
        params = dict(params or {})
        params["per_page"] = min(per_page, MAX_PER_PAGE)
        page = 1
        while True:
            params["page"] = page
            batch = self._get(path, params).json()
            if not batch:
                return
            yield from batch
            if len(batch) < params["per_page"]:
                return
            page += 1
            if max_pages is not None and page > max_pages:
                return

    def past_matches(self, since: str | None = None, until: str | None = None,
                     **kw: Any) -> Iterator[dict[str, Any]]:
        """Finished CS2 matches, oldest first, optionally within a date range.

        Note: matches have no `tier` attribute (tier lives on the tournament),
        so tier filtering is done downstream via the stored tournament tier.
        """
        params: dict[str, Any] = {"sort": "begin_at"}
        if since and until:
            params["range[begin_at]"] = f"{since},{until}"
        elif since:
            params["range[begin_at]"] = f"{since},{_now_iso()}"
        yield from self.paginate("csgo/matches/past", params, **kw)

    def tournament_matches(self, tournament_id: int, **kw: Any) -> Iterator[dict[str, Any]]:
        params = {"filter[tournament_id]": tournament_id, "sort": "begin_at"}
        yield from self.paginate("csgo/matches", params, **kw)

    def tournaments(self, search: str | None = None, tier: str | None = None,
                    **kw: Any) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {"sort": "-begin_at"}
        if search:
            params["search[name]"] = search
        if tier:
            params["filter[tier]"] = tier
        yield from self.paginate("csgo/tournaments", params, **kw)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
