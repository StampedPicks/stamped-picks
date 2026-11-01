"""Hashtags for the main posts: the sport, the game slot or event, and the betting community tag.

X allows hashtags on your own posts but bans automated posting to trending topics, and posts stuffed
with tags read as spam and tend to reach fewer people. So: at most MAX_TAGS, only on the main posts
(picks, cards, hype, reminders, results, recaps), never on live-update replies, and only if they fit
without cutting anything from the post itself."""
import re
from zoneinfo import ZoneInfo
from . import config as C
from .model import parse_time

MAX_TAGS = 5
COMMUNITY = "#GamblingTwitter"
# What people searching for picks actually type: "<league>Picks", free picks, sports betting.
PICKS = {"americanfootball_nfl": "#NFLPicks", "americanfootball_ncaaf": "#CFBPicks", "basketball_nba": "#NBAPicks",
         "basketball_ncaab": "#CBBPicks", "baseball_mlb": "#MLBPicks", "icehockey_nhl": "#NHLPicks",
         "mma_mixed_martial_arts": "#UFCPicks", "boxing_boxing": "#BoxingPicks"}
REACH = ["#FreePicks", "#SportsBetting"]
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
    picks = [PICKS[r["sport"]] for r in rows if r["sport"] in PICKS]
    if picks and picks[0] not in out:
        out.insert(1, picks[0])
    if community:
        out += [t for t in REACH if t not in out] + [COMMUNITY]
    # keep the sport tag, its picks tag and the event/slot first, then reach tags, then the community tag
    if len(out) > MAX_TAGS:
        out = out[:MAX_TAGS - 1] + ([COMMUNITY] if community else [out[MAX_TAGS - 1]])
    return " ".join(out)


def for_sports(sports: list[str]) -> str:
    out = [SPORT[s] for s in sports if s in SPORT][:2] + [PICKS[s] for s in sports if s in PICKS][:1]
    out = list(dict.fromkeys(out + REACH))[:MAX_TAGS - 1]
    return " ".join(dict.fromkeys(out + [COMMUNITY]))
