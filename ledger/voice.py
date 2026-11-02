"""The account's voice: rotating phrasings so posts don't read like a bot, plus hooks built from the real
record (streaks, how last week's card went). Every number in a hook comes from the ledger."""
import hashlib
from datetime import date
from . import config as C, store
from .model import parse_time


def pick(options: list[str], *key) -> str:
    """Same inputs -> same phrasing (so reruns don't change a post), different posts -> varied phrasing."""
    h = int(hashlib.md5("|".join(map(str, key)).encode()).hexdigest(), 16)
    return options[h % len(options)]


def money(units: float, signed: bool = False) -> str:
    d = round(units, 2) * C.UNIT_DOLLARS
    s = f"${abs(d):,.0f}"
    return (("+" if d >= 0 else "-") + s) if signed else s


def units_money(u: float) -> str:
    return f"{u:+.2f}u ({money(u, True)})"


# ---------------------------------------------------------------- streaks
def _graded(rows):
    done = [r for r in rows if r.get("result") in ("win", "loss")]
    return sorted(done, key=lambda r: (r["commence_time"], int(r["pick_no"])))


def streak(rows) -> tuple[str, int]:
    done = _graded(rows)
    if not done:
        return "", 0
    last, n = done[-1]["result"], 0
    for r in reversed(done):
        if r["result"] != last:
            break
        n += 1
    return last, n


def streak_hook(rows, key) -> str:
    kind, n = streak(rows)
    if kind == "win" and n >= 3:
        return pick([f"🔥 We've cashed {n} straight.", f"🔥 {n} winners in a row. Let's keep it rolling.",
                     f"Heater alert 🔥 {n} straight winners."], key, n)
    if kind == "loss" and n >= 3:
        return pick([f"Rough stretch ({n} straight losses). Same process, every pick still on the ledger.",
                     f"We've dropped {n} in a row. No hiding it, no chasing it. Same process."], key, n)
    return ""


def result_streak_line(rows, key) -> str:
    kind, n = streak(rows)
    if kind == "win" and n >= 3:
        return pick([f"That's {n} straight 🔥", f"{n} in a row. The heater continues 🔥"], key, n)
    if kind == "win" and n == 2:
        return "Back-to-back winners 💰"
    if kind == "loss" and n >= 3:
        return "We'll take our lumps and keep the process. On to the next."
    return ""


# ---------------------------------------------------------------- cards and weeks
def card_name(sport: str, game_day: date) -> str:
    name = C.CARD_SPORTS.get(sport, {}).get("name") or C.SPORTS[sport][0]
    return f"{name} {game_day.strftime('%A')}"


def last_card(rows, sport: str, before: str) -> tuple[str, dict] | tuple[None, None]:
    """'Last week' for this sport: every graded card in the 7 days before this one (so NFL Sunday hype
    counts Sunday, Monday and Thursday). If nothing in that window, the most recent finished card."""
    day = date.fromisoformat(before.split("|")[1])
    week_ago = f"{sport}|{(day.fromordinal(day.toordinal() - 7)).isoformat()}"
    mine = [r for r in rows if r.get("card", "").startswith(sport + "|") and r["card"] < before and r.get("result")]
    week = [r for r in mine if r["card"] >= week_ago]
    if week:
        return week[-1]["card"], store.stats(week)
    ids = sorted({r["card"] for r in mine})
    for cid in reversed(ids):
        picks = [r for r in rows if r.get("card") == cid]
        if all(r.get("result") for r in picks):
            return cid, store.stats(picks)
    return None, None


