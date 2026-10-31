"""Offline end-to-end test. A fake Odds API generates games, sharp and soft prices, closing lines and
final scores from a hidden "true" probability, then the real engine code runs day by day against it.
Output goes to demo_out/ and is stamped SIMULATED. It never touches data/ or docs/."""
import math
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

TEAMS = {
    "americanfootball_nfl": ["Bills", "Dolphins", "Jets", "Patriots", "Ravens", "Bengals", "Browns", "Steelers",
                             "Texans", "Colts", "Jaguars", "Titans", "Broncos", "Chiefs", "Raiders", "Chargers",
                             "Cowboys", "Giants", "Eagles", "Commanders", "Bears", "Lions", "Packers", "Vikings"],
    "americanfootball_ncaaf": ["Georgia", "Alabama", "Ohio State", "Michigan", "Texas", "Oregon", "Penn State",
                               "LSU", "Clemson", "Notre Dame", "USC", "Utah", "Tennessee", "Ole Miss",
                               "Florida State", "Miami", "Oklahoma", "Missouri", "Iowa", "Wisconsin"],
    "basketball_nba": ["Celtics", "Knicks", "76ers", "Bucks", "Cavaliers", "Heat", "Magic", "Pacers", "Nuggets",
                       "Thunder", "Timberwolves", "Mavericks", "Suns", "Lakers", "Warriors", "Kings"],
    "baseball_mlb": ["Yankees", "Orioles", "Red Sox", "Rays", "Guardians", "Tigers", "Astros", "Mariners",
                     "Dodgers", "Padres", "Braves", "Phillies", "Mets", "Brewers", "Cubs", "Cardinals"],
    "mma_mixed_martial_arts": ["Jon Jones", "Alex Pereira", "Islam Makhachev", "Ilia Topuria", "Sean O'Malley",
                               "Merab Dvalishvili", "Dricus du Plessis", "Belal Muhammad", "Tom Aspinall",
                               "Max Holloway", "Justin Gaethje", "Charles Oliveira"],
    "icehockey_nhl": ["Bruins", "Panthers", "Rangers", "Hurricanes", "Maple Leafs", "Lightning", "Stars",
                      "Avalanche", "Jets", "Oilers", "Golden Knights", "Kings", "Canucks", "Devils"],
}
# home edge mean/sd, margin sd, total mean, total sd, fixed run/puck line or None
PARAMS = {
    "americanfootball_nfl": (1.5, 6, 13.5, 44, 10, None),
    "americanfootball_ncaaf": (3, 12, 16, 54, 13, None),
    "basketball_nba": (2, 7, 12, 226, 18, None),
    "baseball_mlb": (0.15, 0.8, 4.2, 8.5, 4.2, 1.5),
    "icehockey_nhl": (0.2, 0.6, 2.4, 6.0, 2.3, 1.5),
    "mma_mixed_martial_arts": (0.0, 1.0, 1.6, 2.5, 1.0, None),
}
BOOK_MARGIN = {"pinnacle": .025, "lowvig": .02, "betonlineag": .035}
SOFT = ["draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers", "fanatics"]

REG = {"americanfootball_nfl": 4, "americanfootball_ncaaf": 4, "basketball_nba": 4, "basketball_ncaab": 2,
       "baseball_mlb": 9, "icehockey_nhl": 3, "mma_mixed_martial_arts": 1}
DUR = {"americanfootball_nfl": 3.25, "americanfootball_ncaaf": 3.5, "basketball_nba": 2.4, "basketball_ncaab": 2.0,
       "baseball_mlb": 3.0, "icehockey_nhl": 2.5, "mma_mixed_martial_arts": 0.4}
Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))
half = lambda x: round(x * 2) / 2
iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")


