"""💎 DIAMOND WHALE: one play per sport per week, our highest-conviction bet, 5 to 10 units.

  * Price: -200 to +100 only (decimal 1.50 to 2.00). Never a -800 "safe" play, never a longshot.
  * Candidates: every market the model prices for the sport's games that week, re-run through the sims, then
    the top few go to the analyst for the deepest research we do (more searches, props and stat matchups,
    injuries and practice reports, weather, rest, splits, what the sharpest cappers say).
  * It only posts when the market edge, the simulation and the research all line up: analyst verdict "bet",
    confidence 7+ and the sim not against it. Size by confidence: 7 = 5u, 8 = 6u, 9 = 8u, 10 = 10u.
    If nothing clears that bar, there's no whale that week for that sport. A whale is never forced.
  * It's a real play on the main record (graded and posted like every pick), on its own card id.
Runs on the schedule in WHALE_SCHEDULE: once a week per sport, after the week's injury news has landed.
"""
from __future__ import annotations
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import config as C, social, store, analyst, sim, tags
from .model import candidates, parse_time, fmt_american

TZ = ZoneInfo(C.DISPLAY_TZ)
LO, HI = 1.50, 2.00          # -200 .. +100
SIZE = {7: 5.0, 8: 6.0, 9: 8.0, 10: 10.0}
NAMES = {"americanfootball_nfl": "NFL", "mma_mixed_martial_arts": "UFC", "americanfootball_ncaaf": "College Football",
         "basketball_nba": "NBA", "icehockey_nhl": "NHL", "boxing_boxing": "Boxing"}
WHALE_BRIEF = """
THIS IS FOR THE DIAMOND WHALE: our single biggest play of the week in this sport (5-10 units), so the research
has to be the deepest we do. Go further than usual on every candidate: final injury and practice reports and
expected snap counts / minutes, the key player-vs-player and unit-vs-unit matchups with current-season stats,
how the relevant player or fighter props are priced (they reveal what the market expects), weather and venue,
rest and travel, line movement and splits, and what the sharpest cappers are saying.
Give confidence 9 or 10 ONLY when every angle lines up and nothing important cuts against the bet; 7-8 when the
case is strong with one manageable risk; 6 or lower otherwise. Be strict: a whale that loses costs 5-10 units."""


def week_key(d) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def has_whale(sport, d) -> bool:
    wk = week_key(d)
    return any(r.get("tier") == "whale" and r["sport"] == sport and week_key(parse_time(r["commence_time"])
               .astimezone(TZ).date()) == wk for r in store.read(store.LEDGER))


def run(api, now, state, post, sport, hours, books, markets, to_row, hist, thread):
    """Find, research and post this week's whale for one sport. Returns the ledger row or None."""
    d = now.astimezone(TZ).date()
    if has_whale(sport, d):
        print(f"[whale] {sport}: already have this week's whale")
        return None
    start, end = now + timedelta(minutes=30), now + timedelta(hours=hours)
    events = api.odds(sport, markets, books, start, end)
    cands = [p for p in candidates(sport, events, now, hist, window=(start, end)) if LO <= p.price <= HI]
    cands = sim.apply(sport, cands, now)
    cands = [p for p in cands if p.ev >= 0 and (getattr(p, "sim", None) or {}).get("agrees") is not False]
    if not cands:
        print(f"[whale] {sport}: nothing priced -200 to +100 with an edge")
        return None
    short = sorted(cands, key=lambda p: p.score, reverse=True)
    seen, top = set(), []
    for p in short:                                   # one candidate per game, best five games
        if p.event_id not in seen:
            seen.add(p.event_id)
            top.append(p)
        if len(top) == 5:
            break
    reviewed = analyst.review(sport, top, n=5, extra=WHALE_BRIEF, searches=30)
    ok = [p for p in reviewed if (getattr(p, "analysis", None) or {}).get("verdict") == "bet"
          and p.analysis.get("confidence", 0) >= 7]
    if not ok:
        print(f"[whale] {sport}: nothing reached confidence 7 this week, no whale")
        return None
    best = max(ok, key=lambda p: (p.analysis["confidence"], p.score))
    conf = best.analysis["confidence"]
    best.units, best.tier = SIZE[min(conf, 10)], "whale"
    game_day = parse_time(best.commence_time).astimezone(TZ).date().isoformat()
    rows = store.read(store.LEDGER)
    row = to_row(best, game_day, now, "whale", pick_no=str(len(rows) + 1), card=f"{sport}|{game_day}|whale")
    store.append(store.LEDGER, row)
    print(f"[whale] {sport} #{row['pick_no']} {row['label']} {row['price_american']} {conf}/10 {row['units']}u")
    posts = thread_posts(row, best, conf)
    social.save_post(f"whale_{sport}", d.isoformat(), posts)
    if post:
        ids = social.post_thread(posts)
        if ids:
            thread(state, row["card"]).update(main=ids[0], last=ids[0])
    return row


def thread_posts(row, p, conf) -> list[str]:
    name = NAMES.get(row["sport"], C.SPORTS.get(row["sport"], (row["sport"],))[0])
    when = parse_time(row["commence_time"]).astimezone(TZ).strftime("%a %-I:%M %p ET")
    u = float(row["units"])
    head = "\n".join([
        f"💎🐋 DIAMOND WHALE · {name}",
        "",
        f"{row['label']} ({row['price_american']})",
        f"⏰ {when}",
        f"💰 {u:g}u (${u * C.UNIT_DOLLARS:,.0f} at ${C.UNIT_DOLLARS:,.0f}/unit) · confidence {conf}/10",
        "",
        f"Our biggest {name} play of the week. Every angle checked. Full breakdown 🧵👇",
        "",
        tags.for_rows([row])])
    numbers = "\n".join(["📊 The numbers:"] + social.market_reasons(row, len(p.books_with_edge)) + sim.line(p))
    research = social._matchup(analyst.notes(p))
    return [head, numbers] + ([research] if research else [])


def result_post(r) -> str:
    u, prof = float(r["units"]), float(r["profit"])
    head = {"win": f"💎🐋 DIAMOND WHALE CASHED ✅", "loss": "💎 Diamond Whale missed ❌",
            "push": "💎 Diamond Whale pushed ➖"}[r["result"]]
    tail = {"win": "Biggest play of the week, stamped before kickoff. 💰",
            "loss": "Big swing, big miss. It's on the record like every other play.",
            "push": "Money back."}[r["result"]]
    return f"{head}\n\n{r['label']} ({r['price_american']}) · {u:g}u\n{prof:+.2f}u ({'+' if prof >= 0 else '-'}${abs(prof) * C.UNIT_DOLLARS:,.0f})\n\n{tail}"
