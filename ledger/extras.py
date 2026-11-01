"""Slot extras for NFL cards: a player prop and a "who wins" pick, each with a graphic and a data write-up.

Posted with every NFL kickoff-slot card (1 PM, 4 PM, night), never instead of it.

PLAYER PROP
  1. Prop prices for the slot's games (passing, rushing and receiving yards, receptions): one Odds API call
     per game. Each book's over/under pair is de-vigged; the median is our fair chance; the best price on
     the board gives the edge.
  2. The best few edges (priced -170 to +130, different players) go to the analyst with web search for the
     numbers behind them: season average, last 5 games, the opponent's rank against that stat, role and
     snap share, injuries, weather. A prop only posts if the research backs it with real numbers
     (confidence 6+). Otherwise no prop for that slot: never a forced play.
  3. Kept in data/props.csv with its own record, graded from ESPN's box score after the game.

WHO WINS
  For every game in the slot: our projected score (10,000 simulations) and each team's win chance, blended
  with the sharp market. The play is the side we like most among the slot's moneylines and spreads that the
  research backed. It goes on the main ledger (graded like every pick).
"""
from __future__ import annotations
import csv
import json
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config as C, research, social, store, tags, sim
from .model import parse_time, fmt_american

TZ = ZoneInfo(C.DISPLAY_TZ)
NFL = "americanfootball_nfl"
FILE = C.DATA / "props.csv"
COLS = ["id", "date", "posted_at", "event_id", "commence_time", "home", "away", "market", "player", "side", "point",
        "label", "price", "book", "fair", "ev", "units", "result", "profit", "value", "thread"]
PROP_MARKETS = ["player_pass_yds", "player_rush_yds", "player_reception_yds", "player_receptions"]
WORDS = {"player_pass_yds": "passing yards", "player_rush_yds": "rushing yards",
         "player_reception_yds": "receiving yards", "player_receptions": "receptions"}
SHORT = {"player_pass_yds": "pass yds", "player_rush_yds": "rush yds", "player_reception_yds": "rec yds",
         "player_receptions": "rec"}


def read() -> list[dict]:
    if not FILE.exists():
        return []
    with FILE.open() as fh:
        return list(csv.DictReader(fh))


def write(rows):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    with FILE.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLS})


def _when(iso) -> str:
    return parse_time(iso).astimezone(TZ).strftime("%a %-I:%M %p ET")


def _pic(row: dict, title: str) -> Path | None:
    if not C.GRAPHICS:
        return None
    try:
        from . import graphics
        return graphics.save(graphics.ticket(row, title), Path(tempfile.gettempdir()) / f"{title.split()[0]}_{row['pick_no']}.png")
    except Exception as e:                        # noqa: BLE001 - a picture is never worth a missed post
        print(f"[extras] graphic failed: {e}")
        return None


def _research(kind: str, items: list[str]) -> list[dict]:
    """The numbers behind each candidate, from live search. Returns verdict dicts in the same order."""
    prompt = f"""You are the lead NFL handicapper for a betting account whose brand is credibility. Every claim you
make will be posted publicly, so it must be a real, checkable number from this season.

Candidate {kind} bets for today:
{chr(10).join(f"{i}. {t}" for i, t in enumerate(items, 1))}

For EACH candidate, search for the numbers that decide it: {"the player's season average and median for this stat, his last 5 games, the opponent's rank and yards/receptions allowed to his position, his snap share and target/carry share, injuries to him or around him, game script (spread and total) and weather" if kind == "player prop" else "both teams' records, points scored and allowed per game, offensive and defensive efficiency (EPA/play or yards per play), injuries and who is out, recent form, head-to-head and home/road splits, and how the line has moved"}.
Then decide: does the data back the bet? Say "pass" if it doesn't, or if a specific fact cuts against it.

Rules: every reason must contain a specific number from this season. Never invent a number. Never use the words
lock, guarantee, sure thing, can't lose, hammer or max bet. Write like a sharp bettor talking to his followers.
Work fast: short notes between searches, then the JSON.

Reply with only this JSON:
{{"picks": [{{"n": 1, "verdict": "bet" or "pass", "confidence": 1-10, "angle": "one punchy line, max 110 chars",
"reasons": ["3-4 data points, each max 170 chars, each with a number"], "risk": "max 130 chars"}}]}}"""
    try:
        text, _ = research._call(prompt, 12, max_tokens=12000)
        data = research._json(text)
    except Exception as e:                        # noqa: BLE001
        print(f"[extras] research failed: {e}")
        return [{} for _ in items]
    by_n = {int(v.get("n", 0)): v for v in data.get("picks", []) if isinstance(v, dict)}
    out = []
    for i in range(1, len(items) + 1):
        v = by_n.get(i) or {}
        reasons = [r for r in (research._clean(x, 175) for x in (v.get("reasons") or [])[:4]) if r and any(c.isdigit() for c in r)]
        out.append({"verdict": "pass" if str(v.get("verdict", "")).lower().startswith("pass") else "bet",
                    "confidence": max(1, min(10, int(v.get("confidence") or 0))) if v else 0,
                    "angle": research._clean(v.get("angle"), 115), "reasons": reasons,
                    "risk": research._clean(v.get("risk"), 135)} if v else {})
    return out


