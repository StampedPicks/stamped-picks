"""NFL weekly extras, posted with the NFL card:

  * Every game: one pick per game priced close to even money (-125 to +125), the best number on each game
  * One leg per game: a parlay of the best leg from 4 different games
  * TD parlay: anytime-touchdown scorers from 3-5 different games, each at the best price in the market
  * Flyer: a small-stake longshot, plus-money legs from 5-6 different games

Every-game picks go on the main ledger (tier "slate", half a unit) so they count in the record like any other
pick. Parlays live in data/parlays.csv and are graded here: game legs from ESPN final scores, TD legs from
ESPN's scoring plays. A parlay settles as soon as one leg loses, or when every leg is final."""
from __future__ import annotations
import csv
import json
import re
import statistics
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from . import config as C, espn, social, store, tags, telegram
from .grading import grade
from .model import parse_time, fmt_american

NFL = "americanfootball_nfl"
TZ = ZoneInfo(C.DISPLAY_TZ)
FILE = C.DATA / "parlays.csv"
COLS = ["id", "date", "name", "posted_at", "legs", "price", "units", "result", "profit", "final"]
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
NUM = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
TD_VIG = 1.12            # anytime-TD markets carry roughly 10-15% hold; strip it to estimate a fair chance

KINDS = {   # name, emoji, units, legs
    "ladder": ("ONE LEG PER GAME", "🎟️", 0.5, (3, 4)),
    "td": ("TD PARLAY", "🎯", 0.25, (3, 5)),
    "flyer": ("FLYER", "🚀", 0.25, (4, 6)),
}


# ---------------------------------------------------------------- storage
def read() -> list[dict]:
    if not FILE.exists():
        return []
    with FILE.open() as fh:
        return list(csv.DictReader(fh))


def write(rows: list[dict]):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    with FILE.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLS})


# ---------------------------------------------------------------- picking
def game_leg(p) -> dict:
    return {"kind": "game", "sport": p.sport, "event_id": p.event_id, "commence_time": p.commence_time,
            "home": p.home, "away": p.away, "market": p.market, "side": p.side,
            "point": "" if p.point is None else f"{p.point:g}", "label": p.label, "price": round(p.price, 4),
            "book": p.book}


def best_per_game(cands, lo=1.0, hi=99.0) -> list:
    """The highest-scoring play in each game within a price range, best games first."""
    best = {}
    for p in cands:
        if lo <= p.price <= hi and (p.event_id not in best or p.score > best[p.event_id].score):
            best[p.event_id] = p
    return sorted(best.values(), key=lambda p: p.score, reverse=True)


def td_legs(api, events, books) -> list[dict]:
    """Anytime-TD scorers: per game, the player whose best price beats the rest of the market by the most,
    among likely scorers (-200 to +250). One Odds API call per game."""
    out = []
    for ev in events:
        try:
            data = api.event_odds(NFL, ev["id"], ["player_anytime_td"], books)
        except SystemExit:
            raise
        except Exception as e:                    # noqa: BLE001
            print(f"[parlays] TD odds failed for {ev.get('away_team')} @ {ev.get('home_team')}: {e}")
            continue
        prices = {}
        for bk in data.get("bookmakers", []):
            for m in bk.get("markets", []):
                for o in m.get("outcomes", []):
                    if o.get("name") == "No":
                        continue
                    player = o.get("description") or o.get("name")
                    if player:
                        prices.setdefault(player, []).append((o["price"], bk["key"]))
        best = None
        for player, ps in prices.items():
            if len(ps) < 2:
                continue
            top, book = max(ps)
            if not (1.5 <= top <= 3.5):
                continue
            fair_p = statistics.median(1 / x for x, _ in ps) / TD_VIG
            value = fair_p * top - 1
            if best is None or value > best["ev"]:
                best = {"kind": "td", "event_id": ev["id"], "commence_time": ev["commence_time"],
                        "home": ev["home_team"], "away": ev["away_team"], "player": player,
                        "label": f"{player} anytime TD", "price": round(top, 4), "book": book, "ev": value}
        if best:
            out.append(best)
    return sorted(out, key=lambda x: x["ev"], reverse=True)


