"""Live scores from ESPN's public scoreboards (free, no key).

The feed is undocumented, so everything here is parsed defensively: a missing field or a failed request
just means no live update this time, and the next-morning grading (The Odds API scores) still settles
the bet."""
import re
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from . import config as C
from .model import parse_time

BASE = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
TZ = ZoneInfo(C.DISPLAY_TZ)


class Feed:
    """Fetches each (sport, date) scoreboard once per run."""

    def __init__(self):
        self.cache = {}

    def raw(self, sport: str, day: date) -> dict:
        key = (sport, day)
        if key not in self.cache:
            params = {"dates": day.strftime("%Y%m%d"), "limit": 500}
            if sport == "basketball_ncaab":
                params["groups"] = "50"
            if sport == "americanfootball_ncaaf":
                params["groups"] = "80"
            try:
                r = requests.get(BASE.format(path=C.ESPN[sport][0]), params=params, timeout=20)
                r.raise_for_status()
                self.cache[key] = r.json()
            except Exception as e:
                print(f"[espn] {sport} {day}: {e}")
                self.cache[key] = {}
        return self.cache[key]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _status(comp: dict, ev: dict) -> dict:
    st = comp.get("status") or ev.get("status") or {}
    t = st.get("type") or {}
    return {"state": t.get("state", ""), "completed": bool(t.get("completed")),
            "period": int(st.get("period") or 0),
            "detail": t.get("shortDetail") or t.get("detail") or t.get("description") or "",
            "result": ((st.get("result") or {}).get("displayName") or (st.get("result") or {}).get("name") or "")}


def games(raw: dict) -> list[dict]:
    out = []
    for ev in raw.get("events", []) or []:
        comp = (ev.get("competitions") or [{}])[0]
        sides = {}
        for c in comp.get("competitors", []) or []:
            team = c.get("team") or {}
            names = {norm(team.get(k, "")) for k in ("displayName", "shortDisplayName", "location", "abbreviation")
                     if team.get(k)}
            try:
                lines = [float(x.get("value") or 0) for x in c.get("linescores") or []]
            except (TypeError, ValueError):
                lines = []
            try:
                score = float(c.get("score") or 0)
            except (TypeError, ValueError):
                score = float(((c.get("score") or {}).get("value")) or 0) if isinstance(c.get("score"), dict) else 0.0
            sides[c.get("homeAway")] = {"names": names, "nick": norm(team.get("name", "")), "score": score,
                                        "lines": lines, "display": team.get("displayName", "")}
        if "home" in sides and "away" in sides:
            out.append({"id": ev.get("id"), "start": ev.get("date") or comp.get("date"),
                        "home": sides["home"], "away": sides["away"], **_status(comp, ev)})
    return out


def fights(raw: dict) -> list[dict]:
    out = []
    for ev in raw.get("events", []) or []:
        for comp in ev.get("competitions", []) or []:
            fs = []
            for c in comp.get("competitors", []) or []:
                a = c.get("athlete") or {}
                name = a.get("displayName") or a.get("fullName") or a.get("shortName") or ""
                fs.append({"name": name, "key": fighter_key(name), "winner": bool(c.get("winner"))})
            if len(fs) == 2:
                out.append({"id": comp.get("id"), "card": ev.get("name", ""), "start": comp.get("date") or ev.get("date"),
                            "fighters": fs, **_status(comp, ev)})
    return out


def team_match(odds_name: str, side: dict) -> bool:
    n = norm(odds_name)
    return bool(n) and (n in side["names"] or (side["nick"] and n.endswith(" " + side["nick"]))
                        or (side["nick"] and n == side["nick"]))


def fighter_key(name: str) -> tuple:
    parts = norm(name).split()
    return (parts[0][:1], parts[-1]) if parts else ("", "")


def _close_in_time(a, b, hours=12) -> bool:
    try:
        return abs((parse_time(a) - parse_time(b)).total_seconds()) < hours * 3600
    except Exception:
        return True


def find_game(row: dict, gl: list[dict]) -> tuple[dict, bool] | tuple[None, None]:
    """Returns (game, swapped). swapped=True when ESPN lists the teams the other way round (neutral sites)."""
    for g in gl:
        if not _close_in_time(row["commence_time"], g["start"] or row["commence_time"]):
            continue
        if team_match(row["home"], g["home"]) and team_match(row["away"], g["away"]):
            return g, False
        if team_match(row["home"], g["away"]) and team_match(row["away"], g["home"]):
            return g, True
    return None, None


def find_fight(row: dict, fl: list[dict]) -> dict | None:
    want = {fighter_key(row["home"]), fighter_key(row["away"])}
    last = {k[1] for k in want}
    for f in fl:
        keys = {x["key"] for x in f["fighters"]}
        if keys == want:
            return f
    for f in fl:                               # fallback: last names only
        if {x["key"][1] for x in f["fighters"]} == last:
            return f
    return None


def lookup(feed: Feed, row: dict):
    """The live game/fight for a ledger row, checking the game's Eastern date and the day before
    (a fight after midnight is still on the previous night's card)."""
    if row["sport"] not in C.ESPN:            # e.g. boxing: graded next morning or with /settle
        return None, None
    d = parse_time(row["commence_time"]).astimezone(TZ).date()
    for day in (d, d - timedelta(days=1)):
        raw = feed.raw(row["sport"], day)
        if row["sport"] == "mma_mixed_martial_arts":
            f = find_fight(row, fights(raw))
            if f:
                return f, False
        else:
            g, swapped = find_game(row, games(raw))
            if g:
                return g, swapped
    return None, None