def slate(sport, day):
    """(n games, list of UTC hours) for a sport on a date."""
    wd = day.weekday()
    if sport == "americanfootball_nfl":
        return {6: (8, [17, 17, 17, 17, 20.4, 20.4, 20.4, 24.3]), 0: (1, [24.25]), 3: (1, [24.25])}.get(wd, (0, []))
    if sport == "americanfootball_ncaaf":
        return (10, [16, 16, 19.5, 19.5, 19.5, 23.5, 23.5, 23.5, 26.5, 26.5]) if wd == 5 else (0, [])
    if sport == "mma_mixed_martial_arts":
        return (5, [27, 27.5, 28, 28.5, 29]) if wd == 5 else (0, [])
    if sport == "basketball_nba":
        return (6, [23, 23.5, 24, 24.5, 25, 26.5]) if day >= datetime(2026, 10, 20).date() else (0, [])
    if sport == "icehockey_nhl":
        return (6, [23, 23, 23.5, 24, 25, 26]) if day >= datetime(2026, 10, 7).date() else (0, [])
    if sport == "baseball_mlb":
        if day <= datetime(2026, 9, 27).date():
            return (8, [17, 20, 23, 23, 23.2, 24, 25.7, 26.1])
        return (2, [21, 24.1]) if day <= datetime(2026, 10, 28).date() else (0, [])
    return (0, [])


class World:
    def __init__(self, start, days, seed=7):
        self.rng = random.Random(seed)
        self.games = {}
        for d in range(days + 1):
            day = (start + timedelta(days=d)).date()
            for sport in PARAMS:
                n, hours = slate(sport, day)
                teams = self.rng.sample(TEAMS[sport], min(len(TEAMS[sport]), 2 * n))
                for i in range(n):
                    self._make(sport, day, hours[i], teams[2 * i + 1], teams[2 * i])

    def _make(self, sport, day, hour, home, away):
        r = self.rng
        adv, sd_mu, sd, tot, sd_t, fixed = PARAMS[sport]
        mu = r.gauss(adv, sd_mu)
        T = tot + r.gauss(0, sd_t * 0.3)
        t0 = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(hours=hour)
        hp = -fixed if (fixed and mu > 0) else (fixed if fixed else -half(mu + r.gauss(0, sd * .05)))
        tl = half(T + r.gauss(0, sd_t * .08)) + (0.5 if half(T) == round(T) and sport in ("baseball_mlb", "icehockey_nhl") else 0)
        true = {
            "h2h": Phi(mu / sd),
            "spreads": Phi((mu + hp) / sd),
            "totals": Phi((T - tl) / sd_t),
        }
        gid = f"{sport[:4]}{day:%m%d}{len(self.games):04d}"
        g = dict(id=gid, sport=sport, t=t0, home=home, away=away, mu=mu, T=T, sd=sd, sd_t=sd_t,
                 hp=hp, tl=tl, true=true)
        g["open"] = {m: {bk: true[m] + r.gauss(0, .007 if bk == "pinnacle" else .010 if bk in BOOK_MARGIN else .018)
                         for bk in list(BOOK_MARGIN) + SOFT} for m in true}
        fm = r.gauss(mu, sd)
        ft = max(1.0, r.gauss(T, sd_t))
        h, a = max(0, round((ft + fm) / 2)), max(0, round((ft - fm) / 2))
        if h == a and sport != "americanfootball_nfl":
            h, a = (h + 1, a) if fm >= 0 else (h, a + 1)
        if sport == "mma_mixed_martial_arts":
            h, a = (1, 0) if fm > 0 else (0, 1)
        g["final"] = (h, a)
        P = REG[sport]
        g["lines"] = (self._split(h, P), self._split(a, P)) if sport != "mma_mixed_martial_arts" else None
        g["method"] = r.choice(["KO/TKO", "Submission", "Decision - Unanimous", "Decision - Split"])
        self.games[gid] = g

    def _split(self, total, periods):
        w = [self.rng.random() + 0.2 for _ in range(periods)]
        raw = [total * x / sum(w) for x in w]
        out = [int(x) for x in raw]
        for i in sorted(range(periods), key=lambda i: raw[i] - out[i], reverse=True)[: total - sum(out)]:
            out[i] += 1
        return out

    def outcomes(self, g, m, p, margin, bk):
        p = min(.97, max(.03, p))
        q = [p * (1 + margin), (1 - p) * (1 + margin)]
        pr = [round(1 / x, 2) for x in q]
        if m == "h2h":
            return [{"name": g["home"], "price": pr[0]}, {"name": g["away"], "price": pr[1]}]
        if m == "spreads":
            return [{"name": g["home"], "price": pr[0], "point": g["hp"]},
                    {"name": g["away"], "price": pr[1], "point": -g["hp"]}]
        return [{"name": "Over", "price": pr[0], "point": g["tl"]}, {"name": "Under", "price": pr[1], "point": g["tl"]}]

    def event_json(self, g, now, markets, books, closing=False):
        bks = []
        for bk in books:
            mk = []
            for m in markets:
                if closing:
                    sharp = g["true"][m] + self.rng.gauss(0, .006)
                    p = sharp if bk in BOOK_MARGIN else sharp + self.rng.gauss(0, .012)
                else:
                    hrs = (g["t"] - now).total_seconds() / 3600
                    w = min(1.0, max(0.0, 1 - hrs / 30))
                    p = g["open"][m][bk] + (g["true"][m] - g["open"][m][bk]) * w * 0.7
                margin = BOOK_MARGIN.get(bk, .045)
                lu = iso(now - timedelta(minutes=self.rng.randint(1, 25)))
                mk.append({"key": m, "last_update": lu, "outcomes": self.outcomes(g, m, p, margin, bk)})
            bks.append({"key": bk, "title": bk, "last_update": iso(now), "markets": mk})
        return {"id": g["id"], "sport_key": g["sport"], "commence_time": iso(g["t"]),
                "home_team": g["home"], "away_team": g["away"], "bookmakers": bks}


