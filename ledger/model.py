"""The pick model.

1. Remove the bookmaker's margin ("vig") from sharp-book prices to get a fair win probability.
2. Compare every price at a bettable sportsbook against that fair probability -> expected value (EV).
3. Adjust for confidence: how many sharp sources agree, how many books offer the price,
   and which way the sharp line has moved since our earlier snapshots.
4. The single highest score is the Daily Play. The next best go to the members board.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from . import config as C


# ---------------------------------------------------------------- odds math
def american(dec: float) -> int:
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def fmt_american(dec: float) -> str:
    a = american(dec)
    return f"+{a}" if a > 0 else str(a)


def devig(prices: list[float]) -> list[float]:
    """Power-method de-vig: p_i = (1/price_i)^k with k chosen so the p_i sum to 1.
    Handles favorite/longshot bias better than simple normalising."""
    q = [1 / p for p in prices]
    if sum(q) <= 1:
        s = sum(q)
        return [x / s for x in q]
    lo, hi = 1.0, 20.0
    for _ in range(80):
        k = (lo + hi) / 2
        if sum(x ** k for x in q) > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    return [x ** k for x in q]


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def side_key(market: str, outcome: dict) -> tuple:
    if market == "h2h":
        return (outcome["name"], None)
    return (outcome["name"], float(outcome["point"]))


# ---------------------------------------------------------------- fair prices
def fair_probs(event: dict, now: datetime) -> dict:
    """{(market, side_key): (prob, n_sources, has_pinnacle)} from fresh sharp-book prices."""
    acc: dict = {}
    for bk in event.get("bookmakers", []):
        w = C.PRICING_BOOKS.get(bk["key"])
        if not w:
            continue
        for m in bk.get("markets", []):
            if m["key"] not in C.MARKETS or len(m["outcomes"]) != 2:
                continue
            if now - parse_time(m.get("last_update", bk["last_update"])) > timedelta(minutes=C.MAX_QUOTE_AGE_MIN):
                continue
            probs = devig([o["price"] for o in m["outcomes"]])
            for o, p in zip(m["outcomes"], probs):
                k = (m["key"], side_key(m["key"], o))
                a = acc.setdefault(k, [0.0, 0.0, 0, False])
                a[0] += w * p
                a[1] += w
                a[2] += 1
                a[3] = a[3] or bk["key"] == "pinnacle"
    return {k: (v[0] / v[1], v[2], v[3]) for k, v in acc.items()}


# ---------------------------------------------------------------- candidates
@dataclass
class Play:
    sport: str
    event_id: str
    commence_time: str
    home: str
    away: str
    market: str
    side: str
    point: float | None
    book: str
    price: float
    fair_prob: float
    ev: float
    n_sources: int
    has_pinnacle: bool
    books_with_edge: list = field(default_factory=list)
    movement: float = 0.0          # change in sharp fair prob for this side since first snapshot
    score: float = 0.0
    units: float = 1.0
    tier: str = "edge"
    sharp_point: float | None = None   # the sharp books' line, when it differs from the bet's line
    analysis: dict | None = None       # the analyst's research and verdict, when available
    sim: dict | None = None            # our game simulation's projection and hit rate, when available

    @property
    def label(self) -> str:
        if self.market == "h2h":
            return f"{self.side} ML"
        if self.market == "spreads":
            pt = f"+{self.point:g}" if self.point > 0 else f"{self.point:g}"
            return f"{self.side} {pt}"
        short = lambda t: t.split()[-1]
        return f"{short(self.away)}/{short(self.home)} {self.side} {self.point:g}"

    @property
    def play_to(self) -> str:
        """Worst price that still has zero-or-better expected value."""
        return fmt_american(1 / self.fair_prob)

    def to_dict(self):
        d = asdict(self)
        d.update(label=self.label, price_american=fmt_american(self.price), play_to=self.play_to)
        return d


# How spread (home margin) and total outcomes are spread out, in points. Used to price a book's line against a
# sharp line a little different from it (e.g. a total of 41.5 at DraftKings vs 42.5 at Pinnacle).
SPREAD_SD = {"americanfootball_nfl": (13.5, 10.0), "americanfootball_ncaaf": (16.0, 14.0),
             "basketball_nba": (12.0, 18.0), "basketball_ncaab": (11.0, 15.0)}   # (margin sd, total sd)
MAX_SHIFT = {"americanfootball_nfl": 3.0, "americanfootball_ncaaf": 3.5, "basketball_nba": 4.0,
             "basketball_ncaab": 4.0}
NFL_KEYS = {3: 0.09, 7: 0.06, 6: 0.03, 10: 0.03, 4: 0.03, 14: 0.03}   # share of NFL games decided by exactly this


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _phi_inv(p: float) -> float:
    lo, hi = -8.0, 8.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _phi(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def shift_prob(sport: str, market: str, side_is_over_or_fav: bool, p0: float, from_pt: float, to_pt: float) -> float | None:
    """Probability that a bet wins at line `to_pt`, given the sharp fair probability p0 at line `from_pt`.
    Totals: P(total > L) on a normal curve. Spreads: same on the margin, plus NFL key numbers (3, 7...)
    so moving across 3 is worth far more than moving across 5."""
    if sport not in SPREAD_SD or abs(to_pt - from_pt) > MAX_SHIFT[sport] or not 0.02 < p0 < 0.98:
        return None
    sd = SPREAD_SD[sport][1 if market == "totals" else 0]
    if market == "totals":
        # p0 = P(total > from) for Over, P(total < from) for Under. Solve for the mean, then re-evaluate.
        over0 = p0 if side_is_over_or_fav else 1 - p0
        mu = from_pt + sd * _phi_inv(over0)
        over1 = 1 - _phi((to_pt - mu) / sd)
        return over1 if side_is_over_or_fav else 1 - over1
    # spreads: the bet covers if margin + point > 0, i.e. margin > -point. More points = better for the bettor.
    z0 = _phi_inv(p0)
    p1 = _phi(z0 + (to_pt - from_pt) / sd)
    if sport == "americanfootball_nfl":
        lo, hi = sorted((abs(from_pt), abs(to_pt)))
        crossed = sum(w for k, w in NFL_KEYS.items() if lo < k < hi or (lo < k == hi and to_pt != from_pt))
        extra = crossed - abs(p1 - p0)
        if extra > 0:
            p1 += extra * (1 if to_pt > from_pt else -1) * 0.5
    return min(max(p1, 0.01), 0.99)


def candidates(sport: str, events: list[dict], now: datetime, history: dict | None = None,
               window: tuple | None = None) -> list[Play]:
    """history: {event_id: [older event snapshots]} for line-movement scoring.
    window: (earliest start, latest start). Default = today's slate from now."""
    lo, hi = window or (now + timedelta(minutes=C.EARLIEST_START_MIN), now + timedelta(hours=C.LATEST_START_HOURS))
    out = []
    for ev in events:
        start = parse_time(ev["commence_time"])
        if not (lo <= start <= hi):
            continue
        fair = fair_probs(ev, now)
        if not fair:
            continue
        best: dict = {}
        for bk in ev.get("bookmakers", []):
            if bk["key"] not in C.BET_BOOKS:
                continue
            for m in bk.get("markets", []):
                if m["key"] not in C.MARKETS:
                    continue
                if now - parse_time(m.get("last_update", bk["last_update"])) > timedelta(minutes=C.MAX_QUOTE_AGE_MIN):
                    continue
                for o in m["outcomes"]:
                    k = (m["key"], side_key(m["key"], o))
                    sharp_pt = None
                    if k not in fair:
                        conv = _convert(sport, m["key"], o, fair)
                        if not conv:
                            continue   # no sharp price close enough to this line
                        (p, n, pin), sharp_pt = conv
                    else:
                        p, n, pin = fair[k]
                    evv = p * o["price"] - 1
                    entry = best.setdefault(k, {"plays": []})
                    entry["plays"].append((evv, bk["key"], o["price"], p, n, pin, sharp_pt))
        for (mkt, (side, point)), entry in best.items():
            plays = sorted(entry["plays"], reverse=True)
            evv, book, price, p, n, pin, sharp_pt = plays[0]
            if not (C.MIN_DECIMAL <= price <= C.MAX_DECIMAL) or evv > C.MAX_EV:
                continue
            edge_books = [b for e, b, *_ in plays if e >= C.FALLBACK_MIN_EV]
            pl = Play(sport, ev["id"], ev["commence_time"], ev["home_team"], ev["away_team"],
                      mkt, side, point, book, price, p, evv, n, pin, edge_books)
            pl.sharp_point = sharp_pt
            out.append(pl)
    if history:
        for pl in out:
            pl.movement = movement(pl, history.get(pl.event_id, []))
    for pl in out:
        pl.score = score(pl)
    return out