def build(cands, tds, exclude=frozenset()) -> dict:
    """{kind: [legs]} for the three parlays. Every leg is from a different game; games in `exclude` (the
    premium pick's game) are left out so a public parlay never gives the premium side away."""
    cands = [p for p in cands if p.event_id not in exclude]
    tds = [t for t in tds if t["event_id"] not in exclude]
    out = {}
    ladder = [game_leg(p) for p in best_per_game(cands, 1.6, 2.3)[:4]]
    if len(ladder) >= 3:
        out["ladder"] = ladder
    if len(tds) >= 3:
        out["td"] = tds[:4] if len(tds) >= 4 else tds[:3]
    flyer = []
    dogs = best_per_game(cands, 2.2, 4.0)               # real underdogs (+120 or longer) first
    if len(dogs) < 4:
        dogs += [p for p in best_per_game(cands, 1.9, 2.2) if p.event_id not in {d.event_id for d in dogs}]
    for p in dogs:                                      # stop once it pays about +2500, 4-6 legs
        flyer.append(game_leg(p))
        if len(flyer) >= 6 or (len(flyer) >= 4 and price_of(flyer) >= 26):
            break
    if len(flyer) >= 4:
        out["flyer"] = flyer
    return out


def price_of(legs) -> float:
    d = 1.0
    for l in legs:
        d *= float(l["price"])
    return d


# ---------------------------------------------------------------- posting
def _book(k):
    return C.BET_BOOKS.get(k, k)


def _short(iso):
    return parse_time(iso).astimezone(TZ).strftime("%-I:%M")


def parlay_post(kind, legs, now) -> str:
    name, emoji, units, _ = KINDS[kind]
    dec = price_of(legs)
    lines = [f"{emoji} {name} ({len(legs)} legs)", ""]
    for i, l in enumerate(legs):
        book = f", {_book(l['book'])}" if C.X_PREMIUM else ""          # 280-character accounts drop the book
        lines.append(f"{NUM[i]} {l['label']} ({fmt_american(float(l['price']))}{book})")
    win = units * (dec - 1)
    lines += ["", f"💰 {fmt_american(dec)} · {units:g}u: ${units * C.UNIT_DOLLARS:,.0f} to win "
                  f"${win * C.UNIT_DOLLARS:,.0f}",
              f"🕒 Stamped {now.astimezone(TZ):%a %-I:%M %p} ET"]
    if kind == "flyer":
        lines.append("Small bet, big number. Don't chase it.")
    if kind == "td":
        lines.append("Each scorer is the best price in his game's TD market.")
    return social.tagged("\n".join(lines) if C.X_PREMIUM else social.fit(lines), "#NFL #NFLSunday #GamblingTwitter")


def slate_post(rows, card_rows, now) -> str:
    lines = ["🏈 Every game on today's slate, one pick each (all close to even money):", ""]
    for r in sorted(rows + card_rows, key=lambda r: (r["commence_time"], r["label"])):
        star = "⭐ " if r in card_rows else "• "
        lines.append(f"{star}{r['label']} ({r['price_american']}) · {_short(r['commence_time'])}")
    lines += ["", f"• = {rows[0]['units'] if rows else '0.5'}u each · ⭐ = on our card (bigger bet)",
              f"🕒 Stamped {now.astimezone(TZ):%a %-I:%M %p} ET. All graded on the record."]
    return social.tagged("\n".join(lines) if C.X_PREMIUM else social.fit(lines, keep_last=2),
                         "#NFL #NFLSunday #GamblingTwitter")