class FakeAPI:
    def __init__(self, world):
        self.w, self.spent, self.remaining, self.now = world, 0, 500, None

    def sports(self):
        return [{"key": k} for k in PARAMS]

    def _in(self, sport, a, b):
        return [g for g in self.w.games.values() if g["sport"] == sport and a <= g["t"] <= b]

    def events(self, sport, start, end):
        return [{"id": g["id"]} for g in self._in(sport, start, end)]

    def odds(self, sport, markets, books, start, end):
        gs = self._in(sport, start, end)
        if gs:
            self.spent += len(markets) * math.ceil(len(books) / 10)
        return [self.w.event_json(g, self.now, markets, books) for g in gs]

    def event_odds(self, sport, eid, markets, books):
        self.spent += len(markets)
        return self.w.event_json(self.w.games[eid], self.now, markets, books, closing=True)

    def scores(self, sport, days_from=2):
        if sport == "mma_mixed_martial_arts":
            raise RuntimeError("scores not available for this sport")
        self.spent += 2
        out = []
        for g in self.w.games.values():
            if g["sport"] == sport and self.now - timedelta(days=days_from) <= g["t"] < self.now:
                done = g["t"] + timedelta(hours=3.5) < self.now
                out.append({"id": g["id"], "completed": done, "home_team": g["home"], "away_team": g["away"],
                            "scores": [{"name": g["home"], "score": str(g["final"][0])},
                                       {"name": g["away"], "score": str(g["final"][1])}] if done else None})
        return out