def _convert(sport, market, outcome, fair):
    """Fair probability for a book's spread/total at a line the sharp books don't hang, converted from the
    nearest sharp line for the same side. Returns ((prob, n_sources, has_pinnacle), sharp_point) or None."""
    if market not in ("spreads", "totals") or "point" not in outcome:
        return None
    side, pt = outcome["name"], float(outcome["point"])
    near = [(abs(kp - pt), kp, v) for (mk, (sd, kp)), v in fair.items()
            if mk == market and sd == side and kp is not None]
    if not near:
        return None
    _, kp, (p0, n, pin) = min(near)
    p1 = shift_prob(sport, market, side != "Under", p0, kp, pt)
    return ((p1, n, pin), kp) if p1 is not None else None


def movement(pl: Play, older: list[dict]) -> float:
    """How far the sharp fair probability for this side has moved since our earliest snapshot.
    Positive = sharp money has been moving toward our side (the soft book is lagging)."""
    for snap in older:   # oldest first
        t = parse_time(snap.get("_snapshot_time", snap["commence_time"]))
        f = fair_probs(snap, t)
        k = (pl.market, (pl.side, pl.point))
        if k in f:
            return pl.fair_prob - f[k][0]
    return 0.0


def score(pl: Play) -> float:
    conf = 1.0
    conf *= 1.0 if pl.has_pinnacle else 0.8            # Pinnacle is the gold standard
    conf *= 1.0 if pl.n_sources >= 2 else 0.9          # more sharp books agreeing = more trust
    conf *= 1.0 if len(pl.books_with_edge) >= 2 else 0.85   # one-book outliers often get pulled
    conf *= 0.9 if pl.sharp_point is not None else 1.0         # priced from a nearby sharp line: a bit less sure
    m = max(-0.03, min(0.03, pl.movement))
    conf *= 1 + m * 5                                   # +/-15% for line moving with/against us
    return pl.ev * conf


