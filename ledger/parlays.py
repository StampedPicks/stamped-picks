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
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from . import config as C, espn, social, store, tags, telegram, sim
from .grading import grade
from .model import parse_time, fmt_american

NFL = "americanfootball_nfl"
TZ = ZoneInfo(C.DISPLAY_TZ)
FILE = C.DATA / "parlays.csv"
COLS = ["id", "date", "name", "posted_at", "legs", "price", "units", "result", "profit", "final"]
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
NUM = social.NUM
TD_VIG = 1.12            # anytime-TD markets carry roughly 10-15% hold; strip it to estimate a fair chance

KINDS = {   # name, emoji, units, legs
    "ladder": ("ONE LEG PER GAME", "🎟️", 0.5, (3, 16)),
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


PROP_MARKETS = ["player_anytime_td", "player_rush_yds", "player_reception_yds", "spreads_h1"]
PROP_WORDS = {"player_rush_yds": "rush yds", "player_reception_yds": "rec yds"}


def prop_markets(api) -> list[str]:
    """All prop markets when credits allow; on a squeezed free plan, TD scorers only (1 credit per game)."""
    try:
        left = int(float(api.remaining)) if api.remaining is not None else None
    except (TypeError, ValueError):
        left = None
    if C.ODDS_PLAN == "free" and (left is None or left < 150):
        return ["player_anytime_td"]
    return PROP_MARKETS


def _pair_key(market, o):
    pt = o.get("point")
    return (o.get("description") or "", abs(pt) if market.startswith("spreads") and pt is not None else pt)


def prop_legs(api, events, books, markets=None) -> tuple[list[dict], list[dict]]:
    """One Odds API call per game for its props. Returns (TD legs, other prop legs).
    TD: per game, the scorer whose best price beats the rest of the market by the most (-200 to +250).
    Yards and 1st-half spreads are two-sided, so each book's pair is de-vigged and the median is the fair
    chance; every option gets its best price and its edge, and build() takes the best one per game."""
    markets = markets or ["player_anytime_td"]
    tds, props = [], []
    for ev in events:
        try:
            data = api.event_odds(NFL, ev["id"], markets, books)
        except SystemExit:
            raise
        except Exception as e:                    # noqa: BLE001
            print(f"[parlays] props failed for {ev.get('away_team')} @ {ev.get('home_team')}: {e}")
            continue
        base = {"event_id": ev["id"], "commence_time": ev["commence_time"], "home": ev["home_team"],
                "away": ev["away_team"]}
        td_prices, opts = {}, {}
        for bk in data.get("bookmakers", []):
            for m in bk.get("markets", []):
                mk, outs = m.get("key"), m.get("outcomes", [])
                if mk == "player_anytime_td":
                    for o in outs:
                        player = o.get("description") or o.get("name")
                        if o.get("name") != "No" and player:
                            td_prices.setdefault(player, []).append((o["price"], bk["key"]))
                    continue
                pairs = {}
                for o in outs:
                    pairs.setdefault(_pair_key(mk, o), []).append(o)
                for pair in pairs.values():
                    if len(pair) != 2:
                        continue
                    inv = [1 / o["price"] for o in pair]
                    for o, q in zip(pair, inv):
                        k = (mk, o.get("description") or "", o["name"], o.get("point"))
                        opts.setdefault(k, []).append((q / sum(inv), o["price"], bk["key"]))
        best = None
        for player, ps in td_prices.items():
            if len(ps) < 2:
                continue
            top, book = max(ps)
            if not (1.5 <= top <= 3.5):
                continue
            value = statistics.median(1 / x for x, _ in ps) / TD_VIG * top - 1
            if best is None or value > best["ev"]:
                best = {**base, "kind": "td", "player": player, "label": f"{player} anytime TD",
                        "price": round(top, 4), "book": book, "ev": value}
        if best:
            tds.append(best)
        for (mk, player, side, point), ps in opts.items():
            if len(ps) < 2 or point is None:
                continue
            fair = statistics.median(f for f, _, _ in ps)
            price, _, book = max((x, -i, b) for i, (_, x, b) in enumerate(ps))
            if mk == "spreads_h1":
                label = f"{side} {point:+g} 1st half"
            else:
                label = f"{player} {side.lower()} {point:g} {PROP_WORDS.get(mk, mk)}"
            props.append({**base, "kind": "prop", "market": mk, "player": player, "side": side, "point": point,
                          "label": label, "price": round(price, 4), "book": book, "ev": fair * price - 1,
                          "fair": fair})
    return sorted(tds, key=lambda x: x["ev"], reverse=True), props


def td_legs(api, events, books) -> list[dict]:
    return prop_legs(api, events, books)[0]


def ladder_legs(cands, tds, props, exclude=frozenset(), lo=1.5, hi=2.4) -> list[dict]:
    """ONE LEG PER GAME, every game on the day: for each game the single best-value bet we can find, whether
    it's a side, a total, a 1st-half spread, a player's yards or an anytime TD, priced -200 to +140."""
    best = {}
    def offer(eid, ev, leg):
        if eid not in exclude and (eid not in best or ev > best[eid][0]):
            best[eid] = (ev, leg)
    for p in cands:
        if lo <= p.price <= hi:
            offer(p.event_id, p.ev, game_leg(p))
    for x in props:
        if lo <= x["price"] <= hi:
            offer(x["event_id"], x["ev"], {k: v for k, v in x.items() if k not in ("ev", "fair")})
    for t in tds:
        if lo <= t["price"] <= hi:
            offer(t["event_id"], t["ev"], {k: v for k, v in t.items() if k != "ev"})
    return sorted((leg for _, leg in best.values()), key=lambda l: (l["commence_time"], l["label"]))


def build(cands, tds, exclude=frozenset(), props=None) -> dict:
    """{kind: [legs]} for the three parlays. Every leg is from a different game; games in `exclude` (the
    premium pick's game) are left out so a public parlay never gives the premium side away."""
    cands = [p for p in cands if p.event_id not in exclude]
    tds = [t for t in tds if t["event_id"] not in exclude]
    out = {}
    out_ladder = ladder_legs(cands, tds, props or [], exclude)
    if len(out_ladder) >= 3:
        out["ladder"] = out_ladder
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


def posted_today(state, day) -> bool:
    return day.isoformat() in state.get("parlays_posted", [])


def run(api, now, state, post, sport, day, cands, events, card_ids: set, to_row, card_id: str,
        day_cands=None, day_events=None):
    """Called by run_card for NFL after the card is picked. The premium pick and every-game slate cover this
    card's games; the parlays (once a day, with the first card) cover every game left on the day."""
    if sport != NFL:
        return
    day_cands, day_events = day_cands or cands, day_events or events
    done = state.setdefault("parlays_posted", [])
    key = day.isoformat()
    if card_id in done:                           # premium + every-game picks: once per card (each kickoff slot)
        return
    done.append(card_id)
    rows = store.read(store.LEDGER)
    card_rows = [r for r in rows if r.get("card") == card_id]
    slate_id = f"{card_id}|slate"
    prem = premium(now, state, post, key, cands, card_ids, to_row, card_id)
    rows = store.read(store.LEDGER)               # re-read: the premium pick took a number
    slate = []
    for p in best_per_game(cands, 1.8, 2.25):
        if p.event_id in card_ids or (prem and p.event_id == prem["event_id"]):
            continue
        p.units, p.tier = 0.5, "slate"
        row = to_row(p, key, now, "slate", pick_no=store.next_no(rows, len(slate)), card=slate_id)
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
    if key in done:                               # parlays: once a day, with the first card
        return
    done.append(key)
    tds, props = [], []
    try:
        tds, props = prop_legs(api, day_events, list(C.BET_BOOKS), prop_markets(api))
    except SystemExit as e:
        print(f"[parlays] props unavailable: {e}")
    built = build(day_cands, tds, {prem["event_id"]} if prem else frozenset(), props)
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


def _solid(p) -> bool:
    """Premium has to be a play we'd stand behind: researched and backed (confidence 6+), or a full edge."""
    a = getattr(p, "analysis", None) or {}
    if a.get("verdict") == "pass" or (getattr(p, "sim", None) or {}).get("agrees") is False:
        return False
    return a.get("confidence", 0) >= 6 or p.ev >= C.MIN_EV


def member_text(row, p=None) -> str:
    when = parse_time(row["commence_time"]).astimezone(TZ).strftime("%a %-I:%M %p ET")
    lines = [f"🔒 PREMIUM PICK · {C.SPORTS.get(row['sport'], (row['sport'],))[0]}", "",
             f"{row['label']} ({row['price_american']}) at {_book(row['book'])}",
             f"{float(row['units']):g}u (${float(row['units']) * C.UNIT_DOLLARS:,.0f} at ${C.UNIT_DOLLARS}/unit)",
             f"⏰ {when} · good to {row['play_to']}",
             f"🕒 Stamped {parse_time(row['posted_at']).astimezone(TZ):%a %-I:%M %p} ET"]
    if p is not None:
        lines += [""] + social.market_reasons(row, len(p.books_with_edge)) + sim.line(p)
        a = getattr(p, "analysis", None)
        if a:
            lines += [f"🎯 {a['angle']}"] + [f"• {x}" for x in a.get("reasons", [])]
            if a.get("risk"):
                lines.append(f"⚠️ Risk: {a['risk']}")
    return "\n".join(lines)


def _board(cands, p) -> dict:
    """The market for the premium game, as shown on the teaser board (best price per side)."""
    best = {}
    for c in cands:
        if c.event_id != p.event_id:
            continue
        k = (c.market, c.side)
        if k not in best or c.price > best[k].price:
            best[k] = c
    out = {"away": {}, "home": {}}
    for (mk, side), c in best.items():
        price = fmt_american(c.price)
        if mk == "h2h":
            out["home" if side == p.home else "away"]["money"] = price
        elif mk == "spreads" and c.point is not None:
            out["home" if side == p.home else "away"]["spread"] = f"{c.point:+g} {price}"
        elif mk == "totals" and c.point is not None:
            out["away" if side == "Over" else "home"]["total"] = f"{'O' if side == 'Over' else 'U'} {c.point:g} {price}"
    return out


def _record_line(sport) -> str:
    rows = [r for r in store.read(store.LEDGER) if r["sport"] == sport]
    st = store.stats(rows)
    if st["wins"] + st["losses"] < 5 or st["profit"] <= 0:
        return ""                                  # only brag when it's real and worth it
    name = C.SPORTS.get(sport, (sport,))[0]
    return f"{name} record: {st['wins']}-{st['losses']}{'-' + str(st['pushes']) if st['pushes'] else ''}, {st['profit']:+.2f}u 📈"


def premium(now, state, post, key, cands, card_ids, to_row, card_id) -> dict | None:
    """The strongest play off the public card goes to members only, sealed in the vault (the repo is public)
    until its game starts. The public gets the hook: an odds board for the game and a line with some
    attitude, then the promo, then a reply-to-win giveaway. Never the side. The result posts after the game."""
    from . import writer, giveaway
    pool = [p for p in sorted(cands, key=lambda p: p.score, reverse=True)
            if p.event_id not in card_ids and 1.6 <= p.price <= 2.6 and p.ev >= C.LEAN_FLOOR and _solid(p)]
    if not pool:
        print("[premium] nothing solid enough off the card this time")
        return None
    p = pool[0]
    p.units, p.tier = (1.5 if p.ev >= C.MIN_EV else 1.0), "premium"
    row = to_row(p, key, now, "premium", pick_no=store.next_no(store.read(store.LEDGER)), card=f"{card_id}|premium")
    store.vault_hold(row)
    when = parse_time(row["commence_time"]).astimezone(TZ).strftime("%-I:%M %p ET")
    game = f"{_nick(row['away'])}-{_nick(row['home'])}"
    board = _board(cands, p)
    facts = (f"Game: {row['away']} at {row['home']}, {when} today.\n"
             f"Market: {row['home']} spread {board['home'].get('spread', 'n/a')}, moneyline {board['home'].get('money', 'n/a')}; "
             f"{row['away']} spread {board['away'].get('spread', 'n/a')}, moneyline {board['away'].get('money', 'n/a')}; "
             f"total {board['away'].get('total', 'n/a')} / {board['home'].get('total', 'n/a')}.\n"
             "We have a premium play on this game. Write a one or two line hook about the line or the game "
             "that makes people curious, like 'The Packers are 3.5 point favorites. This line is stupid 😂'.")
    hook = writer.hook("premium teaser (posted with a picture of the odds board)", facts, limit=160, secret=True) \
        or f"{_nick(row['away'])} at {_nick(row['home'])}, {when}. Somebody hung the wrong number here 👀"
    hook = social.tagged(hook, f"{tags.SPORT.get(row['sport'], '')} #{_nick(row['home'])} #{_nick(row['away'])}")
    rec = _record_line(row["sport"])
    link = next((url for k, _, _, url in C.SALES if k == "pick" and url), "")
    cta = f"Get it now: {link}" if link else "Members get it now. DM us for access 🔒"
    promo = "\n".join([f"🔒 PREMIUM PLAY · {game} · {when}", ""] + ([rec, ""] if rec else [])
                       + [cta, "", "Result posts here after the game, win or lose."])
    social.save_post("premium_teaser", key, [hook, promo])
    print(f"[premium] #{row['pick_no']} sealed until {when} (edge {p.ev:+.1%})")
    if post:
        for chat in (C.TG_PREMIUM, C.TG_VIP, C.TG_OWNER):
            if chat:
                telegram.send(chat, member_text(row, p))
        pic = None
        try:
            from . import graphics
            day = parse_time(row["commence_time"]).astimezone(TZ).strftime("%a %b %-d · %-I:%M %p ET").upper()
            pic = graphics.save(graphics.odds_board(row["away"], row["home"], day,
                                                    C.SPORTS.get(row["sport"], (row["sport"],))[0], board),
                                Path(tempfile.gettempdir()) / f"board_{row['pick_no']}.png")
        except Exception as ex:                   # noqa: BLE001 - a picture is never worth a missed post
            print(f"[premium] board image failed: {ex}")
        ids = social.post_thread([hook, promo], media=pic)
        telegram.send(C.TG_FREE, hook + "\n\n" + promo, telegram.sales_buttons() if telegram.selling() else None)
        if ids:
            state.setdefault("threads", {})[f"{card_id}|premium"] = {"main": ids[0], "last": ids[-1]}
        giveaway.schedule(state, now, row, post, when)
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


def _cmp(value, side, point) -> str:
    diff = (value - point) if side in ("Over",) else (point - value) if side == "Under" else value + point
    return "win" if diff > 0 else "loss" if diff < 0 else "push"


def player_stat(summary, player, group) -> float | None:
    """A player's yards from ESPN's box score ('rushing' or 'receiving'); None if he has no line."""
    want = _norm(player)
    for team in (summary.get("boxscore") or {}).get("players", []):
        for st in team.get("statistics", []):
            if st.get("name") != group:
                continue
            labels = [x.upper() for x in st.get("labels", [])]
            if "YDS" not in labels:
                continue
            for a in st.get("athletes", []):
                if _norm((a.get("athlete") or {}).get("displayName", "")) == want:
                    try:
                        return float(str(a["stats"][labels.index("YDS")]).replace(",", ""))
                    except (ValueError, IndexError, KeyError):
                        return None
    return None


def prop_result(leg, summary) -> str | None:
    if not summary:
        return None
    if leg["market"] == "spreads_h1":
        comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
        pts = {}
        for c in comp.get("competitors", []):
            ls = c.get("linescores") or []
            if len(ls) < 2:
                return None
            name = _norm((c.get("team") or {}).get("displayName", ""))
            pts[name] = sum(float(x.get("displayValue", x.get("value", 0)) or 0) for x in ls[:2])
        mine = next((v for k, v in pts.items() if k == _norm(leg["side"]) or k.split()[-1] == _norm(leg["side"]).split()[-1]), None)
        if mine is None or len(pts) != 2:
            return None
        other = sum(pts.values()) - mine
        return _cmp(mine - other, leg["side"], float(leg["point"]))
    group = {"player_rush_yds": "rushing", "player_reception_yds": "receiving"}.get(leg["market"])
    v = player_stat(summary, leg["player"], group) if group else None
    if v is None:
        return "push" if group else None          # no line in the box score = didn't play: books void it
    return _cmp(v, leg["side"], float(leg["point"]))


def leg_result(leg, feed) -> str | None:
    g, swapped = espn.lookup(feed, {"sport": NFL, **leg})
    if not g or not g.get("completed"):
        return None
    if leg["kind"] == "td":
        return "win" if scored(leg["player"], _summary(g["id"]).get("scoringPlays") or []) else "loss"
    if leg["kind"] == "prop":
        return prop_result(leg, _summary(g["id"]))
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