def _research_props(pool) -> list[dict]:
    """For each prop line (both sides priced), the research picks the side the numbers support."""
    items = []
    for i, v in enumerate(pool, 1):
        o, u = v["Over"], v["Under"]
        items.append(f"{i}. {o['player']} {WORDS[o['market']]} {o['point']:g} ({o['away']} at {o['home']}, "
                     f"{_when(o['commence_time'])}): OVER {fmt_american(o['price'])}, UNDER {fmt_american(u['price'])}. "
                     f"Market fair chance of the over: {o['fair']:.0%}.")
    prompt = f"""You are the lead NFL props handicapper for a betting account whose brand is credibility. Every claim is
posted publicly, so it must be a real, checkable number from this season.

Today's player prop lines:
{chr(10).join(items)}

For EACH line, search the numbers that decide it: his season average and median for this stat, his last 5 games,
the opponent's rank and what it allows to his position, his snap/target/carry share, injuries to him or around him
(QB, O-line, other receivers), game script (spread and total) and weather. Then pick the side the numbers support,
OVER or UNDER, and how strongly (confidence 1-10). Say "pass" only if the numbers are a true coin flip.

Rules: every reason must contain a specific number from this season. Never invent a number. Never use the words
lock, guarantee, sure thing, can't lose, hammer or max bet. Write like a sharp bettor. Short notes between searches.

Reply with only this JSON:
{{"props": [{{"n": 1, "pick": "over" or "under" or "pass", "confidence": 1-10, "angle": "one punchy line, max 110 chars",
"reasons": ["3-4 data points, each max 170 chars, each with a number"], "risk": "max 130 chars"}}]}}"""
    try:
        text, _ = research._call(prompt, 20, max_tokens=16000)
        data = research._json(text)
    except Exception as e:                        # noqa: BLE001
        print(f"[extras] prop research failed: {e}")
        return [{} for _ in pool]
    by_n = {int(v.get("n", 0)): v for v in data.get("props", []) if isinstance(v, dict)}
    out = []
    for i in range(1, len(pool) + 1):
        v = by_n.get(i) or {}
        pick = str(v.get("pick", "")).lower()
        out.append({"side": "Over" if pick.startswith("o") else "Under" if pick.startswith("u") else None,
                    "confidence": max(1, min(10, int(v.get("confidence") or 1))),
                    "angle": research._clean(v.get("angle"), 115),
                    "reasons": [r for r in (research._clean(x, 175) for x in (v.get("reasons") or [])[:4])
                                if r and any(c.isdigit() for c in r)],
                    "risk": research._clean(v.get("risk"), 135)} if v else {})
    for v, a in zip(pool, out):
        if a.get("side"):
            print(f"[extras] {v['Over']['player']} {WORDS[v['Over']['market']]} {v['Over']['point']:g}: "
                  f"{a['side']} {a['confidence']}/10")
    return out


def _backed(a: dict) -> bool:
    return bool(a) and a.get("verdict") == "bet" and a.get("confidence", 0) >= 6 and len(a.get("reasons", [])) >= 2


