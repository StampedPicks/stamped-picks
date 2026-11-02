"""Separate records: every play counts toward each record it belongs to, so we can say "SNF: 4-1" or
"Diamond Whale: 3-0" without mixing products together.

Records (a play can be in several):
  Overall (free)       every public pick on the ledger (cards, daily plays, sides, leans)
  By sport             NFL, NBA, NHL, CFB, CBB, MLB, UFC, Boxing ...
  Primetime            NFL games in prime time: TNF, SNF, MNF (and Saturday/holiday night games)
  TNF / SNF / MNF      each prime-time window on its own
  NFL Sunday early/late  1 PM and 4 PM windows
  Diamond Whale        tier "whale"
  Premium              tier "premium" (released after kickoff) + manual premium bets in data/premium.csv
  Props                data/props.csv
  Parlays              each parlay kind in data/parlays.csv (one-leg-per-game, TD, LOTTO, ...)
  Stamped Ladder       weekly ladder runs (completed / busted)
Grading stays where it is (ledger rows carry result/profit); this module only groups and formats.
"""
from __future__ import annotations
import csv
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config as C, store
from .model import parse_time

TZ = ZoneInfo(C.DISPLAY_TZ)
SPORT_NAME = {"americanfootball_nfl": "NFL", "basketball_nba": "NBA", "icehockey_nhl": "NHL",
              "americanfootball_ncaaf": "College Football", "basketball_ncaab": "College Basketball",
              "baseball_mlb": "MLB", "mma_mixed_martial_arts": "UFC", "boxing_boxing": "Boxing",
              "basketball_wnba": "WNBA"}
PREMIUM_MANUAL = C.DATA / "premium.csv"     # date,label,sport,units,price_american,result,profit,note


def window(row) -> str | None:
    """NFL time slot for a play: TNF, SNF, MNF, SAT (night), early, late. None for other sports."""
    if row.get("sport") != "americanfootball_nfl" or not row.get("commence_time"):
        return None
    t = parse_time(row["commence_time"]).astimezone(TZ)
    night = t.hour >= 19
    wd = t.weekday()                     # Mon=0
    if wd == 0:
        return "MNF"
    if wd == 3:
        return "TNF"
    if wd == 6:
        return "SNF" if night else ("early" if t.hour < 15 else "late")
    return "Primetime" if night else "other"


def groups(row) -> list[str]:
    """Every record this play counts toward (sport, NFL window, product)."""
    g = [SPORT_NAME.get(row.get("sport"), row.get("sport") or "Other")]
    w = window(row)
    if w in ("TNF", "SNF", "MNF", "Primetime"):
        g.append("Primetime")
        if w != "Primetime":
            g.append(w)
    elif w in ("early", "late"):
        g.append(f"NFL Sunday {'1 PM' if w == 'early' else '4 PM'}")
    if row.get("tier") == "whale":
        g.append("Diamond Whale")
    return g


def scope(row) -> str:
    """'premium' for premium plays (released vault picks and the manual premium log), else 'free'."""
    return "premium" if row.get("tier") == "premium" else "free"


def _read(path) -> list[dict]:
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def all_records(rows=None) -> dict[str, dict]:
    """{record name: {"free": stats, "premium": stats, "all": stats}}. Whales count as free plays (they're
    given away by DM) and also get their own Diamond Whale record."""
    rows = rows if rows is not None else store.read(store.LEDGER)
    plays = [(r, groups(r)) for r in rows]
    for r in _read(PREMIUM_MANUAL):
        r["units"] = r.get("units") or "1"
        plays.append((r, groups(r)))
    try:
        from . import parlays
        for r in _read(C.DATA / "props.csv"):
            r = dict(r, sport=r.get("sport") or "americanfootball_nfl")
            plays.append((r, groups(r) + ["Props"]))
        for p in parlays.read():
            name = {"ladder": "One-Leg-Per-Game Parlay", "td": "TD Parlay", "lotto": "LOTTO"}.get(
                p.get("name"), p.get("name") or "Parlay")
            plays.append((p, [name, "Parlays"]))
    except Exception as e:                    # noqa: BLE001 - a missing file never breaks the records
        print(f"[records] {e}")
    buckets: dict[str, dict[str, list]] = {}
    for r, gs in plays:
        sc = scope(r)
        for g in gs + ["Overall"]:
            b = buckets.setdefault(g, {"free": [], "premium": [], "all": []})
            b[sc].append(r)
            b["all"].append(r)
    return {g: {k: store.stats(v) for k, v in b.items()} for g, b in buckets.items()}


def _rec(st: dict) -> str:
    return f"{st['wins']}-{st['losses']}" + (f"-{st['pushes']}" if st["pushes"] else "")


def _has(st: dict) -> bool:
    return st["wins"] + st["losses"] + st["pushes"] > 0


def line(name: str, st: dict, which: str = "all") -> str:
    """One record line. st is either a stats dict or the {"free","premium","all"} dict."""
    if "all" in st:
        st = st[which]
    return f"{name}: {_rec(st)}, {st['profit']:+.2f}u"


def full_line(name: str, b: dict) -> str:
    """'SNF: 4-3, +8.48u (Premium 4-1, +10.98u · Free 0-2, -2.50u)'."""
    parts = [f"{k.title()} {_rec(b[k])}, {b[k]['profit']:+.2f}u" for k in ("premium", "free") if _has(b[k])]
    return f"{line(name, b)}" + (f" ({' · '.join(parts)})" if len(parts) > 1 else "")


def record_for(row, rows=None) -> list[str]:
    """Record lines a result post for this play should show, most specific first (e.g. MNF, Primetime, NFL)."""
    recs = all_records(rows)
    order = [g for g in reversed(groups(row) + ["Overall"]) if g in recs and _has(recs[g]["all"])]
    return [full_line(g, recs[g]) for g in order]
