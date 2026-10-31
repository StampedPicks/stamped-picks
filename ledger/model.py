"""The pick model.

1. Remove the bookmaker's margin ("vig") from sharp-book prices to get a fair win probability.
2. Compare every price at a bettable sportsbook against that fair probability -> expected value (EV).
3. Adjust for confidence: how many sharp sources agree, how many books offer the price,
   and which way the sharp line has moved since our earlier snapshots.
4. The single highest score is the Daily Play. The next best go to the members board.
"""
from __future__ import annotations
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
                    if k not in fair:
                        continue   # no sharp price at this exact line
                    p, n, pin = fair[k]
                    evv = p * o["price"] - 1
                    entry = best.setdefault(k, {"plays": []})
                    entry["plays"].append((evv, bk["key"], o["price"], p, n, pin))
        for (mkt, (side, point)), entry in best.items():
            plays = sorted(entry["plays"], reverse=True)
            evv, book, price, p, n, pin = plays[0]
            if not (C.MIN_DECIMAL <= price <= C.MAX_DECIMAL) or evv > C.MAX_EV:
                continue
            edge_books = [b for e, b, *_ in plays if e >= C.FALLBACK_MIN_EV]
            out.append(Play(sport, ev["id"], ev["commence_time"], ev["home_team"], ev["away_team"],
                            mkt, side, point, book, price, p, evv, n, pin, edge_books))
    if history:
        for pl in out:
            pl.movement = movement(pl, history.get(pl.event_id, []))
    for pl in out:
        pl.score = score(pl)
    return out


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
    m = max(-0.03, min(0.03, pl.movement))
    conf *= 1 + m * 5                                   # +/-15% for line moving with/against us
    return pl.ev * conf


def size(pl: Play, tier: str = "edge") -> float:
    """Units to bet. Bigger edge = bigger bet, capped at 2u. Leans (thin edges) are half size."""
    if tier == "lean":
        return 0.5
    if C.UNIT_MODE != "tiered":
        return 1.0
    confirmed = pl.has_pinnacle and len(pl.books_with_edge) >= 2   # big edges are often stale lines
    if pl.ev >= 0.06 and confirmed:
        return 2.0
    return 1.5 if pl.ev >= 0.035 else 1.0


def choose_card(plays: list[Play], max_picks: int) -> list[Play]:
    """Card days: up to max_picks full edges, one per game. No leans; no edge means no bet."""
    ranked = sorted(plays, key=lambda p: p.score, reverse=True)
    seen, out = set(), []
    for p in ranked:
        if p.ev < C.MIN_EV or p.event_id in seen:
            continue
        seen.add(p.event_id)
        p.units = size(p, "edge")
        out.append(p)
        if len(out) == max_picks:
            break
    return sorted(out, key=lambda p: (p.commence_time, -p.score))


def choose(plays: list[Play]) -> tuple[Play | None, list[Play], str]:
    """Returns (daily play, members board, tier). One play per game on the board."""
    ranked = sorted(plays, key=lambda p: p.score, reverse=True)
    full = [p for p in ranked if p.ev >= C.MIN_EV]
    pool, tier = (full, "edge") if full else (
        ([p for p in ranked if p.ev >= C.FALLBACK_MIN_EV], "lean") if C.ALWAYS_PICK else ([], "none"))
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
