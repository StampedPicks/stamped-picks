"""Hashtags for the main posts: the sport, the game slot or event, and the betting community tag.

X allows hashtags on your own posts but bans automated posting to trending topics, and posts stuffed
with tags read as spam and tend to reach fewer people. So: at most MAX_TAGS, only on the main posts
(picks, cards, hype, reminders, results, recaps), never on live-update replies, and only if they fit
without cutting anything from the post itself."""
import re
from zoneinfo import ZoneInfo
from . import config as C
from .model import parse_time

MAX_TAGS = 3
COMMUNITY = "#GamblingTwitter"
SPORT = {"americanfootball_nfl": "#NFL", "americanfootball_ncaaf": "#CFB", "basketball_nba": "#NBA",
         "basketball_ncaab": "#CBB", "baseball_mlb": "#MLB", "icehockey_nhl": "#NHL",
         "mma_mixed_martial_arts": "#UFC", "boxing_boxing": "#Boxing"}
TZ = ZoneInfo(C.DISPLAY_TZ)


def slot(row: dict) -> str:
    """#TNF / #SNF / #MNF / #NFLSunday for NFL; #CFBSaturday for college Saturdays."""
    t = parse_time(row["commence_time"]).astimezone(TZ)
    if row["sport"] == "americanfootball_nfl":
        if t.weekday() == 3:
            return "#TNF"
        if t.weekday() == 0:
            return "#MNF"
        if t.weekday() == 6:
            return "#SNF" if t.hour >= 19 else "#NFLSunday"
    if row["sport"] == "americanfootball_ncaaf" and t.weekday() == 5:
        return "#CFBSaturday"
    return ""


def event_tag(name: str) -> str:
    """'UFC 320: Ankalaev vs Pereira 2' → #UFC320, 'UFC Fight Night: …' → #UFCFightNight."""
    m = re.search(r"UFC\s*(\d{3})", name or "")
    if m:
        return f"#UFC{m.group(1)}"
    if re.search(r"fight night", name or "", re.I):
        return "#UFCFightNight"
    return ""


def for_rows(rows: list[dict], event: str = "", community: bool = True) -> str:
    out = []
    for r in rows:
        for t in (SPORT.get(r["sport"], ""), slot(r)):
            if t and t not in out:
                out.append(t)
    e = event_tag(event)
    if e and e not in out:
        out.insert(1, e)
    if community:
        out.append(COMMUNITY)
    # keep the sport tag(s), then the most specific, then the community tag, within the cap
    if len(out) > MAX_TAGS:
        out = out[:MAX_TAGS - 1] + ([COMMUNITY] if community else [out[MAX_TAGS - 1]])
    return " ".join(out)


def for_sports(sports: list[str]) -> str:
    out = [SPORT[s] for s in sports if s in SPORT][:MAX_TAGS - 1]
    return " ".join(dict.fromkeys(out + [COMMUNITY]))
