"""How a bet stands during a game: covering or not, leading or not, total on pace or not."""
from . import config as C

CLOSE = {  # "close game" margin for the mood line, per sport and bet type
    "americanfootball_nfl": (3, 4), "americanfootball_ncaaf": (4, 6), "basketball_nba": (4, 6),
    "basketball_ncaab": (4, 6), "icehockey_nhl": (1, 1), "baseball_mlb": (1, 1.5),
}
CHECKPOINT_LABEL = {"half": "Halftime", "q4": "End of 3", "p1": "After 1 period", "p2": "After 2 periods",
                    "mid": "Through 5 innings"}


def checkpoints_due(sport: str, g: dict, done: list[str]):
    """Returns (latest checkpoint to post or None, every checkpoint reached so far).
    latest = (name, periods_completed). If we fell behind (a slow runner), only the newest checkpoint
    is posted and the skipped ones are marked done, so a stale halftime update never goes out late."""
    if g["completed"] or g["state"] != "in":
        return None, []
    reached = []
    detail = g["detail"].lower()
    for name, start_period in C.ESPN[sport][2]:
        if g["period"] >= start_period or (name == "half" and "half" in detail) \
                or (g["period"] == start_period - 1 and detail.startswith("end")):
            reached.append((name, start_period - 1))
    if not reached or reached[-1][0] in done:
        return None, [n for n, _ in reached]
    return reached[-1], [n for n, _ in reached]


def _scores(row, g, swapped, upto):
    home, away = (g["away"], g["home"]) if swapped else (g["home"], g["away"])
    def at(side):
        if upto and len(side["lines"]) >= upto:
            return sum(side["lines"][:upto])
        return side["score"]
    return at(home), at(away)


def bet_status(row: dict, g: dict, swapped: bool, upto: int | None) -> dict:
    """margin > 0 means the bet is currently winning. upto = periods completed (None = current score)."""
    hs, as_ = _scores(row, g, swapped, upto)
    total = hs + as_
    point = float(row["point"]) if row.get("point") not in (None, "") else 0.0
    side_is_home = row["side"] == row["home"]
    mine, theirs = (hs, as_) if side_is_home else (as_, hs)
    reg = C.ESPN[row["sport"]][1]
    elapsed = (upto / reg) if upto else None
    out = {"home": hs, "away": as_, "total": total}
    if row["market"] == "totals":
        out["margin"] = (total - point) if row["side"] == "Over" else (point - total)
        if elapsed:
            pace = total / elapsed
            out["pace"] = pace
            out["pace_margin"] = (pace - point) if row["side"] == "Over" else (point - pace)
    elif row["market"] == "spreads":
        out["margin"] = mine + point - theirs
    else:
        out["margin"] = mine - theirs
    return out


def describe(row: dict, st: dict) -> str:
    """One plain line on where the bet stands."""
    fmt = lambda x: f"{x:g}"
    if row["market"] == "totals":
        need = float(row["point"])
        if "pace" in st:
            return f"{fmt(st['total'])} so far, on pace for about {st['pace']:.0f} vs our {row['side']} {fmt(need)}."
        return f"{fmt(st['total'])} so far vs our {row['side']} {fmt(need)}."
    m = st["margin"]
    if row["market"] == "spreads":
        if m > 0:
            return f"Covering by {fmt(m)}."
        if m == 0:
            return "Right on the number."
        return f"Need {fmt(-m)} more to cover."
    if m > 0:
        return f"Up {fmt(m)}."
    if m == 0:
        return "All tied up."
    return f"Down {fmt(-m)}."


def close_margin(row) -> float:
    a, b = CLOSE.get(row["sport"], (3, 4))
    return b if row["market"] == "totals" else a


def mood_margin(row, st) -> float:
    return st.get("pace_margin", st["margin"]) if row["market"] == "totals" else st["margin"]


def scoreline(row, st) -> str:
    fmt = lambda x: f"{x:g}"
    return f"{row['away']} {fmt(st['away'])}, {row['home']} {fmt(st['home'])}"