# ---------------------------------------------------------------- player prop
def prop(api, now, state, post, events, slot_name: str) -> dict | None:
    from .parlays import prop_legs
    if not events:
        return None
    try:
        _, props = prop_legs(api, events, list(C.BET_BOOKS), PROP_MARKETS)
    except SystemExit as e:
        print(f"[extras] props unavailable: {e}")
        return None
    print(f"[extras] {len(props)} prop prices across {len(events)} games")
    # Every line, both sides: the research picks the side the numbers support, not just the side the prices lean.
    lines = {}
    for x in props:
        if x["market"] in WORDS and x["player"]:
            lines.setdefault((x["event_id"], x["market"], x["player"], x["point"]), {})[x["side"]] = x
    pairs = [v for v in lines.values() if "Over" in v and "Under" in v
             and all(1.45 <= v[s]["price"] <= 2.5 for s in ("Over", "Under"))]
    if not pairs:
        print("[extras] no two-sided prop lines in this slot")
        return None
    # The pool is the players people actually bet on: each game's QB passing line and its biggest rushing and
    # receiving lines (the starters), plus the best-priced lines on the board. Fringe receptions lines don't lead.
    pool, seen = [], set()

    def add(v):
        if v["Over"]["player"] not in seen and len(pool) < 10:
            seen.add(v["Over"]["player"])
            pool.append(v)
    for eid in sorted({v["Over"]["event_id"] for v in pairs}):
        for mk, n in (("player_pass_yds", 1), ("player_rush_yds", 1), ("player_reception_yds", 2)):
            for v in sorted((v for v in pairs if v["Over"]["event_id"] == eid and v["Over"]["market"] == mk),
                            key=lambda v: v["Over"]["point"], reverse=True)[:n]:
                add(v)
    for v in sorted(pairs, key=lambda v: max(v["Over"]["ev"], v["Under"]["ev"]), reverse=True)[:3]:
        add(v)
    verdicts = _research_props(pool)
    scored = []
    for v, a in zip(pool, verdicts):
        side = a.get("side")
        if side not in ("Over", "Under") or len(a.get("reasons", [])) < 2:
            continue
        x = v[side]
        if x["ev"] < -0.06:                        # standard -110 juice is fine; never pay a big premium over it
            continue
        scored.append((a["confidence"], x["ev"], x, a))
    if not scored:
        print("[extras] research came back without a usable prop")
        return None
    conf, _, x, a = max(scored, key=lambda t: (t[0], t[1]))
    units = 1.5 if conf >= 8 else 1.0 if conf >= 6 else 0.5
    best = (x, a)
    x, a = best
    rows = read()
    pid = f"P{len(rows) + 1}"
    label = f"{x['player']} {x['side'].lower()} {x['point']:g} {WORDS[x['market']]}"
    row = {"id": pid, "date": now.astimezone(TZ).date().isoformat(), "posted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "event_id": x["event_id"], "commence_time": x["commence_time"], "home": x["home"], "away": x["away"],
           "market": x["market"], "player": x["player"], "side": x["side"], "point": f"{x['point']:g}", "label": label,
           "price": f"{x['price']:.4f}", "book": x["book"], "fair": f"{x['fair']:.4f}", "ev": f"{x['ev']:.4f}", "units": f"{units:g}",
           "result": "", "profit": "", "value": ""}
    rec = record_line()
    head = "\n".join([f"🎯 PLAYER PROP · {slot_name}", "", f"{label} ({fmt_american(x['price'])}) at {social._book(x['book'])}",
                      f"⏰ {_when(x['commence_time'])} · {units:g}u", "", f"🧠 {a['angle']}" if a.get("angle") else ""]
                     + ([rec] if rec else []) + ["", "The numbers 🧵👇"])
    head = social.tagged(head.replace("\n\n\n", "\n\n"), "#NFL #NFLProps #PlayerProps #GamblingTwitter")
    numbers = "\n".join(["📊 The data:"] + [f"• {r}" for r in a["reasons"]]
                        + [f"• Market: the books' fair price makes this a {x['fair']:.0%} shot. "
                           f"{social._book(x['book'])} pays {fmt_american(x['price'])}"
                           + (f", {x['ev']:+.1%} edge." if x["ev"] > 0.005 else ", right around fair. The data is the edge here.")])
    tail = "\n".join(([f"⚠️ Risk: {a['risk']}"] if a.get("risk") else []) + [f"Confidence: {a['confidence']}/10",
                     "Graded from the box score after the game, win or lose."])
    print(f"[extras] prop {pid}: {label} {fmt_american(x['price'])} ({a['confidence']}/10)")
    social.save_post("prop", row["date"], [head, numbers, tail])
    if post:
        ticket = {**row, "sport": NFL, "pick_no": pid.lstrip("P"), "price_american": fmt_american(x["price"]),
                  "play_to": fmt_american(1 / x["fair"]), "units": f"{units:g}",
                  "label": f"{x['player'].split()[-1]} {x['side']} {x['point']:g} {SHORT[x['market']]}".upper()}
        ids = social.post_thread([head, numbers, tail], media=_pic(ticket, "PLAYER PROP"))
        row["thread"] = ids[-1] if ids else ""
    rows.append(row)
    write(rows)
    return row


def record_line() -> str:
    done = [r for r in read() if r.get("result") in ("win", "loss", "push")]
    w, l = sum(r["result"] == "win" for r in done), sum(r["result"] == "loss" for r in done)
    if w + l < 5:
        return ""
    u = sum(float(r["profit"] or 0) for r in done)
    return f"Props record: {w}-{l}, {u:+.2f}u" if u > 0 else ""


