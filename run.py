"""Stamped Picks engine.

The main command is `tick`, run every 5 minutes. Each tick checks the date and time (Eastern) and does
whatever is due, once:

  06:00  grade anything the live feed missed (backup grading from The Odds API)
  11:00  Daily Play (NBA, NHL, MLB, college) with its thread, members board, reply kit
  12:00  hype post the day before a big NFL / UFC card ("Last week we CASHED on UFC...")
  20:00  the NFL / UFC card for tomorrow (up to 3 picks each, with the breakdown)
  10:00 NFL / 12:00 UFC  gameday reminder with where each line is now
  ~1 hour before each game: pre-game update (line move, late news)
  during games: halftime / period check-ins, each UFC fight as it ends, results the moment games go final
  Sunday 23:00  week in review

Other commands:
  python run.py daily [--force]        run the Daily Play now
  python run.py card nfl|ufc|boxing [today]   run tomorrow's card now (or today's, for a same-day launch)
  python run.py grade                  backup grading now
  python run.py settle N win|loss|push ["final"]   grade play #N by hand
  python run.py recap | render | newkey | demo
  python run.py menu                   post and pin the buy menu in the free Telegram channel
  python run.py tgchats                list the Telegram chats the bot can see, with their ids
Day to day you can run things from Telegram instead: message the bot /help in your private chat.
Add --no-post to anything to save the text without posting.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ledger import config as C, store, social, seal, render, research, espn, live, voice, telegram, members, tags, analyst, sim
from ledger.model import candidates, choose, choose_card, parse_time
from ledger.grading import grade, closing, clv
from ledger.oddsapi import OddsAPI

TZ = ZoneInfo(C.DISPLAY_TZ)
BOOKS = list(C.PRICING_BOOKS) + list(C.BET_BOOKS)
DAILY_SPORTS = [s for s in C.SPORTS if s not in C.CARD_SPORTS]
CURRENT_FEED = None      # the live-score feed for this run (set by tick)
MMA = "mma_mixed_martial_arts"


# ---------------------------------------------------------------- time and state helpers
def et(now):
    return now.astimezone(TZ)


def today(now):
    return et(now).date().isoformat()


def at(day: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


def game_day_window(day: date):
    start = at(day, f"{C.DAY_START_HOUR:02d}:00")
    return start, start + timedelta(days=1)


def markets_for(sport):
    return C.SPORT_MARKETS.get(sport, C.MARKETS)


def due(state, key, target, now) -> bool:
    return key not in state.setdefault("done", {}) and target <= now < target + timedelta(hours=C.TASK_GRACE_HOURS)


def mark(state, key, now):
    state.setdefault("done", {})[key] = now.isoformat()
    cutoff = (now - timedelta(days=21)).isoformat()
    state["done"] = {k: v for k, v in state["done"].items() if v >= cutoff}


def thread(state, key) -> dict:
    return state.setdefault("threads", {}).setdefault(key, {})


def thread_key(row) -> str:
    return row.get("card") or f"daily{row['pick_no']}"


def reply(state, key, text, post, media=None) -> str | None:
    """Post as the next reply in a thread (or standalone if the thread never got posted)."""
    if not post:
        return None
    t = thread(state, key)
    pid = social.post_x(text, reply_to=t.get("last"), media=media)
    if pid:
        t.setdefault("main", pid)
        t["last"] = pid
    return pid


MEDIA_DIR = Path(tempfile.gettempdir()) / "stamped-media"     # not committed: images are rebuilt from the ledger


def art(kind: str, rows: list[dict], title: str, sub: str = "", name: str = "") -> Path | None:
    """Renders the picture for a post. Returns None (post goes out text-only) if graphics are off or fail."""
    if not C.GRAPHICS or not rows:
        return None
    try:
        from ledger import graphics
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        stem = MEDIA_DIR / (name or f"{kind}_{rows[0]['pick_no']}")
        if kind == "ticket":
            return graphics.save(graphics.ticket(rows[0], title), stem.with_suffix(".png"))
        if kind == "result":
            return graphics.stamp_gif(rows[0], title, stem.with_suffix(".gif"))
        if kind in ("card", "card_results"):
            return graphics.save(graphics.card_image(rows, title, sub, results=kind == "card_results"),
                                 stem.with_suffix(".png"))
    except Exception as ex:                       # noqa: BLE001 - a picture is never worth a missed post
        print(f"[graphics] {kind} failed: {ex}")
    return None


def card_title(sport: str) -> str:
    return f"{C.CARD_SPORTS.get(sport, {}).get('name') or C.SPORTS[sport][0]} CARD"


def card_sub(state, sport: str, day: date) -> str:
    ev = event_name(state, sport, day) if sport == MMA else ""
    return (ev or day.strftime("%A %b %-d")).upper()


def result_title(row) -> str:
    if row.get("card"):
        return card_title(row["sport"])
    return f"DAILY PLAY #{row['pick_no']}"


def prefix(row, rows) -> str:
    if not row.get("card"):
        return ""
    card = [r for r in rows if r.get("card") == row["card"]]
    i = next(i for i, r in enumerate(card) if r["pick_no"] == row["pick_no"])
    return social.NUM[i] + " "


def event_name(state, sport, day: date, feed=None) -> str:
    """The UFC event's name from ESPN's free scoreboard (for #UFC320-style tags). Cached per card."""
    if sport != MMA:
        return ""
    names = state.setdefault("event_names", {})
    key = day.isoformat()
    if key not in names:
        try:
            evs = (feed or CURRENT_FEED or espn.Feed()).raw(sport, day).get("events") or []
            names[key] = evs[0].get("name", "") if evs else ""
        except Exception:
            names[key] = ""
    return names[key]


def card_id(sport, day: date) -> str:
    return f"{sport}|{day.isoformat()}"


def card_day(cid: str) -> date:
    return date.fromisoformat(cid.split("|")[1])


# ---------------------------------------------------------------- odds scanning
def scan(api, now, sports, window=None, budget=None):
    start, end = window or (now + timedelta(minutes=C.EARLIEST_START_MIN), now + timedelta(hours=C.LATEST_START_HOURS))
    active = [s["key"] for s in api.sports() if s["key"] in sports]
    slate = {s: len(api.events(s, start, end)) for s in active}           # free calls
    order = sorted((s for s in slate if slate[s]), key=lambda s: -slate[s])
    plays, scanned, spent0 = [], [], api.spent
    for sport in order:
        mk = markets_for(sport)
        if budget is not None and api.spent - spent0 + len(mk) > budget:
            print(f"[scan] credit budget reached; skipped {sport}")
            continue
        events = api.odds(sport, mk, BOOKS, start, end)
        hist = store.snapshot_history(sport, now)
        store.save_snapshot(sport, now, events)
        plays += candidates(sport, events, now, hist, window=(start, end))
        scanned.append(f"{sport}({slate[sport]})")
    print(f"[scan] {', '.join(scanned) or 'no games'} | {len(plays)} priced sides | credits {api.spent - spent0}, "
          f"remaining {api.remaining}")
    return plays


def to_row(p, date_, now, tier, pick_no="", card=""):
    d = p.to_dict()
    return {**{k: d[k] for k in ("sport", "event_id", "commence_time", "home", "away", "market", "side",
                                 "book", "label", "price_american", "play_to")},
            "date": date_, "pick_no": pick_no, "posted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "tier": tier,
            "point": "" if p.point is None else f"{p.point:g}", "price": f"{p.price:.4f}",
            "fair_prob": f"{p.fair_prob:.4f}", "ev": f"{p.ev:.4f}", "units": f"{p.units:g}",
            "n_sources": p.n_sources, "movement": f"{p.movement:.4f}", "card": card}


def save_writeup(pick_no, market, notes):
    f = C.DATA / "writeups" / f"{pick_no}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"market": market, "research": notes}, indent=1))


# ---------------------------------------------------------------- Daily Play
def run_daily(api, now, state, post):
    date_ = today(now)
    plays = scan(api, now, DAILY_SPORTS, budget=C.SCAN_CREDIT_BUDGET)
    for sp in {p.sport for p in plays}:
        sim.apply(sp, [p for p in plays if p.sport == sp], now)
    plays = analyst.review(None, plays)
    daily, board, tier = choose(plays)
    board = [b for b in board if b.ev >= C.MIN_EV]     # paid plays are full edges only, never leans
    if not daily:
        text = social.no_play_post(date_)
        social.save_post("daily", date_, text)
        if post:
            social.post_x(text)
            social.post_discord(text)
        print("[daily] no play today")
        return
    rows = store.read(store.LEDGER)
    row = to_row(daily, date_, now, tier, pick_no=str(len(rows) + 1))
    store.append(store.LEDGER, row)          # on the record before anything is posted
    print(f"[daily] #{row['pick_no']} {row['label']} {row['price_american']} @ {row['book']} "
          f"edge {float(row['ev']):+.1%} · {row['units']}u ({tier})")
    market = social.market_reasons(row, len(daily.books_with_edge)) + sim.line(daily)
    notes = analyst.notes(daily) or research.pick_research(row)
    save_writeup(row["pick_no"], market, notes)
    th = social.daily_thread(row, rows, market, notes)
    th[0] = social.tagged(th[0], tags.for_rows([row]))
    social.save_post("daily", date_, th)
    brows = [to_row(b, date_, now, tier) for b in board]
    if brows and C.BOARD_KEY:
        seals, blob = seal.seal_board(brows, C.BOARD_KEY)
        (C.DATA / "board").mkdir(parents=True, exist_ok=True)
        (C.DATA / "board" / f"{date_}.enc").write_bytes(blob)
        for s in seals:
            store.append(store.SEALS, {"date": date_, "posted_at": row["posted_at"], "seal": s},
                         ["date", "posted_at", "seal"])
    if post:
        ids = social.post_thread(th, media=art("ticket", [row], f"DAILY PLAY #{row['pick_no']}"))
        if ids:
            thread(state, thread_key(row)).update(main=ids[0], last=ids[-1])
        social.post_discord(social.board_message(row, brows, th))
        social.post_owner(social.reply_kit_message([row]))
        if board:
            top = brows[0]
            telegram.send(C.TG_PREMIUM, social.premium_message(top, social.market_reasons(top, len(board[0].books_with_edge))))
            if telegram.selling():
                telegram.send(C.TG_FREE, social.vip_teaser(brows), members.menu_buttons())


# ---------------------------------------------------------------- card days
def run_hype(api, now, state, post, sport, day):
    s, e = game_day_window(day)
    n = len(api.events(sport, s, e))
    if n < C.CARD_SPORTS[sport]["min_games_for_hype"]:
        return
    text = social.hype_post(store.read(store.LEDGER), sport, card_id(sport, day), n)
    slot = tags.slot({"sport": sport, "commence_time": game_day_window(day)[0].replace(hour=13).isoformat()})
    text = social.tagged(text, " ".join(t for t in (tags.SPORT[sport], tags.event_tag(event_name(state, sport, day)),
                                                    slot, tags.COMMUNITY) if t))
    social.save_post(f"hype_{sport}", today(now), text)
    print(f"[hype] {sport} {day}")
    if post:
        social.post_x(text)
        social.post_discord(text)


def run_card(api, now, state, post, sport, day):
    cfg, (s, e) = C.CARD_SPORTS[sport], game_day_window(day)
    n_games = len(api.events(sport, s, e))                  # free
    if not n_games:
        return
    events = api.odds(sport, markets_for(sport), BOOKS, s, e)
    hist = store.snapshot_history(sport, now)
    store.save_snapshot(sport, now, events)
    if sport == "boxing_boxing":        # big fights only
        events = [ev for ev in events if sum(b["key"] in C.BET_BOOKS for b in ev.get("bookmakers", []))
                  >= C.BIG_FIGHT_MIN_BOOKS]
        n_games = len(events)
        if not events:
            print(f"[card] {sport} {day}: no big fights")
            return
    window = (max(s, now + timedelta(minutes=5)), e)    # "today" cards skip games starting within 5 minutes
    cands = candidates(sport, events, now, hist, window=window)
    taken = {(r["event_id"], r["market"], r["side"]) for r in store.read(store.LEDGER) if r.get("date") == day.isoformat()}
    taken_games = {r["event_id"] for r in store.read(store.LEDGER)
                   if r.get("date") == day.isoformat() and r.get("tier") in ("edge", "lean", "premium")}
    cands = [p for p in cands if p.event_id not in taken_games and (p.event_id, p.market, p.side) not in taken]
    cands = analyst.review(sport, sim.apply(sport, cands, now))      # our sims, then research + verdicts
    picks = choose_card(cands, cfg["max_picks"], cfg.get("min_picks", 0))
    cid = card_id(sport, day)
    if not picks:
        print(f"[card] {cid}: no edges")
        if n_games >= cfg["min_games_for_hype"]:
            text = social.no_card_post(sport, day)
            social.save_post(f"card_{sport}", day.isoformat(), text)
            if post:
                social.post_x(text)
        return
    before = store.read(store.LEDGER)
    new, markets, notes = [], {}, {}
    for p in picks:
        row = to_row(p, day.isoformat(), now, p.tier, pick_no=str(len(before) + len(new) + 1), card=cid)
        store.append(store.LEDGER, row)
        new.append(row)
        markets[row["pick_no"]] = social.market_reasons(row, len(p.books_with_edge)) + sim.line(p)
        notes[row["pick_no"]] = analyst.notes(p) or research.pick_research(row)
        save_writeup(row["pick_no"], markets[row["pick_no"]], notes[row["pick_no"]])
        print(f"[card] {cid} #{row['pick_no']} {row['label']} {row['price_american']} edge {p.ev:+.1%} {row['units']}u")
    th = social.card_thread(sport, day, new, before, markets, notes)
    th[0] = social.tagged(th[0], tags.for_rows(new, event_name(state, sport, day)))
    social.save_post(f"card_{sport}", day.isoformat(), th)
    if post:
        pic = (art("ticket", new, card_title(sport), name=f"card_{sport}_{day}") if len(new) == 1
               else art("card", new, card_title(sport), card_sub(state, sport, day), name=f"card_{sport}_{day}"))
        ids = social.post_thread(th, media=pic)
        if ids:
            thread(state, cid).update(main=ids[0], last=ids[0])   # updates and results reply to the card post
        social.post_discord("\n\n".join(th))
        social.post_owner(social.reply_kit_message(new))
    try:                                          # NFL: every-game picks + parlays, posted with the card
        from ledger import parlays
        upcoming = [ev for ev in events if parse_time(ev["commence_time"]) >= window[0]]
        parlays.run(api, now, state, post, sport, day, cands, upcoming, {p.event_id for p in picks}, to_row, cid)
    except Exception as ex:                       # noqa: BLE001 - extras never block the card
        print(f"[parlays] {ex}")


def run_reminder(api, now, state, post, sport, day):
    cid = card_id(sport, day)
    picks = [r for r in store.read(store.LEDGER) if r.get("card") == cid]
    if not picks:
        return
    quotes = {}
    try:
        s, e = game_day_window(day)
        mk = sorted({r["market"] for r in picks})
        evs = {ev["id"]: ev for ev in api.odds(sport, mk, list(C.PRICING_BOOKS) + sorted({r["book"] for r in picks}), s, e)}
        for r in picks:
            if r["event_id"] in evs:
                fair, bp = closing(r, evs[r["event_id"]], C.PRICING_BOOKS)
                quotes[r["pick_no"]] = (bp, fair)
    except Exception as ex:
        print(f"[reminder] prices unavailable: {ex}")
    text = social.tagged(social.reminder_post(sport, day, picks, quotes),
                         tags.for_rows(picks, event_name(state, sport, day)))
    social.save_post(f"reminder_{sport}", day.isoformat(), text)
    print(f"[reminder] {cid}")
    if post:
        t = thread(state, cid)
        pic = (art("ticket", picks, "GAMEDAY", name=f"reminder_{sport}_{day}") if len(picks) == 1
               else art("card", picks, f"GAMEDAY · {card_title(sport)}", card_sub(state, sport, day),
                        name=f"reminder_{sport}_{day}"))
        pid = social.post_x(text, quote=t.get("main"), media=pic)
        if pid:
            t["last"] = pid            # live updates chain under the reminder, the most visible post
        social.post_discord(text)


# ---------------------------------------------------------------- before kickoff
def set_close(r, fair, bp):
    if fair:
        r["close_fair_prob"] = f"{fair:.4f}"
        r["close_price"] = f"{bp:.4f}" if bp else ""
        r["clv"] = f"{clv(float(r['price']), fair):.4f}"


def run_pregame(api, now, state, rows, post) -> bool:
    pg, closed, changed, groups = state.setdefault("pregame", {}), state.setdefault("closed", {}), False, {}
    lo, hi = C.PREGAME_UPDATE_MIN
    for r in rows:
        if r.get("result") or r.get("tier") == "premium":     # premium picks stay members-only until graded
            continue
        n, mins = r["pick_no"], (parse_time(r["commence_time"]) - now).total_seconds() / 60
        needs_update = 10 < mins <= hi and n not in pg
        needs_close = 0 < mins <= C.FINAL_CLOSE_MIN and n not in closed
        if not (needs_update or needs_close):
            continue
        try:
            ev = api.event_odds(r["sport"], r["event_id"], [r["market"]], list(C.PRICING_BOOKS) + [r["book"]])
            fair, bp = closing(r, ev, C.PRICING_BOOKS)
        except Exception as ex:
            print(f"[pregame] #{n} odds unavailable: {ex}")
            fair, bp = None, None
        set_close(r, fair, bp)
        changed = True
        if needs_close:
            closed[n] = True
        if needs_update:
            pg[n] = True
            slot = parse_time(r["commence_time"]).replace(minute=0, second=0)
            groups.setdefault((thread_key(r), slot), []).append(
                (r, prefix(r, rows), bp, fair, research.pregame_news(r)))
    for (tk, _), items in groups.items():
        text = social.pregame_post(items)
        social.save_post("pregame", today(now), text)
        reply(state, tk, text, post)
        if post:
            social.post_discord(text)
    return changed


# ---------------------------------------------------------------- during the game
def grade_from_espn(r, g, swapped) -> bool:
    if r["sport"] == MMA:
        winners = [f for f in g["fighters"] if f["winner"]]
        if not winners:            # draw or no contest: books refund
            r["result"], r["final_score"], r["profit"] = "push", "Draw / no contest", "0.000"
            return True
        w = winners[0]
        won = espn.fighter_key(r["side"]) == w["key"] or espn.fighter_key(r["side"])[1] == w["key"][1]
        how = g.get("result") or ("" if g["detail"].lower() in ("final", "") else g["detail"])
        u, price = float(r["units"]), float(r["price"])
        r["result"] = "win" if won else "loss"
        r["final_score"] = f"{w['name']} wins" + (f" ({how})" if how else "")
        r["profit"] = f"{(u * (price - 1) if won else -u):.3f}"
        return True
    home, away = (g["away"], g["home"]) if swapped else (g["home"], g["away"])
    game = {"completed": True, "home_team": r["home"], "away_team": r["away"],
            "scores": [{"name": r["home"], "score": home["score"]}, {"name": r["away"], "score": away["score"]}]}
    res = grade(r, game)
    if not res:
        return False
    r["result"], r["final_score"], r["profit"] = res[0], res[1], f"{res[2]:.3f}"
    return True


def run_live(now, state, rows, feed, post) -> bool:
    lv, graded, updates = state.setdefault("live", {}), False, {}
    for r in rows:
        if r.get("result"):
            continue
        start = parse_time(r["commence_time"])
        if not (start <= now <= start + timedelta(hours=12)):
            continue
        g, swapped = espn.lookup(feed, r)
        if not g:
            continue
        if g["completed"]:
            if grade_from_espn(r, g, swapped):
                graded = True
                print(f"[live] #{r['pick_no']} final: {r['result']} ({r['final_score']})")
            continue
        if r["sport"] == MMA or r.get("tier") == "premium":
            continue                    # fights report when they end; premium picks stay private until graded
        latest, reached = live.checkpoints_due(r["sport"], g, lv.get(r["pick_no"], []))
        lv[r["pick_no"]] = sorted(set(lv.get(r["pick_no"], [])) | set(reached))
        if latest:
            name, upto = latest
            st = live.bet_status(r, g, swapped, upto)
            mood = voice.live_mood(live.mood_margin(r, st), live.close_margin(r), r["pick_no"], name)
            updates.setdefault(thread_key(r), []).append(
                (r, prefix(r, rows), live.CHECKPOINT_LABEL[name], st, live.describe(r, st), mood))
    for tk, items in updates.items():
        text = social.live_post(items)
        social.save_post("live", today(now), text)
        reply(state, tk, text, post)
        if post:
            social.post_discord(text)
    return graded


# ---------------------------------------------------------------- results
def announce(state, rows, post):
    """Post every graded play we haven't announced yet, then card recaps when a card is finished."""
    done = set(state.setdefault("announced", []))
    fresh = sorted([r for r in rows if r.get("result") and r["pick_no"] not in done],
                   key=lambda r: (r["commence_time"], int(r["pick_no"])))
    for r in fresh:
        done.add(r["pick_no"])
        if not r.get("card"):
            text = social.tagged(social.daily_result(r, rows), tags.for_rows([r]))
            social.save_post(f"result_{r['pick_no']}", r["date"], text)
            if post:
                social.post_x(text, quote=thread(state, thread_key(r)).get("main"),
                              media=art("result", [r], result_title(r)))
                social.post_discord(text)
        else:
            card_rows = [x for x in rows if x.get("card") == r["card"]]
            text = social.card_pick_result(r, card_rows, prefix(r, rows))
            social.save_post(f"result_{r['pick_no']}", r["date"], text)
            reply(state, r["card"], text, post, media=art("result", [r], result_title(r)) if post else None)
            if post:
                social.post_discord(text)
        print(f"[result] #{r['pick_no']} {r['result']} {float(r['profit']):+.2f}u")
    state["announced"] = sorted(done, key=int)
    recapped = set(state.setdefault("recapped", []))
    for cid in sorted({r["card"] for r in rows if r.get("card")}):
        card_rows = [x for x in rows if x.get("card") == cid]
        if cid in recapped or not all(x.get("result") for x in card_rows):
            continue
        recapped.add(cid)
        text = social.card_recap(cid.split("|")[0], card_day(cid), card_rows, rows)
        text = social.tagged(text, tags.for_rows(card_rows, event_name(state, cid.split("|")[0], card_day(cid))))
        social.save_post("card_recap", card_day(cid).isoformat(), text)
        print(f"[recap] {cid}")
        if post:
            sp = cid.split("|")[0]
            social.post_x(text, quote=thread(state, cid).get("main"),
                          media=art("card_results", card_rows, f"{card_title(sp)} · FINAL",
                                    card_sub(state, sp, card_day(cid)), name=f"recap_{sp}_{card_day(cid)}"))
            social.post_discord(text)
    state["recapped"] = sorted(recapped)
    return bool(fresh)


def backup_grade(api, now, state, feed=None, post=True):
    """Morning job: reveal sealed boards, grade them from the live feed, post VIP results to the free
    channel, and grade from The Odds API anything the live feed missed."""
    rows, board = store.read(store.LEDGER), store.read(store.BOARD_LEDGER)
    revealed = set(state.get("revealed", []))
    for f in sorted((C.DATA / "board").glob("*.enc")) if (C.DATA / "board").exists() else []:
        if f.stem in revealed or not C.BOARD_KEY:
            continue
        plays = seal.unseal(f.read_bytes(), C.BOARD_KEY)
        if all(parse_time(p["commence_time"]) < now for p in plays):
            board += plays
            revealed.add(f.stem)
    state["revealed"] = sorted(revealed)
    ripe = lambda r: not r.get("result") and parse_time(r["commence_time"]) < now - timedelta(hours=3)
    feed = feed or espn.Feed()
    for r in board:
        if ripe(r):
            g, swapped = espn.lookup(feed, r)
            if g and g["completed"]:
                grade_from_espn(r, g, swapped)
    need = {r["sport"] for r in rows if ripe(r)}
    if C.ODDS_PLAN == "paid":
        need |= {r["sport"] for r in board if ripe(r)}
    games = {}
    for sport in need:
        try:
            for g in api.scores(sport, days_from=3):
                games[g["id"]] = g
        except Exception as e:
            print(f"[grade] no scores feed for {sport} ({e})")
    for r in rows + board:
        if ripe(r) and r["event_id"] in games:
            g = grade(r, games[r["event_id"]])
            if g:
                r["result"], r["final_score"], r["profit"] = g[0], g[1], f"{g[2]:.3f}"
    stuck = [r for r in rows if not r.get("result") and parse_time(r["commence_time"]) < now - timedelta(hours=20)]
    if stuck:
        msg = ("Still ungraded: " + ", ".join(f"#{r['pick_no']} {r['label']}" for r in stuck)
               + ". Settle from GitHub: Run workflow → settle N win|loss|push")
        print(f"[grade] {msg}")
        social.post_owner(msg)
    store.write(store.LEDGER, rows)
    store.write(store.BOARD_LEDGER, board, store.BOARD_COLUMNS)
    posted = set(state.setdefault("vip_posted", []))
    for day in sorted({b["date"] for b in board} - posted):
        rows_d = [b for b in board if b["date"] == day]
        if all(b.get("result") for b in rows_d) or parse_time(rows_d[0]["commence_time"]) < now - timedelta(days=2):
            posted.add(day)
            text = social.vip_results(date.fromisoformat(day).strftime("%a %b %-d"), rows_d,
                                      [b for b in board if b.get("result")])
            social.save_post("vip_results", day, text)
            if post and telegram.selling():
                telegram.send(C.TG_FREE, text, members.menu_buttons())
            if post:
                telegram.send(C.TG_VIP, text)
    state["vip_posted"] = sorted(posted)


def run_recap(now, post):
    rows = store.read(store.LEDGER)
    d = et(now).date()
    since = (d - timedelta(days=6)).isoformat()
    text = social.weekly_recap(rows, since, f"{(d - timedelta(days=6)):%b %-d} – {d:%b %-d}")
    text = social.tagged(text, tags.for_sports(sorted({r["sport"] for r in rows if r["date"] >= since})))
    social.save_post("recap", d.isoformat(), text)
    if post:
        social.post_x(text)
        social.post_discord(text)


# ---------------------------------------------------------------- the tick
def tick(api, now, post=True, feed=None):
    for action, args in members.process(now):
        if action == "settle" and len(args) >= 2:
            try:
                cmd_settle(args[0], args[1].lower(), " ".join(args[2:]), post)
                telegram.send(C.TG_OWNER, f"Settled #{args[0]} as {args[1].lower()}. Result posted.")
            except SystemExit as e:
                telegram.send(C.TG_OWNER, str(e))
    global CURRENT_FEED
    state = store.load_state()
    feed = feed or espn.Feed()
    CURRENT_FEED = feed
    d = et(now).date()
    if due(state, f"grade:{d}", at(d, C.SCHEDULE["grade"]), now):
        mark(state, f"grade:{d}", now)
        backup_grade(api, now, state, feed, post)
    if due(state, f"daily:{d}", at(d, C.SCHEDULE["daily"]), now):
        mark(state, f"daily:{d}", now)
        store.save_state(state)
        run_daily(api, now, state, post)
    for sport, cfg in C.CARD_SPORTS.items():
        tomorrow = d + timedelta(days=1)
        if due(state, f"hype:{sport}:{tomorrow}", at(d, cfg["hype"]), now):
            mark(state, f"hype:{sport}:{tomorrow}", now)
            run_hype(api, now, state, post, sport, tomorrow)
        if due(state, f"card:{sport}:{tomorrow}", at(d, cfg["card"]), now):
            mark(state, f"card:{sport}:{tomorrow}", now)
            store.save_state(state)
            run_card(api, now, state, post, sport, tomorrow)
        if due(state, f"reminder:{sport}:{d}", at(d, cfg["reminder"]), now):
            mark(state, f"reminder:{sport}:{d}", now)
            run_reminder(api, now, state, post, sport, d)
    if d.weekday() == 6 and due(state, f"recap:{d}", at(d, C.SCHEDULE["recap"]), now):
        mark(state, f"recap:{d}", now)
        run_recap(now, post)
    rows = store.read(store.LEDGER)
    changed = run_pregame(api, now, state, rows, post)
    graded = run_live(now, state, rows, feed, post)
    if changed or graded:
        store.write(store.LEDGER, rows)
    if announce(state, store.read(store.LEDGER), post):
        render.render()
    try:                                          # UFC nights: cover the whole card, bet or no bet
        from ledger import fightnight
        fightnight.run(now, state, feed, post, store.read(store.LEDGER))
    except Exception as ex:                       # noqa: BLE001 - coverage never blocks picks
        print(f"[fightnight] {ex}")
    try:                                          # grade NFL parlays
        from ledger import parlays
        parlays.settle(now, state, feed, post)
    except Exception as ex:                       # noqa: BLE001
        print(f"[parlays] settle: {ex}")
    store.save_state(state)


def cmd_settle(pick_no, result, final, post=True):
    rows = store.read(store.LEDGER)
    r = next((x for x in rows if x["pick_no"] == str(pick_no)), None)
    if not r:
        raise SystemExit(f"no play #{pick_no}")
    if result not in ("win", "loss", "push"):
        raise SystemExit("result must be win, loss or push")
    u, price = float(r["units"]), float(r["price"])
    r["result"], r["final_score"] = result, final or "settled by hand"
    r["profit"] = f"{(u * (price - 1) if result == 'win' else -u if result == 'loss' else 0):.3f}"
    store.write(store.LEDGER, rows)
    state = store.load_state()
    announce(state, rows, post)
    store.save_state(state)
    print(f"[settle] page -> {render.render()}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["tick", "daily", "card", "grade", "settle", "recap", "render", "newkey", "demo",
                                    "menu", "tgchats", "reset"])
    ap.add_argument("args", nargs="*", help="card: nfl|ufc · settle: play number, win|loss|push, optional final")
    ap.add_argument("--no-post", action="store_true", help="save the text without posting")
    a = ap.parse_args()
    now, post = datetime.now(timezone.utc), not a.no_post
    if a.cmd == "newkey":
        return print(seal.new_key())
    if a.cmd == "render":
        return print(render.render())
    if a.cmd == "demo":
        from tests.simulate import run_demo
        return run_demo()
    if a.cmd == "settle":
        if len(a.args) < 2:
            raise SystemExit('usage: python run.py settle 12 win "Topuria by KO, round 2"')
        return cmd_settle(a.args[0], a.args[1].lower(), " ".join(a.args[2:]), post)
    if a.cmd == "recap":
        return run_recap(now, post)
    if a.cmd == "menu":
        return print(members.cmd_menu(None, now, []))
    if a.cmd == "reset":                          # reset nfl: today's ungraded picks for a sport were deleted from X
        sport = {"nfl": "americanfootball_nfl", "ufc": MMA, "mma": MMA, "boxing": "boxing_boxing"}.get((a.args or [""])[0].lower())
        if not sport:
            raise SystemExit("usage: python run.py reset nfl|ufc|boxing")
        day = et(now).date().isoformat()
        rows = store.read(store.LEDGER)
        keep = [r for r in rows if not (r["sport"] == sport and r["date"] == day and not r.get("result"))]
        store.write(store.LEDGER, keep)
        from ledger import parlays
        parlays.write([p for p in parlays.read() if not (sport == parlays.NFL and p["date"] == day)])
        st = store.load_state()
        st["parlays_posted"] = [d for d in st.get("parlays_posted", []) if not (sport == parlays.NFL and d == day)]
        st["threads"] = {k: v for k, v in st.get("threads", {}).items() if not (sport in k and day in k)}
        store.save_state(st)
        return print(f"[reset] removed {len(rows) - len(keep)} ungraded {sport} picks for {day}")
    if a.cmd == "tgchats":
        for cid, kind, title in telegram.chats():
            print(f"{cid}\t{kind}\t{title}")
        return print("(post a message in each channel first if one is missing)")
    api = OddsAPI(C.ODDS_API_KEY)
    state = store.load_state()
    if a.cmd == "tick":
        return tick(api, now, post)          # tick loads and saves its own state
    if a.cmd == "daily":
        run_daily(api, now, state, post)
    elif a.cmd == "card":
        sport = {"nfl": "americanfootball_nfl", "ufc": MMA, "mma": MMA, "boxing": "boxing_boxing"}.get((a.args or [""])[0].lower())
        if not sport:
            raise SystemExit("usage: python run.py card nfl|ufc|boxing [today]")
        today_card = len(a.args) > 1 and a.args[1].lower() == "today"
        day = et(now).date() + timedelta(days=0 if today_card else 1)
        if today_card and et(now).hour < C.DAY_START_HOUR:
            day -= timedelta(days=1)
        run_card(api, now, state, post, sport, day)
    elif a.cmd == "grade":
        backup_grade(api, now, state)
        announce(state, store.read(store.LEDGER), post)
        render.render()
    store.save_state(state)


if __name__ == "__main__":
    sys.exit(main())
