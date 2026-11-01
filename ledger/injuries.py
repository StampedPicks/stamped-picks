"""The injury desk: every substantial injury, posted as it happens.

Two jobs, both through Claude with live web search (ESPN's free feed has no injury data):

  * Injury report before kickoff (NFL inactives drop ~90 minutes before each slot; NBA status ~1 hour before
    tip): one post per kickoff slot with every starter or key player who is OUT, DOUBTFUL or a game-time call,
    plus who replaces him.
  * In-game alerts while games are live: any starter or key player who leaves a game hurt, with his status
    (questionable / doubtful / out) and who's in for him. One post per player per day; updates (e.g.
    "questionable" -> "out", or "returned") go as replies to the first alert.

Coverage is only for games we have an open play on (premium picks excepted, so they stay private). Searches are throttled to one sweep per sport every
INJURY_EVERY_MIN minutes while games are live, so the cost stays a few dollars per NFL Sunday.
"""
from __future__ import annotations
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import config as C, espn, research, social, tags
from .model import parse_time

TZ = ZoneInfo(C.DISPLAY_TZ)
SPORTS = {"americanfootball_nfl": "NFL", "basketball_nba": "NBA", "americanfootball_ncaaf": "college football",
          "icehockey_nhl": "NHL", "basketball_ncaab": "college basketball", "baseball_mlb": "MLB"}
EVERY_MIN = int(getattr(C, "INJURY_EVERY_MIN", 15))
PREGAME_REPORT = False          # no lists of every player out; in-game alerts only
REPORT_LEAD_MIN = (60, 100)     # pre-game injury report: 60-100 minutes before the slot's first kickoff


def _games(feed, sport, now):
    d = now.astimezone(TZ).date()
    out = []
    for day in (d - timedelta(days=1), d):
        out += espn.games(feed.raw(sport, day))
    return out


def _ours(rows, sport):
    return [r for r in rows if r["sport"] == sport and not r.get("result") and r.get("tier") != "premium"]


def _is_ours(g, rows) -> bool:
    """Only games we have an open play on get injury coverage."""
    for r in rows:
        h, a = espn.norm(r["home"]), espn.norm(r["away"])
        if (h in g["home"]["names"] or (h.split() or [""])[-1] == g["home"]["nick"]) and \
                (a in g["away"]["names"] or (a.split() or [""])[-1] == g["away"]["nick"]):
            return True
    return False


def _touch(alert, rows) -> list[str]:
    team = espn.norm(alert.get("team", ""))
    hit = []
    for r in rows:
        if team and (team in espn.norm(r["home"]) or team in espn.norm(r["away"])
                     or espn.norm(r["home"]).split()[-1] in team or espn.norm(r["away"]).split()[-1] in team):
            hit.append(r["label"])
    return hit


def _ask(prompt, searches=4) -> list[dict]:
    try:
        text, _ = research._call(prompt, searches, max_tokens=3000)
        data = research._json(text)
    except Exception as e:                        # noqa: BLE001 - the injury desk never blocks anything else
        print(f"[injuries] search failed: {e}")
        return []
    return [a for a in data.get("injuries", []) if isinstance(a, dict) and a.get("player")]


def live_sweep(sport, games) -> list[dict]:
    matchups = "; ".join(f"{g['away']['display']} at {g['home']['display']} ({g['detail']})" for g in games)
    return _ask(f"""These {SPORTS[sport]} games are in progress right now: {matchups}.
Search for SIGNIFICANT in-game injuries in these games from the last 2 hours: only a player whose loss changes
the game for bettors, like the starting QB or a team's star (top RB/WR/TE, best pass rusher or cornerback, a
star NBA/NHL player). Include him if he left the game hurt, was ruled questionable/doubtful/out to return, or
came back in. Leave out backups, role players, routine checks and anyone who was already out before the game.
Only what a beat reporter or the team has confirmed. Most sweeps should return an empty list.
Reply with only JSON: {{"injuries": [{{"player": "...", "team": "full team name", "position": "QB",
"injury": "thumb", "status": "questionable to return" or "out" or "returned" etc, "replacement": "who's in, or empty",
"detail": "one short line of context, at most 120 characters"}}]}} (empty list if nothing).""")