class FakeESPN:
    """Plays games out over time in ESPN's scoreboard shape: quarters, halftime, periods, innings,
    and UFC fights finishing one by one."""

    def __init__(self, world, now):
        self.w, self.now = world, now

    def _status(self, g):
        el = (self.now - g["t"]).total_seconds() / 3600
        dur, P = DUR[g["sport"]], REG[g["sport"]]
        if el < 0:
            return "pre", False, 0, "Scheduled", 0.0
        if el >= dur:
            return "post", True, P, "Final", 1.0
        f = el / dur
        if P == 4 and g["sport"] != "mma_mixed_martial_arts":       # quarters with a halftime break
            segs = [(0, .22, 1), (.22, .44, 2), (.44, .56, "H"), (.56, .78, 3), (.78, 1.0, 4)]
            for a, b, q in segs:
                if a <= f < b:
                    if q == "H":
                        return "in", False, 2, "Halftime", 0.5
                    done = (q - 1) / 4 + (f - a) / (b - a) / 4
                    return "in", False, q, f"Q{q}", done
        period = min(P, int(f * P) + 1)
        return "in", False, period, f"P{period}", f

    def _lines(self, lines, period, done, P):
        full = [float(x) for x in lines[: period - 1]] if done < 1 else [float(x) for x in lines]
        if done < 1 and period >= 1:
            part = done * P - (period - 1)
            full.append(float(round(lines[period - 1] * max(0.0, min(1.0, part)))))
        return full

    def raw(self, sport, day):
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        gs = [g for g in self.w.games.values() if g["sport"] == sport
              and (g["t"].astimezone(et) - timedelta(hours=6)).date() == day]
        if sport == "mma_mixed_martial_arts":
            comps = []
            for g in gs:
                state, done, _, detail, _ = self._status(g)
                h_win = g["final"][0] > g["final"][1]
                st = {"type": {"state": state, "completed": done, "shortDetail": detail}}
                if done:
                    st["result"] = {"displayName": g["method"]}
                comps.append({"id": g["id"], "date": iso(g["t"]), "status": st, "competitors": [
                    {"athlete": {"displayName": g["home"]}, "winner": done and h_win},
                    {"athlete": {"displayName": g["away"]}, "winner": done and not h_win}]})
            name = f"UFC {300 + day.toordinal() % 50}: Main Event" if day.day % 2 else "UFC Fight Night: Main Event"
            return {"events": [{"id": f"card{day}", "name": name, "date": comps[0]["date"],
                                "competitions": comps}]} if comps else {"events": []}
        events = []
        for g in gs:
            state, done, period, detail, frac = self._status(g)
            P = REG[sport]
            sides = []
            for side, name, lines in (("home", g["home"], g["lines"][0]), ("away", g["away"], g["lines"][1])):
                ls = self._lines(lines, period, frac, P) if state != "pre" else []
                sides.append({"homeAway": side, "score": str(int(sum(ls))),
                              "team": {"displayName": name, "name": name.split()[-1], "abbreviation": name[:3].upper()},
                              "linescores": [{"value": v} for v in ls]})
            events.append({"id": g["id"], "date": iso(g["t"]), "competitions": [{
                "date": iso(g["t"]), "competitors": sides,
                "status": {"period": period, "type": {"state": state, "completed": done, "shortDetail": detail}}}]})
        return {"events": events}


def redirect(root):
    from ledger import config as C, store
    C.DATA, C.DOCS = root / "data", root / "docs"
    store.LEDGER, store.BOARD_LEDGER = C.DATA / "ledger.csv", C.DATA / "board_ledger.csv"
    store.SEALS, store.STATE, store.SNAPS = C.DATA / "board_seals.csv", C.DATA / "state.json", C.DATA / "snapshots"


def simulate(days, start, root, seed=7, before_tick=None):
    """Runs the real `tick` every 15 minutes across `days` of simulated games."""
    from ledger import config as C, seal
    import run as engine
    shutil.rmtree(root, ignore_errors=True)
    redirect(root)
    C.BOARD_KEY, C.ODDS_PLAN = seal.new_key(), "free"
    world = World(start - timedelta(days=1), days + 2, seed=seed)
    api = FakeAPI(world)
    t, end = start, start + timedelta(days=days)
    while t < end:
        api.now = t
        if before_tick:
            before_tick(t)
        engine.tick(api, t, post=True, feed=FakeESPN(world, t))
        t += timedelta(minutes=15)
    return world, api


def run_demo(days=45, out="demo_out"):
    from ledger import store, render, seal
    root = Path(out).resolve()
    start = datetime(2026, 9, 14, 4, 0, tzinfo=timezone.utc)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, api = simulate(days, start, root)
    page = render.render(demo=True)
    rows = store.read(store.LEDGER)
    st = store.stats(rows)
    by = {}
    for r in rows:
        k = r["card"].split("|")[0] if r.get("card") else "daily"
        by.setdefault(k, []).append(r)
    print(f"[demo] {days} days, {len(rows)} plays | record {st['wins']}-{st['losses']}-{st['pushes']} | "
          f"{st['profit']:+.2f}u | avg CLV {st['avg_clv']:+.2%} | beat close {st['beat_close']:.0%}")
    for k, rs in sorted(by.items()):
        s2 = store.stats(rs)
        print(f"[demo]   {k}: {len(rs)} plays, {s2['wins']}-{s2['losses']}-{s2['pushes']}, {s2['profit']:+.2f}u")
    print(f"[demo] odds credits used: {api.spent} over {days} days (~{api.spent / days * 30:.0f}/month)")
    print(f"[demo] page -> {page}")