def hype_post(rows, sport: str, card_id: str, n_games: int, drop_time: str) -> str:
    name = C.CARD_SPORTS[sport]["name"]
    from .social import event_word
    what, what_n = event_word(sport), event_word(sport, n_games)
    emoji = C.SPORTS[sport][1]
    cid, st = last_card(rows, sport, card_id)
    tail = (f"Picks drop tonight at {drop_time} ET {emoji}" if not drop_time.endswith("AM") else
            (f"Picks drop tomorrow at {drop_time} ET, then again before the 4 PM and night games {emoji}"
             if sport == "americanfootball_nfl" else f"Picks drop tomorrow at {drop_time} ET {emoji}"))
    if not cid:
        return pick([f"First {name} card on the ledger this weekend {emoji}\n\n{n_games} {what_n} on the board and the model "
                     f"is already finding numbers we like.\n\n{tail}",
                     f"{name} week is here {emoji}\n\nWe've been running every line on tomorrow's {n_games} {what_n}.\n\n{tail}"],
                    card_id)
    rec = f"{st['wins']}-{st['losses']}" + (f"-{st['pushes']}" if st["pushes"] else "")
    um = units_money(st["profit"])
    if st["profit"] > 0:
        return pick([f"Last week we CASHED on {name}: {rec}, {um} 💰\n\nWe plan to do the same this week.\n\n{tail}",
                     f"Last {name} card: {rec}, {um} ✅\n\nLooking to run it back tomorrow.\n\n{tail}",
                     f"{name} last week? {rec} and {um} 💰\n\nSame process this week. {n_games} {what_n} on the board.\n\n{tail}"],
                    card_id)
    if st["profit"] < 0:
        return pick([f"Last week was a tough one on the {name} card ({rec}, {um}).\n\nWe've been grinding the numbers on "
                     f"this week's {what} and we like what we see.\n\n{tail}",
                     f"Not our week on {name} last time out ({rec}, {um}). It's on the ledger like everything else.\n\n"
                     f"Fresh card, fresh numbers. We've got some solid spots for tomorrow.\n\n{tail}"], card_id)
    return f"Broke even on {name} last week ({rec}).\n\nLooking for more this week.\n\n{tail}"


def weekly_lines(rows, since: str) -> list[str]:
    week = [r for r in rows if r["date"] >= since and r.get("result")]
    out = []
    groups = {}
    for r in week:
        g = C.CARD_SPORTS[r["sport"]]["name"] if r.get("card") else "Daily Play"
        groups.setdefault(g, []).append(r)
    for g, rs in sorted(groups.items()):
        st = store.stats(rs)
        rec = f"{st['wins']}-{st['losses']}" + (f"-{st['pushes']}" if st["pushes"] else "")
        out.append(f"{'✅' if st['profit'] > 0 else '❌' if st['profit'] < 0 else '➖'} {g}: {rec}, {units_money(st['profit'])}")
    return out


# ---------------------------------------------------------------- live and results
def live_mood(margin: float, close: float, *key) -> str:
    if margin > close:
        return pick(["Love where this one sits 😤", "We're in great shape here.", "Cruising so far.",
                     "This is the script we wanted."], *key)
    if margin > 0:
        return pick(["Ahead, but it's tight. Sweat mode 😅", "On the right side, barely. Keep going.",
                     "Clinging to it 😬"], *key)
    if margin == 0:
        return pick(["Right on the number. Sweat incoming 😅", "Dead even with our number. Buckle up."], *key)
    if margin >= -close:
        return pick(["Down a little, plenty of time left.", "Need a small push from here.",
                     "Not where we want it, but very much alive."], *key)
    return pick(["Not our half. Need a big push from here.", "Tough spot right now. Need a comeback.",
                 "Trailing for now. Plenty left."], *key)


def win_shout(key) -> str:
    return pick(["CASH IT ✅", "WINNER ✅", "Ticket cashed ✅", "Got there ✅"], key)


def loss_note(key) -> str:
    return pick(["That one didn't go our way ❌", "Lost that one ❌", "Can't win 'em all ❌"], key)


def reply_kit(row: dict) -> list[str]:
    """Lines you paste by hand under other cappers' posts about the same game. X's automation rules ban
    bots replying to posts found by search, so these are drafts for a human, never auto-posted."""
    book = C.BET_BOOKS.get(row["book"], row["book"])
    p = float(row["fair_prob"])
    return [f"We're on {row['label']} too. Sharp market has it around {p:.0%}, so {row['price_american']} is a good number. "
            f"Wouldn't go past {row['play_to']}.",
            f"Like this one. {book} has {row['label']} at {row['price_american']}, fair price is more like {row['play_to']}.",
            f"Same side here. If you're tailing, don't chase it past {row['play_to']}."]