def run(api, now, state, post, sport, day, cands, events, card_ids: set, to_row, card_id: str):
    """Called by run_card for NFL after the card is picked. Records and posts the slate and the parlays."""
    if sport != NFL:
        return
    done = state.setdefault("parlays_posted", [])
    key = day.isoformat()
    if key in done:
        return
    done.append(key)
    rows = store.read(store.LEDGER)
    card_rows = [r for r in rows if r.get("card") == card_id]
    slate_id = f"{card_id}|slate"
    prem = premium(now, state, post, key, cands, card_ids, to_row, card_id)
    slate = []
    for p in best_per_game(cands, 1.8, 2.25):
        if p.event_id in card_ids or (prem and p.event_id == prem["event_id"]):
            continue
        p.units, p.tier = 0.5, "slate"
        row = to_row(p, key, now, "slate", pick_no=str(len(rows) + len(slate) + 1), card=slate_id)
        slate.append(row)
    for row in slate:
        store.append(store.LEDGER, row)
    if slate or card_rows:
        text = slate_post(slate, card_rows, now)
        social.save_post("slate_nfl", key, text)
        if post:
            pid = social.post_x(text)
            if pid:
                state.setdefault("threads", {}).setdefault(slate_id, {}).update(main=pid, last=pid)
    tds = []
    try:
        tds = td_legs(api, events, list(C.BET_BOOKS))
    except SystemExit as e:
        print(f"[parlays] TD props unavailable: {e}")
    built = build(cands, tds, {prem["event_id"]} if prem else frozenset())
    prs = read()
    for kind, legs in built.items():
        name, emoji, units, _ = KINDS[kind]
        text = parlay_post(kind, legs, now)
        social.save_post(f"parlay_{kind}", key, text)
        pid = social.post_x(text) if post else None
        prs.append({"id": f"{key}-{kind}", "date": key, "name": kind, "posted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "legs": json.dumps(legs), "price": f"{price_of(legs):.4f}", "units": f"{units:g}",
                    "result": "", "profit": "", "final": ""})
        if pid:
            state.setdefault("threads", {})[f"parlay:{key}-{kind}"] = {"main": pid, "last": pid}
        print(f"[parlays] {kind}: {len(legs)} legs at {fmt_american(price_of(legs))}")
    write(prs)


# ---------------------------------------------------------------- premium pick
def _nick(team: str) -> str:
    return team.split()[-1] if team else team


def premium(now, state, post, key, cands, card_ids, to_row, card_id) -> dict | None:
    """The strongest play off the public card goes to members only. The public gets a teaser with the game
    and kickoff time, never the side. The result is posted publicly after the game either way."""
    pool = [p for p in sorted(cands, key=lambda p: p.score, reverse=True)
            if p.event_id not in card_ids and 1.6 <= p.price <= 2.6 and p.ev >= C.LEAN_FLOOR]
    if not pool:
        return None
    p = pool[0]
    p.units, p.tier = (1.5 if p.ev >= C.MIN_EV else 1.0), "premium"
    rows = store.read(store.LEDGER)
    row = to_row(p, key, now, "premium", pick_no=str(len(rows) + 1), card=f"{card_id}|premium")
    store.append(store.LEDGER, row)
    when = parse_time(row["commence_time"]).astimezone(TZ).strftime("%-I:%M %p ET")
    game = f"{_nick(row['away'])}-{_nick(row['home'])}"
    member_text = "\n".join([f"🔒 PREMIUM PICK · NFL", "", f"{row['label']} ({row['price_american']}) at {_book(row['book'])}",
                             f"{float(row['units']):g}u (${float(row['units']) * C.UNIT_DOLLARS:,.0f} at ${C.UNIT_DOLLARS}/unit)",
                             f"⏰ {when} · good to {row['play_to']}",
                             f"🕒 Stamped {now.astimezone(TZ):%a %-I:%M %p} ET"] + [""] + social.market_reasons(row, len(p.books_with_edge)))
    teaser = "\n".join([f"🔒 We have a PREMIUM pick for today's {when} {game} game.", "",
                         "We really like this spot. Our strongest number of the day that isn't on the free card.",
                         "", "Members get it now. Result posts here after the game, win or lose. Link in bio 👀"])
    teaser = social.tagged(teaser, f"#NFL #{_nick(row['home'])} #{_nick(row['away'])}")
    social.save_post("premium_teaser_nfl", key, teaser)
    print(f"[premium] #{row['pick_no']} {row['label']} {row['price_american']} edge {p.ev:+.1%}")
    if post:
        for chat in (C.TG_PREMIUM, C.TG_VIP, C.TG_OWNER):
            if chat:
                telegram.send(chat, member_text)
        pid = social.post_x(teaser, mirror=False)
        telegram.send(C.TG_FREE, teaser, telegram.sales_buttons() if telegram.selling() else None)
        if pid:
            state.setdefault("threads", {})[f"{card_id}|premium"] = {"main": pid, "last": pid}
    return row