def settle(now, state, feed, post):
    from .parlays import prop_result, _summary
    from . import espn
    rows, changed = read(), False
    for r in rows:
        if r.get("result") or now < parse_time(r["commence_time"]):
            continue
        g, _ = espn.lookup(feed, {"sport": NFL, "home": r["home"], "away": r["away"], "commence_time": r["commence_time"]})
        if not g or not g.get("completed"):
            continue
        res = prop_result({"market": r["market"], "player": r["player"], "side": r["side"], "point": r["point"]},
                          _summary(g["id"]))
        if not res:
            continue
        price = float(r["price"])
        r["result"], r["profit"] = res, f"{(price - 1) if res == 'win' else -1.0 if res == 'loss' else 0.0:.3f}"
        changed = True
        head = {"win": "✅ PROP CASHED", "loss": "❌ Prop missed", "push": "➖ Prop pushed"}[res]
        text = f"{head}\n\n{r['label']} ({fmt_american(price)})\n{float(r['profit']):+.2f}u"
        print(f"[extras] prop {r['id']} {res}")
        social.save_post("prop_result", r["date"], text)
        if post:
            social.post_x(text, reply_to=r.get("thread") or None)
    if changed:
        write(rows)


# ---------------------------------------------------------------- who wins
def who_wins(now, state, post, cands, slot_name, card_ids, to_row, cid, thread) -> dict | None:
    """Projected winner for every game in the slot, and our side play (ML or spread) backed by research."""
    games = {}
    for p in cands:
        games.setdefault(p.event_id, p)
    lines = []
    for eid, p in sorted(games.items(), key=lambda t: t[1].commence_time):
        s = getattr(p, "sim", None)
        if not s:
            continue
        hp, ap = s["home_proj"], s["away_proj"]
        win = max((c for c in cands if c.event_id == eid and c.market == "h2h"), key=lambda c: c.fair_prob, default=None)
        fav = (p.home if hp >= ap else p.away).split()[-1]
        pct = f" ({win.fair_prob:.0%})" if win and win.side.split()[-1] == fav else ""
        lines.append(f"• {p.away.split()[-1]} {ap:.0f}, {p.home.split()[-1]} {hp:.0f} → {fav}{pct}")
    sides = [p for p in cands if p.market in ("h2h", "spreads") and p.event_id not in card_ids
             and 1.5 <= p.price <= 2.5 and p.ev >= C.LEAN_FLOOR and _backed(getattr(p, "analysis", None) or {})
             and (getattr(p, "sim", None) or {}).get("agrees") is not False]
    if not sides:
        print("[extras] no researched side worth a play this slot")
        if lines and post:
            text = social.tagged("\n".join([f"🏆 WHO WINS · {slot_name}", "", "Our 10,000-game sims:"] + lines),
                                 "#NFL #NFLPicks #GamblingTwitter")
            social.post_x(text)
        return None
    p = max(sides, key=lambda p: (p.analysis["confidence"], p.score))
    p.units, p.tier = 1.0, "side"
    rows = store.read(store.LEDGER)
    day = parse_time(p.commence_time).astimezone(TZ).date().isoformat()
    row = to_row(p, day, now, "side", pick_no=store.next_no(rows), card=f"{cid}|side")
    store.append(store.LEDGER, row)
    a = p.analysis
    head = "\n".join([f"🏆 WHO WINS · {slot_name}", "", "Our 10,000-game sims:"] + lines
                     + ["", f"Our play: {row['label']} ({row['price_american']}) at {social._book(row['book'])} · 1u",
                        "", "Why 🧵👇"])
    head = social.tagged(head, tags.for_rows([row]))
    numbers = "\n".join(["📊 The data:"] + [f"• {r}" for r in a["reasons"][:4]] + social.market_reasons(row, len(p.books_with_edge))
                        + sim.line(p))
    tail = "\n".join(([f"⚠️ Risk: {a['risk']}"] if a.get("risk") else []) + [f"Confidence: {a['confidence']}/10"])
    print(f"[extras] side #{row['pick_no']}: {row['label']} {row['price_american']} ({a['confidence']}/10)")
    social.save_post("who_wins", day, [head, numbers, tail])
    if post:
        ids = social.post_thread([head, numbers, tail], media=_pic(row, "WHO WINS"))
        if ids:
            thread(state, row["card"]).update(main=ids[0], last=ids[-1])
    return row


def run(api, now, state, post, sport, slot, events, cands, card_ids, to_row, cid, thread, force=False,
        parts=("who", "prop")):
    """card_ids: games where the card already has a side (ML/spread) bet, so who-wins picks another game."""
    if sport != NFL:
        return
    name = {"early": "1 PM GAMES", "late": "4 PM GAMES", "night": "PRIMETIME"}.get(slot, "TODAY")
    done = state.setdefault("extras_posted", [])
    if cid in done and not force:
        return
    if cid not in done:
        done.append(cid)
    try:
        if "who" in parts:
            who_wins(now, state, post, cands, name, card_ids, to_row, cid, thread)
    except Exception as e:                        # noqa: BLE001 - extras never block anything
        print(f"[extras] who wins: {e}")
    try:
        if "prop" in parts:
            prop(api, now, state, post, events, name)
    except Exception as e:                        # noqa: BLE001
        print(f"[extras] prop: {e}")
