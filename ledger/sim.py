"""Game simulations, RotoBet-style: project each team's score from this season's results, play the game out
10,000 times, and average the results into a projected score plus a win / cover / over chance at any line.

  1. Every completed game this season from ESPN's free scoreboard (finished weeks cached on disk).
  2. Team ratings by weighted least squares, recent games counting more (half-life in days):
        points scored = league average + home edge (if home) + offense rating + opponent's defense rating
     (defense rating > 0 = allows more than average), shrunk toward average so two games can't swing it.
  3. 10,000 simulated games: each side's points drawn around its projection with football's scoring spread,
     slightly correlated (shootouts and slogs), overtime breaking ties.
  4. Averages = the projected score; counting outcomes = the chance any bet wins (pushes excluded).

The sim is one signal and the sharp market is another (usually stronger: it already prices injuries and
news). Candidates get a blended fair chance (SIM_WEIGHT of sim, the rest market), and their score goes up
when the two agree and down when they don't. The analyst then checks what neither can see.
Player props: ledger/props.py runs the same 10,000 games and draws each player's stat line inside them.
"""
from __future__ import annotations
import math
import random
from datetime import datetime, timedelta, timezone

import requests

from . import config as C, espn

RUNS = 10000
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
# sport: (ESPN path, scoreboard group, home edge pts, per-team points sd, score correlation, ridge, half-life days, ties possible)
CFG = {
    "americanfootball_nfl": ("football/nfl", None, 1.6, 9.8, 0.10, 1.5, 35, False),
    "americanfootball_ncaaf": ("football/college-football", "80", 2.8, 12.0, 0.10, 1.5, 28, False),
    "basketball_nba": ("basketball/nba", None, 2.2, 11.5, 0.30, 3.0, 30, False),
}
_CACHE: dict = {}                    # (sport, date) -> ratings, so a run fits each sport once


# ---------------------------------------------------------------- data
def fetch_games(sport: str, now: datetime, days: int = 120) -> list[dict]:
    """Completed games in the last `days`, from ESPN's daily scoreboards. Finished days are cached on disk."""
    import json
    path, group = CFG[sport][0], CFG[sport][1]
    out = []
    folder = C.DATA / "sim" / sport
    for back in range(1, days + 1):
        d = (now - timedelta(days=back)).date()
        f = folder / f"{d.isoformat()}.json"
        if f.exists():
            out += [dict(g, date=datetime.fromisoformat(g["date"])) for g in json.loads(f.read_text())]
            continue
        params = {"dates": d.strftime("%Y%m%d"), "limit": 500}
        if group:
            params["groups"] = group
        try:
            r = requests.get(SCOREBOARD.format(path=path), params=params, timeout=20)
            r.raise_for_status()
            gl = espn.games(r.json())
        except Exception as e:                    # noqa: BLE001
            print(f"[sim] {sport} {d}: {e}")
            continue
        day = []
        for g in gl:
            if g["completed"]:
                day.append({"home": g["home"]["display"], "away": g["away"]["display"],
                            "hs": g["home"]["score"], "as": g["away"]["score"], "date": f"{d}T12:00:00+00:00",
                            "home_names": sorted(g["home"]["names"] | {g["home"]["nick"]}),
                            "away_names": sorted(g["away"]["names"] | {g["away"]["nick"]})})
        folder.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(day))
        out += [dict(g, date=datetime.fromisoformat(g["date"])) for g in day]
    return out


# ---------------------------------------------------------------- ratings
def ratings(sport: str, games: list[dict], now: datetime) -> dict:
    hfa, ridge, half = CFG[sport][2], CFG[sport][5], CFG[sport][6]
    rows, names = [], {}
    for g in games:
        age = max(0.0, (now - g["date"]).total_seconds() / 86400)
        wt = 0.5 ** (age / half)
        rows.append((wt, g["home"], g["away"], 1, g["hs"]))
        rows.append((wt, g["away"], g["home"], 0, g["as"]))
        names.setdefault(g["home"], set()).update(espn.norm(x) for x in g.get("home_names", []) or [g["home"]])
        names.setdefault(g["away"], set()).update(espn.norm(x) for x in g.get("away_names", []) or [g["away"]])
    if not rows:
        return {}
    mu = sum(r[0] * r[4] for r in rows) / sum(r[0] for r in rows) - hfa / 2
    off = {t: 0.0 for t in names}
    dfn = {t: 0.0 for t in names}
    by_off, by_def = {t: [] for t in names}, {t: [] for t in names}
    for r in rows:
        by_off[r[1]].append(r)
        by_def[r[2]].append(r)
    for _ in range(40):
        for t, rs in by_off.items():               # offense: scores more than expected
            off[t] = sum(w * (pts - mu - hfa * h - dfn[d]) for w, o, d, h, pts in rs) / (sum(r[0] for r in rs) + ridge)
        for t, rs in by_def.items():               # defense: allows more than expected (+ = worse)
            dfn[t] = sum(w * (pts - mu - hfa * h - off[o]) for w, o, d, h, pts in rs) / (sum(r[0] for r in rs) + ridge)
    return {"sport": sport, "mu": mu, "hfa": hfa, "off": off, "def": dfn, "names": names, "n_games": len(games)}


def team(r: dict, name: str) -> str | None:
    if name in r.get("off", {}):
        return name
    n = espn.norm(name)
    nick = n.split()[-1] if n else ""
    for t, ns in r.get("names", {}).items():
        if n in ns or espn.norm(t) == n:
            return t
    for t, ns in r.get("names", {}).items():          # "Miami Dolphins" vs "Dolphins"
        if nick and any(x == nick or x.endswith(" " + nick) for x in ns):
            return t
    return None


