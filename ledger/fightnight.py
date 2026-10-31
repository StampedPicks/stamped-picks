"""Fight-night coverage: the whole UFC card, whether or not we have a bet on it.

Each tick on a UFC night:
  * kickoff post once the card is underway or close (event, main event, what the market says, our plays
    or "no edge tonight, we're watching"), which starts a fight-night thread
  * every finished fight goes in the thread as it ends (several at once if they finished between ticks)
  * a main-event poll about an hour before the main event
  * the main-event result as its own post, then a wrap-up once every fight is done

Results come from ESPN's free scoreboard; the market read comes from the odds snapshots the card scan
already saved (no extra API credits). Everything is best-effort: if a piece of data is missing, that line
is simply left out."""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config as C, espn, social, tags, telegram
from .model import parse_time, devig

MMA = "mma_mixed_martial_arts"
TZ = ZoneInfo(C.DISPLAY_TZ)
KICKOFF_BEFORE = timedelta(hours=2)      # kickoff post goes out up to 2h before the first fight
POLL_BEFORE = timedelta(minutes=75)      # main-event poll opens this long before the main event
POLL_MINUTES = 150


# ---------------------------------------------------------------- reading the card
def _last(name: str) -> str:
    return name.split()[-1] if name else name


def card(raw: dict) -> list[dict]:
    """Events on the scoreboard, each with its fights in running order and the main event last."""
    out = []
    for ev in raw.get("events", []) or []:
        fl = []
        for comp in ev.get("competitions", []) or []:
            fs = []
            for c in comp.get("competitors", []) or []:
                a = c.get("athlete") or {}
                fs.append({"name": a.get("displayName") or a.get("fullName") or "", "winner": bool(c.get("winner"))})
            if len(fs) != 2:
                continue
            st = comp.get("status") or {}
            t = st.get("type") or {}
            res = st.get("result") or {}
            fl.append({"id": str(comp.get("id")), "start": comp.get("date") or ev.get("date"), "fighters": fs,
                       "state": t.get("state", ""), "completed": bool(t.get("completed")),
                       "round": int(st.get("period") or 0), "clock": st.get("displayClock") or "",
                       "method": res.get("displayName") or res.get("name") or res.get("shortDisplayName") or ""})
        if fl:
            fl.sort(key=lambda f: (f["start"] or ""))
            out.append({"id": str(ev.get("id")), "name": ev.get("name", "UFC"), "fights": fl})
    return out


def line(f: dict) -> str:
    """'✅ Renato Moicano def. Tom Nolan · KO/TKO R2 3:41'"""
    w = [x for x in f["fighters"] if x["winner"]]
    if not w:
        a, b = f["fighters"]
        return f"➖ {a['name']} vs {b['name']}: draw / no contest"
    l = [x for x in f["fighters"] if not x["winner"]][0]
    how = f["method"]
    if f["round"] and "decision" not in how.lower():
        how += f" R{f['round']}" + (f" {f['clock']}" if f["clock"] and f["clock"] not in ("0:00", "5:00") else "")
    return f"✅ {w[0]['name']} def. {l['name']}" + (f" · {how.strip()}" if how.strip() else "")


def market(f: dict, day) -> str:
    """'Market: Moicano 62% · Nolan 38%' from the newest saved Pinnacle (or sharp) price, if we have one."""
    try:
        folder = C.DATA / "snapshots" / day.isoformat()
        files = sorted(folder.glob(f"{MMA}_*.json"))
        if not files:
            return ""
        events = json.loads(files[-1].read_text())
        names = {espn.fighter_key(x["name"]) for x in f["fighters"]}
        for ev in events:
            if {espn.fighter_key(ev["home_team"]), espn.fighter_key(ev["away_team"])} != names:
                continue
            for bk in sorted(ev.get("bookmakers", []), key=lambda b: b["key"] != "pinnacle"):
                if bk["key"] not in C.PRICING_BOOKS:
                    continue
                m = next((m for m in bk["markets"] if m["key"] == "h2h"), None)
                if m and len(m["outcomes"]) == 2:
                    p = devig([o["price"] for o in m["outcomes"]])
                    pairs = sorted(zip(p, m["outcomes"]), key=lambda x: -x[0])
                    return " · ".join(f"{_last(o['name'])} {pr:.0%}" for pr, o in pairs)
    except Exception as e:                       # noqa: BLE001
        print(f"[fightnight] market read failed: {e}")
    return ""


# ---------------------------------------------------------------- posting
def _tags(ev) -> str:
    return " ".join(t for t in ("#UFC", tags.event_tag(ev["name"]), tags.COMMUNITY) if t)


