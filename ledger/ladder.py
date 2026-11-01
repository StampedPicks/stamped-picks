"""THE STAMPED LADDER: $10 to $1,000 in one week, Monday to Sunday.

Every day one rung: the whole stack rides on one play. Seven rungs near even money take $10 past $1,000
(each rung roughly doubles it). One loss and that week's climb is over; a fresh climb starts the next Monday.

  * Which play: the Daily Play when its price is in the ladder's band, otherwise the best-rated play on the
    board in the band. The band is set by what's still needed: (target / stack) ^ (1 / days left), so the
    price only gets longer when the climb is behind schedule, and never past +250.
  * A rung goes up only after the last one is graded. A day with nothing in the band is skipped.
  * Grading uses the same ESPN feed as everything else. Everything lives in data/ladder.json.

The ladder is a fun bankroll challenge for small bettors. It's tracked and posted, but kept off the main
record (units on the ledger stay what they are).
"""
from __future__ import annotations
import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from . import config as C, social
from .model import parse_time, fmt_american

TZ = ZoneInfo(C.DISPLAY_TZ)
FILE = C.DATA / "ladder.json"
START, TARGET = 10.0, 1000.0
NAME = "THE STAMPED LADDER"
LO, HI = 1.8, 3.5            # never shorter than -125, never longer than +250
TAGS = "#SportsBetting #FreePicks #GamblingTwitter"


def load() -> dict:
    return json.loads(FILE.read_text()) if FILE.exists() else {"weeks": []}


def save(d: dict):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(d, indent=1))


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def money(x: float) -> str:
    return f"${x:,.2f}".replace(".00", "")


def current(d: dict, today: date) -> dict:
    """This week's climb, started on its Monday (the first climb starts the first Monday it sees)."""
    mon = _monday(today).isoformat()
    if not d["weeks"] or d["weeks"][-1]["monday"] != mon:
        d["weeks"].append({"week": len(d["weeks"]) + 1, "monday": mon, "stack": START, "status": "climbing",
                           "rungs": []})
    return d["weeks"][-1]


def _leg(p) -> dict:
    return {"sport": p.sport, "event_id": p.event_id, "commence_time": p.commence_time, "home": p.home,
            "away": p.away, "market": p.market, "side": p.side, "point": "" if p.point is None else f"{p.point:g}",
            "label": p.label, "price": round(p.price, 4), "book": p.book, "kind": "game"}


def pick(plays, daily, stack: float, days_left: int):
    need = (TARGET / stack) ** (1 / max(1, days_left))
    lo, hi = max(LO, min(need, HI) * 0.93), HI
    ok = lambda p: lo <= p.price <= hi and (getattr(p, "analysis", None) or {}).get("verdict") != "pass"
    if daily and ok(daily):
        return daily
    good = [p for p in plays if ok(p) and p.ev >= -0.01]
    return max(good, key=lambda p: p.score) if good else None


def rung_post(w, r) -> str:
    n = len(w["rungs"])
    when = parse_time(r["commence_time"]).astimezone(TZ).strftime("%a %-I:%M %p ET")
    win = w["stack"] * r["price"]
    lines = [f"🪜 {NAME} · Week {w['week']} · Rung {n}",
             "",
             f"{money(w['stack'])} rides on: {r['label']} ({fmt_american(r['price'])})",
             f"⏰ {when}",
             f"🎯 A win makes it {money(win)}",
             "",
             f"The goal: {money(START)} to {money(TARGET)} by Sunday. The whole stack rides every rung. "
             f"One loss and we start a new climb Monday.",
             "",
             TAGS]
    if n == 1:
        lines.insert(1, "Week starts now. $10 in, let's climb 🧗")
    return "\n".join(lines)


def result_post(w, r) -> str:
    n = len(w["rungs"])
    if r["result"] == "win" and w["status"] == "done":
        return (f"🏆 {NAME} · Week {w['week']} COMPLETE\n\n✅ {r['label']} cashed.\n\n"
                f"{money(START)} → {money(w['stack'])} in {n} rungs. Every rung stamped before kickoff.\n\n"
                f"New climb starts Monday. {TAGS}")
    if r["result"] == "win":
        return (f"✅ Rung {n} cleared: {r['label']}\n\n🪜 Stack: {money(w['stack'])}. "
                f"{money(TARGET - w['stack'])} to go. Next rung tomorrow.")
    if r["result"] == "push":
        return f"➖ Rung {n} pushed: {r['label']}\n\n🪜 Stack stays {money(w['stack'])}. Next rung tomorrow."
    return (f"❌ The ladder broke on Rung {n}: {r['label']}\n\nWeek {w['week']} topped out at "
            f"{money(r['stack'])}. That's the deal with a ladder. Fresh $10 climb starts Monday 🧗")


def step(now, state, post, plays, daily):
    """Daily: put up today's rung if the climb is alive and the last rung is graded."""
    today = now.astimezone(TZ).date()
    d = load()
    w = current(d, today)
    if w["status"] != "climbing" or any(not r.get("result") for r in w["rungs"]):
        save(d)
        return
    if any(r["date"] == today.isoformat() for r in w["rungs"]):
        return
    days_left = 7 - today.weekday()
    p = pick(plays, daily, w["stack"], days_left)
    if not p:
        print(f"[ladder] week {w['week']}: nothing in the band today")
        save(d)
        return
    r = {**_leg(p), "date": today.isoformat(), "stack": w["stack"], "result": ""}
    w["rungs"].append(r)
    save(d)
    text = rung_post(w, r)
    social.save_post("ladder", today.isoformat(), text)
    print(f"[ladder] week {w['week']} rung {len(w['rungs'])}: {money(w['stack'])} on {r['label']} "
          f"{fmt_american(r['price'])}")
    if post:
        pid = social.post_x(text)
        if pid:
            state.setdefault("threads", {})[f"ladder:{w['week']}"] = {"main": pid, "last": pid}


def settle(now, state, feed, post):
    from .parlays import leg_result
    d = load()
    if not d["weeks"]:
        return
    w = d["weeks"][-1]
    changed = False
    for r in w["rungs"]:
        if r.get("result") or now < parse_time(r["commence_time"]):
            continue
        res = leg_result(r, feed)
        if not res:
            continue
        r["result"], changed = res, True
        if res == "win":
            w["stack"] = round(w["stack"] * r["price"], 2)
            if w["stack"] >= TARGET:
                w["status"] = "done"
        elif res == "loss":
            w["status"] = "busted"
        text = result_post(w, r)
        social.save_post("ladder_result", r["date"], text)
        print(f"[ladder] week {w['week']} rung {w['rungs'].index(r) + 1} {res}: stack {money(w['stack'])}")
        if post:
            t = state.setdefault("threads", {}).setdefault(f"ladder:{w['week']}", {})
            pid = social.post_x(text, reply_to=None if w["status"] == "done" else t.get("last"))
            if pid:
                t["last"] = pid
    if changed:
        save(d)