def report_sweep(sport, games) -> list[dict]:
    matchups = "; ".join(f"{g['away']['display']} at {g['home']['display']}" for g in games)
    return _ask(f"""These {SPORTS[sport]} games kick off soon: {matchups}.
Search the official inactives / injury reports released today and this week's final injury report. List every
STARTER or KEY player who is OUT, DOUBTFUL, a game-time decision, or a surprise inactive, plus notable players
returning from injury. Only confirmed reports.
Reply with only JSON: {{"injuries": [{{"player": "...", "team": "full team name", "position": "QB",
"injury": "ankle", "status": "out" or "doubtful" or "game-time decision" or "active (returning)",
"replacement": "who starts instead, or empty", "detail": "at most 120 characters"}}]}} (empty list if nothing).""",
                searches=6)


def _line(a) -> str:
    rep = f" {a['replacement']} in." if a.get("replacement") else ""
    pos = f" ({a['position']})" if a.get("position") else ""
    inj = f" {a['injury']}," if a.get("injury") else ""
    return f"{a['player']}{pos}, {a.get('team', '')}:{inj} {a.get('status', '')}.{rep}".replace(" ,", ",")


def alert_post(sport, a, ours) -> str:
    st = str(a.get("status", "")).lower()
    head = "✅ UPDATE" if ("returned" in st or st.startswith("back in") or st.startswith("active")) else "🚨 INJURY"
    lines = [f"{head}: {_line(a)}"]
    if a.get("detail"):
        lines += ["", research._clean(a["detail"], 125)]
    if ours:
        lines += ["", "Our play in this game: " + ", ".join(ours[:2])]
    lines += ["", tags.SPORT.get(sport, "") + " #InjuryReport #GamblingTwitter"]
    return "\n".join(lines)


def report_post(sport, items, ours_by) -> str:
    lines = [f"🏥 {SPORTS[sport].upper()} INJURY REPORT · before {social.start_word(sport)}", ""]
    for a in items:
        mark = " ⚠️ our game" if ours_by.get(a["player"]) else ""
        lines.append(f"• {_line(a)}{mark}")
    lines += ["", tags.SPORT.get(sport, "") + " #InjuryReport #GamblingTwitter"]
    return "\n".join(lines)


def run(now, state, feed, post, rows):
    if not C.ANTHROPIC_API_KEY:
        return
    desk = state.setdefault("injuries", {})
    day = now.astimezone(TZ).date().isoformat()
    seen = desk.setdefault(day, {})                       # player -> {"status", "post"}
    for k in [k for k in desk if k < (now.astimezone(TZ).date() - timedelta(days=3)).isoformat()]:
        desk.pop(k, None)
    for sport in SPORTS:
        try:
            games = _games(feed, sport, now)
        except Exception as e:                            # noqa: BLE001
            print(f"[injuries] {sport}: {e}")
            continue
        ours = _ours(rows, sport)
        if not ours:
            continue
        games = [g for g in games if _is_ours(g, ours)]
        live = [g for g in games if g["state"] == "in"]
        last = desk.setdefault("_last", {}).get(sport)
        if live and (not last or now - parse_time(last) >= timedelta(minutes=EVERY_MIN)):
            desk["_last"][sport] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            for a in live_sweep(sport, live)[:3]:
                key = espn.norm(a["player"])
                status = espn.norm(str(a.get("status", "")))
                prev = seen.get(key)
                if prev and prev["status"] == status:
                    continue
                text = alert_post(sport, a, _touch(a, ours))
                social.save_post("injury", day, text)
                print(f"[injuries] {a['player']}: {a.get('status')}")
                pid = social.post_x(text, reply_to=prev.get("post") if prev else None) if post else None
                seen[key] = {"status": status, "post": pid or (prev or {}).get("post")}
        # pre-game report: off. Only significant in-game injuries get posted.
        pre = [g for g in games if PREGAME_REPORT and g["state"] == "pre" and g.get("start")]
        slots = {}
        for g in pre:
            mins = (parse_time(g["start"]) - now).total_seconds() / 60
            if REPORT_LEAD_MIN[0] <= mins <= REPORT_LEAD_MIN[1]:
                slots.setdefault(parse_time(g["start"]).astimezone(TZ).strftime("%H"), []).append(g)
        for hour, gs in slots.items():
            key = f"_report:{sport}:{day}:{hour}"
            if key in seen:
                continue
            seen[key] = True
            items = [a for a in report_sweep(sport, gs) if espn.norm(a["player"]) not in seen][:15]
            if not items:
                continue
            for a in items:
                seen[espn.norm(a["player"])] = {"status": espn.norm(str(a.get("status", ""))), "post": None}
            text = report_post(sport, items, {a["player"]: _touch(a, ours) for a in items})
            social.save_post("injury_report", day, text)
            print(f"[injuries] {sport} report for {hour}:00 ET slot: {len(items)} players")
            if post:
                social.post_x(text)