# ---------------------------------------------------------------- simulation
def simulate(sport: str, r: dict, home: str, away: str, runs: int = RUNS, seed: int | None = None) -> dict | None:
    h, a = team(r, home), team(r, away)
    if not h or not a:
        return None
    _, _, hfa, sd, rho, *_ = CFG[sport]
    hp = max(3.0, r["mu"] + hfa + r["off"][h] + r["def"][a])
    ap = max(3.0, r["mu"] + r["off"][a] + r["def"][h])
    rnd = random.Random(seed if seed is not None else hash((home, away)) & 0xffffffff)
    k = math.sqrt(1 - rho * rho)
    scores = []
    for _ in range(runs):
        z1, z2 = rnd.gauss(0, 1), rnd.gauss(0, 1)
        hs = max(0, round(hp + sd * z1))
        as_ = max(0, round(ap + sd * (rho * z1 + k * z2)))
        if hs == as_:                                # overtime: slight edge to the better projected side
            if rnd.random() < 0.5 + (hp - ap) / 40:
                hs += 3 if sport.startswith("americanfootball") else 4
            else:
                as_ += 3 if sport.startswith("americanfootball") else 4
        scores.append((hs, as_))
    n = len(scores)
    return {"home": home, "away": away, "home_proj": sum(x for x, _ in scores) / n,
            "away_proj": sum(y for _, y in scores) / n, "scores": scores, "runs": n}


def prob(s: dict, market: str, side: str, point: float | None) -> float:
    """Chance the bet wins across all simulated games. Pushes refund, so they count for neither side."""
    win = lose = 0
    home_side = side == s["home"] or (side not in ("Over", "Under") and espn.norm(side) == espn.norm(s["home"]))
    for h, a in s["scores"]:
        if market == "totals":
            v = (h + a - point) if side == "Over" else (point - h - a)
        else:
            margin = (h - a) if home_side else (a - h)
            v = margin + (point or 0) if market == "spreads" else margin
        if v > 0:
            win += 1
        elif v < 0:
            lose += 1
    return win / max(1, win + lose)


# ---------------------------------------------------------------- applying it to candidates
def _ratings_for(sport: str, now: datetime) -> dict:
    key = (sport, now.date())
    if key not in _CACHE:
        games = fetch_games(sport, now)
        _CACHE[key] = ratings(sport, games, now) if len(games) >= 20 else {}
    return _CACHE[key]


def apply(sport: str, cands: list, now: datetime) -> list:
    """Adds p.sim to each candidate and blends the sim into its fair chance, EV and score."""
    if sport not in CFG or not C.SIM_WEIGHT or not cands:
        return cands
    try:
        r = _ratings_for(sport, now)
    except Exception as e:                          # noqa: BLE001 - the sim never blocks a pick
        print(f"[sim] {sport} ratings failed: {e}")
        return cands
    if not r:
        return cands
    sims = {}
    for p in cands:
        if p.event_id not in sims:
            sims[p.event_id] = simulate(sport, r, p.home, p.away)
        s = sims[p.event_id]
        if not s:
            continue
        sp = prob(s, p.market, p.side, p.point)
        market_p = p.fair_prob
        sim_ev = sp * p.price - 1
        agrees = None if abs(sim_ev) < 0.01 else sim_ev > 0          # does the sim back this bet?
        p.sim = {"home_proj": round(s["home_proj"], 1), "away_proj": round(s["away_proj"], 1), "prob": sp,
                 "runs": s["runs"], "market_prob": market_p, "sim_ev": sim_ev, "agrees": agrees}
        p.fair_prob = (1 - C.SIM_WEIGHT) * market_p + C.SIM_WEIGHT * sp
        old_ev, p.ev = p.ev, p.fair_prob * p.price - 1
        p.score = p.score * (p.ev / old_ev) if old_ev > 0 and p.ev > 0 else p.score + (p.ev - old_ev)
        p.score *= 1.2 if agrees else 0.75 if agrees is False else 1.0
    return cands


def line(p) -> list[str]:
    """The sim line for the 'numbers' post: projected score and how often this bet wins."""
    s = getattr(p, "sim", None)
    if not s:
        return []
    nick = lambda t: t.split()[-1]
    what = {"h2h": f"{nick(p.side)} win", "spreads": f"{p.label} covers",
            "totals": f"the {p.side} {p.point:g} hits"}.get(p.market, p.label)
    tail = {True: " The sim backs it.", False: " The sim is lower on it than the market, so it's a smaller bet.",
            None: ""}[s.get("agrees")]
    return [f"• Our sim ({s['runs']:,} games): {nick(p.away)} {s['away_proj']:.1f}, {nick(p.home)} "
            f"{s['home_proj']:.1f}. {what[0].upper() + what[1:]} in {s['prob']:.0%} of them.{tail}"]


def prompt_line(p) -> str:
    """The sim result as a sentence for the analyst's brief."""
    s = getattr(p, "sim", None)
    if not s:
        return ""
    return (f" Our {s['runs']:,}-game simulation projects {p.away} {s['away_proj']:.1f}, {p.home} {s['home_proj']:.1f} "
            f"and has this bet winning {s['prob']:.0%} ({'the sim backs it' if s.get('agrees') else 'the sim is against it' if s.get('agrees') is False else 'the sim is neutral'}).")
