"""Thin client for The Odds API v4 (https://the-odds-api.com). Tracks credit usage."""
from datetime import datetime, timezone
import requests

BASE = "https://api.the-odds-api.com/v4"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OddsAPI:
    def __init__(self, api_key: str):
        if not api_key:
            raise SystemExit("ODDS_API_KEY is not set. Get a free key at https://the-odds-api.com")
        self.key = api_key
        self.remaining = None
        self.spent = 0

    def _get(self, path, **params):
        params["apiKey"] = self.key
        r = requests.get(BASE + path, params=params, timeout=30)
        if r.status_code == 401:
            raise SystemExit("Odds API rejected the key (401).")
        if r.status_code == 429:
            raise SystemExit("Odds API quota or rate limit hit (429).")
        r.raise_for_status()
        self.remaining = r.headers.get("x-requests-remaining", self.remaining)
        self.spent += int(r.headers.get("x-requests-last", 0) or 0)
        return r.json()

    # Free calls ------------------------------------------------------------
    def sports(self):
        return self._get("/sports")

    def events(self, sport, start, end):
        return self._get(f"/sports/{sport}/events",
                         commenceTimeFrom=iso(start), commenceTimeTo=iso(end))

    # Paid calls (cost = markets x bookmaker-groups-of-10) ------------------
    def odds(self, sport, markets, bookmakers, start, end):
        return self._get(f"/sports/{sport}/odds", markets=",".join(markets),
                         bookmakers=",".join(bookmakers), oddsFormat="decimal",
                         commenceTimeFrom=iso(start), commenceTimeTo=iso(end))

    def event_odds(self, sport, event_id, markets, bookmakers):
        return self._get(f"/sports/{sport}/events/{event_id}/odds", markets=",".join(markets),
                         bookmakers=",".join(bookmakers), oddsFormat="decimal")

    def scores(self, sport, days_from=2):
        return self._get(f"/sports/{sport}/scores", daysFrom=days_from)