def _post(st, text, post, reply=True):
    if not post:
        print("[fightnight] (not posted)\n" + text)
        return None
    pid = social.post_x(text, reply_to=st.get("last") if reply else None)
    if pid:
        st.setdefault("main", pid)
        if reply or not st.get("last"):
            st["last"] = pid
    return pid


def _poll(ev, main, post) -> bool:
    a, b = (x["name"] for x in main["fighters"])
    q = f"🥊 Main event: {a} vs {b}. Who you got?"
    opts = [_last(a)[:25], _last(b)[:25]]
    if not post:
        print(f"[fightnight] poll: {q} {opts}")
        return True
    if C.TELEGRAM_BOT_TOKEN and C.TG_FREE:
        telegram._call("sendPoll", chat_id=C.TG_FREE, question=q[:300], options=[{"text": o} for o in opts])
    s = social._x_session()
    if not s:
        return True
    r = s.post("https://api.x.com/2/tweets", json={"text": social.tagged(q, _tags(ev)),
                                                   "poll": {"options": opts, "duration_minutes": POLL_MINUTES}},
               timeout=30)
    if r.status_code >= 300:
        print(f"[fightnight] poll failed {r.status_code}: {r.text[:200]}")
    return True


def _our_plays(rows, day) -> list[dict]:
    cid = f"{MMA}|{day.isoformat()}"
    return [r for r in rows if r.get("card") == cid]


def run(now: datetime, state: dict, feed, post: bool, rows: list[dict]):
    d = now.astimezone(TZ).date()
    for day in (d, d - timedelta(days=1)):
        for ev in card(feed.raw(MMA, day)):
            fights = ev["fights"]
            first, main = parse_time(fights[0]["start"]), fights[-1]
            main_start = parse_time(main["start"])
            if now < first - KICKOFF_BEFORE or now > main_start + timedelta(hours=8):
                continue
            st = state.setdefault("fightnight", {}).setdefault(ev["id"], {"done": []})
            plays = _our_plays(rows, day)

            if not st.get("kickoff"):
                st["kickoff"] = now.isoformat()
                a, b = (x["name"] for x in main["fighters"])
                mk = market(main, day)
                when = main_start.astimezone(TZ).strftime("%-I:%M %p ET")
                lines = [f"🥊 {ev['name']} is live tonight.", "",
                         f"Main event: {a} vs {b} (~{when})"]
                if mk:
                    lines.append(f"Fair odds: {mk}")
                lines.append("")
                if plays:
                    lines.append("Our plays tonight:")
                    lines += [f"• {r['label']} ({r['price_american']}) · {float(r['units']):g}u" for r in plays]
                else:
                    lines.append("We ran every line on the card. No edge, no bet tonight. We're watching with you.")
                lines += ["", "Every result in this thread as it happens 👇"]
                _post(st, social.tagged("\n".join(lines), _tags(ev)), post, reply=False)

            fresh = [f for f in fights if f["completed"] and f["id"] not in st["done"] and f is not main]
            if fresh:
                st["done"] += [f["id"] for f in fresh]
                head = "Results so far:" if len(fresh) > 2 else "Result:" if len(fresh) == 1 else "Results:"
                _post(st, "\n".join([head] + [line(f) for f in fresh]), post)

            if not st.get("poll") and main_start - POLL_BEFORE <= now < main_start and not main["completed"]:
                st["poll"] = _poll(ev, main, post)

            if main["completed"] and main["id"] not in st["done"]:
                st["done"].append(main["id"])
                text = "\n".join(["🏆 MAIN EVENT", "", line(main)])
                pid = _post(st, social.tagged(text, _tags(ev)), post, reply=False)
                if pid:
                    st["last"] = pid

            if not st.get("wrap") and all(f["completed"] for f in fights):
                st["wrap"] = True
                done = [f for f in fights if f["completed"]]
                fins = sum(1 for f in done if "decision" not in f["method"].lower() and f["method"])
                lines = [f"That's a wrap on {ev['name']}.", "",
                         f"{len(done)} fights, {fins} finish{'es' if fins != 1 else ''}."]
                graded = [r for r in plays if r.get("result")]
                if graded:
                    pr = sum(float(r["profit"]) for r in graded)
                    w = sum(r["result"] == "win" for r in graded)
                    l = sum(r["result"] == "loss" for r in graded)
                    lines.append(f"Our plays: {w}-{l}, {pr:+.2f}u ({'+' if pr >= 0 else '-'}${abs(pr) * C.UNIT_DOLLARS:,.0f})")
                else:
                    lines.append("No bet from us tonight. Sitting out when there's no edge is part of the job.")
                lines += ["", "Next card, every pick stamped before the first bell."]
                _post(st, "\n".join(lines), post)
