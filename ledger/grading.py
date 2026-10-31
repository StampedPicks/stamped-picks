"""Grade a play from a final score, and measure closing line value (CLV)."""
from .model import devig, side_key


def grade(row: dict, game: dict) -> tuple[str, str, float] | None:
    """game = one item from the Odds API /scores response. Returns (result, final, profit) or None."""
    if not game.get("completed") or not game.get("scores"):
        return None
    s = {x["name"]: float(x["score"]) for x in game["scores"]}
    home, away = game["home_team"], game["away_team"]
    final = f"{away} {s[away]:g} – {home} {s[home]:g}"
    side, mkt = row["side"], row["market"]
    point = float(row["point"]) if row.get("point") not in (None, "") else 0.0
    if mkt == "totals":
        margin = (s[home] + s[away] - point) * (1 if side == "Over" else -1)
    else:
        other = away if side == home else home
        margin = s[side] + (point if mkt == "spreads" else 0) - s[other]
    units, price = float(row["units"]), float(row["price"])
    if margin > 0:
        return "win", final, round(units * (price - 1), 3)
    if margin < 0:
        return "loss", final, -units
    return "push", final, 0.0


def closing(row: dict, event: dict, pricing_books: dict) -> tuple[float | None, float | None]:
    """From a pre-game snapshot of the event: (closing fair prob of our side, closing price at our book)."""
    mkt = row["market"]
    point = float(row["point"]) if row.get("point") not in (None, "") else None
    want = (row["side"], point)
    num = den = 0.0
    book_price = None
    for bk in event.get("bookmakers", []):
        for m in bk.get("markets", []):
            if m["key"] != mkt:
                continue
            keys = [side_key(mkt, o) for o in m["outcomes"]]
            if bk["key"] == row["book"] and want in keys:
                book_price = m["outcomes"][keys.index(want)]["price"]
            w = pricing_books.get(bk["key"])
            if w and len(m["outcomes"]) == 2 and want in keys:
                p = devig([o["price"] for o in m["outcomes"]])[keys.index(want)]
                num += w * p
                den += w
    return (num / den if den else None), book_price


def clv(price: float, close_fair_prob: float) -> float:
    """EV of the price we posted, measured against the closing no-vig line.
    Positive = we beat the close, the best long-run signal that a bettor has a real edge."""
    return price * close_fair_prob - 1