# ---------------------------------------------------------------- grading
def _norm(s):
    return espn.norm(s)


def scored(player: str, plays: list[dict]) -> bool:
    """Did the player score a (non-passing) touchdown? ESPN text: 'Bijan Robinson 12 Yd Run (Koo Kick)',
    'Drake London 25 Yd pass from Michael Penix Jr. (Koo Kick)'. The scorer is named before ' from '."""
    parts = _norm(player).split()
    if not parts:
        return False
    first, last = parts[0], parts[-1]
    for sp in plays:
        if "touchdown" not in ((sp.get("type") or {}).get("text", "") or "").lower() and "td" not in \
                ((sp.get("type") or {}).get("abbreviation", "") or "").lower():
            continue
        who = _norm(re.split(r"\bfrom\b", sp.get("text", ""), maxsplit=1)[0])
        who = re.split(r"\s\d+\s", " " + who + " ", maxsplit=1)[0]
        if f" {last}" in f" {who}" and (first in who or who.split()[0][:1] == first[:1]):
            return True
    return False


def _summary(game_id) -> dict:
    try:
        r = requests.get(SUMMARY, params={"event": game_id}, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:                        # noqa: BLE001
        print(f"[parlays] ESPN summary {game_id}: {e}")
        return {}


def leg_result(leg, feed) -> str | None:
    g, swapped = espn.lookup(feed, {"sport": NFL, **leg})
    if not g or not g.get("completed"):
        return None
    if leg["kind"] == "td":
        return "win" if scored(leg["player"], _summary(g["id"]).get("scoringPlays") or []) else "loss"
    home, away = (g["away"], g["home"]) if swapped else (g["home"], g["away"])
    game = {"completed": True, "home_team": leg["home"], "away_team": leg["away"],
            "scores": [{"name": leg["home"], "score": home["score"]}, {"name": leg["away"], "score": away["score"]}]}
    res = grade({**leg, "units": "1", "price": str(leg["price"])}, game)
    return res[0] if res else None


def result_post(pr, legs, marks) -> str:
    name, emoji, units, _ = KINDS[pr["name"]]
    p = float(pr["profit"])
    head = {"win": f"✅ {name} CASHED {fmt_american(float(pr['price']))} 💰",
            "loss": f"❌ {name} didn't get there",
            "push": f"➖ {name} pushed"}[pr["result"]]
    lines = [head, ""]
    for l in legs:
        lines.append(f"{ {'win': '✅', 'loss': '❌', 'push': '➖'}.get(marks.get(l['label']), '⏳') } {l['label']}")
    sign = "+" if p >= 0 else "-"
    lines += ["", f"{p:+.2f}u ({sign}${abs(p) * C.UNIT_DOLLARS:,.0f})"]
    return social.tagged("\n".join(lines), "#NFL #GamblingTwitter")


def settle(now, state, feed, post):
    prs = read()
    changed = False
    for pr in prs:
        if pr.get("result"):
            continue
        legs = json.loads(pr["legs"])
        if now < min(parse_time(l["commence_time"]) for l in legs):
            continue
        marks = {}
        for l in legs:
            r = leg_result(l, feed)
            if r:
                marks[l["label"]] = r
        lost = any(v == "loss" for v in marks.values())
        if not lost and len(marks) < len(legs):
            continue
        units = float(pr["units"])
        if lost:
            pr["result"], pr["profit"] = "loss", f"{-units:.3f}"
        else:
            live = [float(l["price"]) for l in legs if marks[l["label"]] == "win"]
            if not live:
                pr["result"], pr["profit"] = "push", "0.000"
            else:
                d = 1.0
                for x in live:
                    d *= x
                pr["result"], pr["profit"] = "win", f"{units * (d - 1):.3f}"
        pr["final"] = json.dumps(marks)
        changed = True
        text = result_post(pr, legs, marks)
        social.save_post(f"parlay_result_{pr['name']}", pr["date"], text)
        print(f"[parlays] {pr['id']} {pr['result']} {pr['profit']}u")
        if post:
            t = state.setdefault("threads", {}).get(f"parlay:{pr['id']}", {})
            social.post_x(text, reply_to=None if pr["result"] == "win" else t.get("last"))   # wins get their own post
    if changed:
        write(prs)