def size(pl: Play, tier: str = "edge") -> float:
    """Units to bet. Bigger edge = bigger bet, capped at 2u. Leans (thin edges) are half size."""
    if tier == "lean":
        return 1.0 if pl.ev >= C.FALLBACK_MIN_EV else 0.5
    if C.UNIT_MODE != "tiered":
        return 1.0
    confirmed = pl.has_pinnacle and len(pl.books_with_edge) >= 2   # big edges are often stale lines
    if pl.ev >= 0.06 and confirmed:
        return 2.0
    return 1.5 if pl.ev >= 0.035 else 1.0


def choose_card(plays: list[Play], max_picks: int, min_picks: int = 0) -> list[Play]:
    """Card days: up to max_picks full edges, one per game. If that leaves fewer than min_picks, the card is
    filled with leans: the best-priced plays left on the board (within LEAN_FLOOR of fair), bet smaller."""
    ranked = sorted(plays, key=lambda p: p.score, reverse=True)
    seen, out = set(), []
    for p in ranked:
        if p.ev < C.MIN_EV or p.event_id in seen:
            continue
        seen.add(p.event_id)
        p.tier, p.units = "edge", size(p, "edge")
        out.append(p)
        if len(out) == max_picks:
            break
    for floor in (C.LEAN_FLOOR, -1.0):                 # second pass: never leave a card short
        if not C.ALWAYS_PICK or len(out) >= min_picks or (floor < C.LEAN_FLOOR and out):
            break                                       # below-fair fills only when the card would be empty
        for p in ranked:
            if p.event_id in seen or p.ev < floor:
                continue
            seen.add(p.event_id)
            p.tier, p.units = "lean", size(p, "lean")
            out.append(p)
            if len(out) == min_picks:
                break
    return sorted(out, key=lambda p: (p.commence_time, -p.score))


def choose(plays: list[Play]) -> tuple[Play | None, list[Play], str]:
    """Returns (daily play, members board, tier). One play per game on the board."""
    ranked = sorted(plays, key=lambda p: p.score, reverse=True)
    full = [p for p in ranked if p.ev >= C.MIN_EV]
    pool, tier = (full, "edge") if full else (
        ([p for p in ranked if p.ev >= C.LEAN_FLOOR], "lean") if C.ALWAYS_PICK else ([], "none"))
    if not pool and C.ALWAYS_PICK and ranked:          # never a day without a pick: best number on the board
        pool, tier = ranked, "lean"
    if not pool:
        return None, [], "none"
    seen, board = set(), []
    for p in pool:
        if p.event_id in seen:
            continue
        seen.add(p.event_id)
        p.units = size(p, tier)
        board.append(p)
    return board[0], board[1:C.BOARD_SIZE + 1], tier
