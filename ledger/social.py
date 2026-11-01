"""Everything that gets posted, in the account's voice, plus delivery to X and Discord.

Every post is checked against X's 280-character limit (emoji count double). Numbers always come from
the ledger and the model; the voice module only varies the wording around them."""
import time
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from . import config as C, store, voice
from .model import parse_time, fmt_american
from .voice import money, units_money
from . import telegram

TZ = ZoneInfo(C.DISPLAY_TZ)
LIMIT = 280
NUM = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


# ---------------------------------------------------------------- helpers
def _when(iso: str, day: bool = False) -> str:
    t = parse_time(iso).astimezone(TZ)
    return t.strftime("%a %-I:%M %p ET") if day else t.strftime("%-I:%M %p ET")


def stamped(row) -> str:
    """The brand: the exact time the pick was locked in, before the game."""
    return f"🕒 Stamped {parse_time(row['posted_at']).astimezone(TZ):%a %-I:%M %p} ET"


def _tonight(iso: str) -> str:
    return "tonight" if parse_time(iso).astimezone(TZ).hour >= 17 else "today"


def _book(key: str) -> str:
    return C.BET_BOOKS.get(key, key)


def _emoji(sport: str) -> str:
    return C.SPORTS.get(sport, (sport, "📒"))[1]


def stake_line(units: float) -> str:
    word = "unit" if units == 1 else "units"
    return f"💵 Bet {units:g} {word} (= {money(units)} if 1 unit is ${C.UNIT_DOLLARS})"


def stake_short(units: float) -> str:
    return f"{units:g}u ({money(units)})"


def rec(st: dict) -> str:
    return f"{st['wins']}-{st['losses']}" + (f"-{st['pushes']}" if st["pushes"] else "")


def record_line(st: dict) -> str:
    return f"{rec(st)} · {st['profit']:+.2f}u ({money(st['profit'], True)} at ${C.UNIT_DOLLARS}/unit)"


def x_len(text: str) -> int:
    """X's weighted length: most Latin text counts 1, emoji and CJK count 2. Slightly conservative."""
    n = 0
    for ch in text:
        o = ord(ch)
        n += 1 if (o <= 4351 or 8192 <= o <= 8205 or 8208 <= o <= 8223 or 8242 <= o <= 8247) else 2
    return n


def tagged(text: str, tags: str) -> str:
    """Adds hashtags as a last line only if they fit; drops tags (from the end) before any content."""
    words = tags.split()
    limit = 25000 if C.X_PREMIUM else LIMIT
    while words:
        out = f"{text}\n\n{' '.join(words)}"
        if x_len(out) <= limit:
            return out
        words.pop(-2 if len(words) > 1 else -1)      # keep the community tag longest
    return text


def fit(lines: list[str], keep_last: int = 0) -> str:
    """Join lines into one post. Over the limit (280, or a readable 1,200 with X Premium) → drop the lowest
    optional lines until it fits, always keeping the first line and the last `keep_last` lines."""
    lines = [l for l in lines if l is not None]
    limit = 1200 if C.X_PREMIUM else LIMIT
    while x_len("\n".join(lines)) > limit:
        cand = [i for i in range(1, len(lines) - keep_last) if lines[i].strip()]
        if not cand:
            break
        lines.pop(cand[-1])
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    text = text.strip()
    while x_len(text) > limit:
        text = text[:-2].rstrip() + "…"
    return text


def _verdict(row, now_price, now_fair) -> str:
    """Short read on the current price vs. ours."""
    if not now_price:
        return ""
    a0, a1 = row["price_american"], fmt_american(now_price)
    good = (now_price * now_fair - 1 >= 0) if now_fair else now_price >= float(row["price"]) - 0.004
    if not good:
        return f"{a0} → {a1}. Past our number now, don't chase ⚠️"
    if now_price < float(row["price"]) - 0.004:
        return f"{a0} → {a1}. We got the better number ✔"
    if now_price > float(row["price"]) + 0.004:
        return f"{a0} → {a1}. Even better now ✔"
    return f"Holding at {a0} ✔"


# ---------------------------------------------------------------- the reasons
def market_reasons(row: dict, n_edge_books: int) -> list[str]:
    """Why the numbers like it. Always true: straight from the model's own data."""
    p, ev = float(row["fair_prob"]), float(row["ev"])
    side = {"h2h": row["side"], "spreads": row["label"], "totals": f"the {row['side']} {row['point']}"}[row["market"]]
    what = {"h2h": "to win", "spreads": "to cover", "totals": "to hit"}[row["market"]]
    out = [f"• Our fair price: {side} is a {p:.0%} shot {what} (fair odds {row['play_to']})."]
    if ev >= C.MIN_EV:
        out.append(f"• {_book(row['book'])} pays {row['price_american']}, about {ev:.1%} more than that.")
    elif ev >= 0:
        out.append(f"• {_book(row['book'])} pays {row['price_american']}, right at or a hair above fair. "
                   "Best number on the board.")
    else:
        out.append(f"• {_book(row['book'])} pays {row['price_american']}, the best number on the board for this side. "
                   "Thin edge, so it's a smaller bet.")
    mv = float(row.get("movement") or 0)
    if mv > 0.005:
        out.append("• The sharp line has been moving our way.")
    elif mv < -0.005:
        out.append("• Sharp line drifted the other way since open.")
    out.append(f"• {n_edge_books} books still offer a number this good." if n_edge_books >= 2
               else f"• Only {_book(row['book'])} has this price, so it may not last.")
    return out


def _matchup(research: dict | None, prefix: str = "") -> str | None:
    if not research:
        return None
    head = f"{prefix}🎯 {research['angle']}" if research.get("angle") else f"{prefix}The matchup:"
    lines = [head] + [f"• {p}" for p in research["points"]]
    if research.get("context"):
        lines += ["", "📋 What we checked:"] + [f"• {c}" for c in research["context"]]
    if research.get("news"):
        lines.append(f"📰 {research['news']}")
    tail = []
    if research.get("risk"):
        tail.append(f"⚠️ Risk: {research['risk']}")
    if research.get("confidence"):
        tail.append(f"Our confidence: {research['confidence']}/10")
    if tail:
        lines += [""] + tail
    return fit(lines, keep_last=len(tail))


def _units_note(units_text: str, extra: str) -> str:
    return fit([f"Quick note on units: 1 unit = your normal bet. If yours is ${C.UNIT_DOLLARS}, {units_text}. "
                "Only bet what you can afford to lose 🤝", "", extra])


# ---------------------------------------------------------------- Daily Play
def daily_thread(row: dict, before: list[dict], market: list[str], research: dict | None) -> list[str]:
    st = store.stats(before)
    n, u = row["pick_no"], float(row["units"])
    bet = f"{row['label']} ({row['price_american']})"
    lean = row["tier"] == "lean"
    head = [f"📒 Daily Play #{n} · {_emoji(row['sport'])} {C.SPORTS[row['sport']][0]}"
            + (" · LEAN (smaller bet)" if lean else ""), "",
            voice.pick([f"We're on {bet} at {_book(row['book'])} {_tonight(row['commence_time'])}.",
                        f"Today's play: {bet} at {_book(row['book'])}.",
                        f"Riding with {bet} at {_book(row['book'])} {_tonight(row['commence_time'])}."], "open", n),
            stake_line(u), f"⏰ {_when(row['commence_time'])} · good to {row['play_to']}", stamped(row), ""]
    hook = voice.streak_hook(before, n)
    if hook:
        head.append(hook)
    if st["wins"] + st["losses"] + st["pushes"]:
        head.append(f"📊 Record: {record_line(st)}")
    head.append(voice.pick(["Why we like it 🧵👇", "Here's why 🧵👇", "The breakdown 🧵👇"], "why", n))
    posts = [fit(head, keep_last=1),
             fit([voice.pick(["The numbers:", "Why the numbers like it:", "Here's the edge:"], "num", n)] + market)]
    m = _matchup(research)
    if m:
        posts.append(m)
    posts.append(_units_note(f"today's play is {money(u)}",
                             "Stamped before the game, graded in public, wins and losses (link in bio). Update before "
                             "the game, check-ins during it."))
    return posts                              # a real thread: one post per step, so people can reply to each


def no_play_post(date: str) -> str:
    return voice.pick([f"📒 No Daily Play today ({date}).\n\nThe model didn't find a price worth betting. "
                       "No edge, no bet. That discipline is the product.",
                       f"📒 Passing today ({date}).\n\nNothing on the board cleared our bar. "
                       "Sitting out is a winning move too. Back tomorrow."], date)


# ---------------------------------------------------------------- card days (NFL, UFC)
def card_thread(sport, game_day, picks, before, markets, research) -> list[str]:
    st = store.stats(before)
    name, emoji = voice.card_name(sport, game_day), _emoji(sport)
    k = f"{sport}{game_day}"
    same_day = parse_time(picks[0]["posted_at"]).astimezone(TZ).date() == game_day
    head = [voice.pick([f"{emoji} {name} card is IN", f"{emoji} {name} card is set", f"{emoji} Here's our {name} card"], k),
            "", f"{len(picks)} play{'s' if len(picks) > 1 else ''} we like for "
                f"{'today' if same_day else 'tomorrow'}:"]
    for i, r in enumerate(picks):
        head.append(f"{NUM[i]} {r['label']} ({r['price_american']}) · {stake_short(float(r['units']))}"
                    + (" · lean" if r.get("tier") == "lean" else ""))
    head += ["", stamped(picks[0]) + ", before every game"]
    if st["wins"] + st["losses"] + st["pushes"]:
        head.append(f"📊 Record: {record_line(st)}")
    hook = voice.streak_hook(before, k)
    if hook:
        head.append(hook)
    head.append(voice.pick(["Breakdown on each one 🧵👇", "Why we like each one 🧵👇"], k))
    posts = [fit(head, keep_last=1)]
    for i, r in enumerate(picks):
        posts.append(fit([f"{NUM[i]} {r['label']} ({r['price_american']}) at {_book(r['book'])}"
                          + (" · LEAN (smaller bet)" if r.get("tier") == "lean" else ""),
                          f"⏰ {_when(r['commence_time'], day=True)} · good to {r['play_to']} · {stake_short(float(r['units']))}",
                          ""] + markets[r["pick_no"]]))
        m = _matchup(research.get(r["pick_no"]), prefix=f"{NUM[i]} ")
        if m:
            posts.append(m)
    total = sum(float(r["units"]) for r in picks)
    what = "fights" if sport == "mma_mixed_martial_arts" else "games"
    posts.append(_units_note(f"the whole card is {money(total)}",
                             ("Live updates" if same_day else "Gameday reminder tomorrow, then live updates")
                             + f" during the {what}. Every pick stamped, "
                             "every result public (link in bio)."))
    return posts                              # a real thread: one post per step, so people can reply to each


def no_card_post(sport, game_day) -> str:
    name, emoji = voice.card_name(sport, game_day), _emoji(sport)
    return (f"{emoji} No {name} plays this week.\n\nWe ran every line and nothing cleared our bar. "
            "No edge, no bet. Protecting the bankroll is part of the job.")


def reminder_post(sport, game_day, picks, quotes) -> str:
    name = voice.card_name(sport, game_day)
    k = f"rem{sport}{game_day}"
    opener = voice.pick(["It's FIGHT DAY 🥊", "Fight day is here 🥊"], k) if sport == "mma_mixed_martial_arts" \
        else voice.pick(["☀️ GAMEDAY.", "Gameday is here 🏈", "It's gameday 🏈"], k)
    lines = [f"{opener} Today's {name} card:", ""]
    for i, r in enumerate(picks):
        p, f = quotes.get(r["pick_no"], (None, None))
        v = _verdict(r, p, f) if p else ""
        short = v.replace(". Past our number now, don't chase ⚠️", " ⚠️ past our number").replace(
            ". We got the better number ✔", " ✔").replace(". Even better now ✔", " ✔")
        lines.append(f"{NUM[i]} {r['label']} {short or r['price_american']} · {r['units']}u")
    lines += ["", "✔ still worth it · ⚠️ don't chase" if any(quotes.values()) else "",
              voice.pick(["Live updates all day. Let's get it 💰", "We'll be checking in all day. Let's go 💰",
                          "Updates as it happens. Let's work 💰"], k)]
    return fit(lines, keep_last=1)


# ---------------------------------------------------------------- before and during the game
def pregame_post(items: list[tuple]) -> str:
    """items: (row, prefix, now_price, now_fair, news). One post per kickoff group."""
    r0 = items[0][0]
    k = "pre" + r0["pick_no"]
    if len(items) == 1 and not r0.get("card"):
        head = f"⏱️ Pre-game update · Daily Play #{r0['pick_no']}"
    else:
        head = voice.pick(["⏱️ About an hour to go.", "⏱️ Almost time.", "⏱️ T-minus one hour."], k)
    lines = [head, ""]
    news = []
    for row, prefix, p, f, nw in items:
        v = _verdict(row, p, f)
        lines.append(f"{prefix}{row['label']} · {stake_short(float(row['units']))}" + (f"\n{v}" if v else ""))
        if nw:
            news.append(f"📰 {nw}")
    lines += [""] + news
    return fit(lines, keep_last=len(news))


def live_post(items: list[tuple]) -> str:
    """items: (row, prefix, checkpoint_label, status, describe_text, mood)."""
    if len(items) == 1:
        row, prefix, cp, st, desc, mood = items[0]
        from .live import scoreline
        return fit([f"{_emoji(row['sport'])} {cp}: {scoreline(row, st)}", "",
                    f"{prefix}{row['label']}: {desc}", mood])
    from .live import scoreline
    lines = [voice.pick(["👀 Live check", "👀 Checking in", "📡 Live update"], items[0][0]["pick_no"], len(items)), ""]
    for row, prefix, cp, st, desc, mood in items:
        lines += [f"{prefix}{row['label']} ({cp}, {scoreline(row, st)})", f"{desc} {mood}", ""]
    return fit(lines)


# ---------------------------------------------------------------- results
def _final(row) -> str:
    return row.get("final_score") or "settled"


def _clv_line(row) -> str:
    if row.get("close_price") and row.get("clv"):
        c = float(row["clv"])
        return f"Closed {fmt_american(float(row['close_price']))}. " + (
            f"We beat the close by {c:.1%} ✔" if c > 0 else f"Close went against us ({c:+.1%}).")
    return ""


def daily_result(row: dict, upto: list[dict]) -> str:
    """Quote-post of the original pick."""
    st, n, p = store.stats(upto), row["pick_no"], float(row["profit"])
    head = {"win": f"{voice.win_shout(n)} Daily Play #{n} wins", "loss": f"{voice.loss_note(n)} Daily Play #{n}",
            "push": f"➖ Push. Daily Play #{n}"}[row["result"]]
    lines = [head, f"{row['label']} ({row['price_american']}), {float(row['units']):g}u · Final: {_final(row)}",
             f"{p:+.2f}u ({money(p, True)} at ${C.UNIT_DOLLARS}/unit)", _clv_line(row), "",
             f"📊 Record: {record_line(st)}"]
    if st["wins"] + st["losses"] >= 10:
        lines.append(f"ROI {st['roi']:+.1%}")
    tail = voice.result_streak_line(upto, n)
    if row["result"] == "loss":
        tail = "Logged like every other pick. We don't delete."
    if tail:
        lines.append(tail)
    return fit(lines, keep_last=2 if tail else 1)


def card_pick_result(row: dict, card_rows: list[dict], prefix: str) -> str:
    """Reply in the card thread as each game/fight finishes."""
    p = float(row["profit"])
    shout = {"win": voice.win_shout(row["pick_no"]), "loss": voice.loss_note(row["pick_no"]), "push": "➖ Push"}[row["result"]]
    done = [r for r in card_rows if r.get("result")]
    cst = store.stats(done)
    lines = [f"{shout} {prefix}{row['label']} ({row['price_american']})",
             f"Final: {_final(row)}", f"{p:+.2f}u ({money(p, True)})", ""]
    if len(done) < len(card_rows):
        lines.append(f"Card so far: {rec(cst)}, {units_money(cst['profit'])}. "
                     + voice.pick(["Still got more to go 👀", "On to the next one.", "Next one up 👇"], row["pick_no"]))
    return fit(lines, keep_last=1)


def card_recap(sport, game_day, card_rows, upto) -> str:
    """Standalone post (quoting the card) once every pick on the card is graded."""
    cst, st = store.stats(card_rows), store.stats(upto)
    name, emoji = voice.card_name(sport, game_day), _emoji(sport)
    k = f"recap{sport}{game_day}"
    if cst["profit"] > 0:
        head = voice.pick([f"{emoji} {name} card CASHED: {rec(cst)}, {units_money(cst['profit'])} 💰",
                           f"{emoji} {name} card in the books: {rec(cst)}, {units_money(cst['profit'])} 💰"], k)
    elif cst["profit"] < 0:
        head = f"{emoji} {name} card: {rec(cst)}, {units_money(cst['profit'])}. Tough one, and it's on the ledger like every other."
    else:
        head = f"{emoji} {name} card: {rec(cst)}, broke even."
    lines = [head, ""]
    for r in card_rows:
        lines.append(f"{'✅' if r['result'] == 'win' else '❌' if r['result'] == 'loss' else '➖'} {r['label']}")
    lines += ["", f"📊 Season: {record_line(st)}"]
    tail = voice.result_streak_line(upto, k)
    if tail:
        lines.append(tail)
    return fit(lines, keep_last=2 if tail else 1)


def weekly_recap(rows, since: str, label: str) -> str:
    st = store.stats(rows)
    lines = [f"📊 Week in review ({label})", ""] + voice.weekly_lines(rows, since) + [
        "", f"Season: {record_line(st)}", "Every play is on the ledger."]
    return fit(lines, keep_last=1)


def hype_post(rows, sport, card_id, n_games) -> str:
    hh, mm = map(int, C.CARD_SPORTS[sport]["card"].split(":"))
    drop = f"{(hh - 1) % 12 + 1}{':%02d' % mm if mm else ''} {'PM' if hh >= 12 else 'AM'}"
    return fit(voice.hype_post(rows, sport, card_id, n_games, drop).split("\n"))


# ---------------------------------------------------------------- private messages to you / members
def board_message(daily: dict, board: list[dict], thread: list[str]) -> str:
    out = ["\n\n".join(thread), "", "**Members Board** (sealed publicly until game time)"]
    for b in board:
        label, emoji = C.SPORTS.get(b["sport"], (b["sport"], ""))
        out.append(f"{emoji} {label} **{b['label']}** {b['price_american']} at {_book(b['book'])} · "
                   f"{stake_short(float(b['units']))} · edge {float(b['ev']):+.1%} · "
                   f"good to {b['play_to']} · {_when(b['commence_time'])}")
    return "\n".join(out)


def reply_kit_message(rows: list[dict]) -> str:
    out = ["**Reply kit** (paste these yourself under other cappers' posts about these games. "
           "X bans automated replies to posts found by search, so the bot never does this.)", ""]
    for r in rows:
        out.append(f"__{r['label']} ({r['away']} @ {r['home']}, {_when(r['commence_time'], day=True)})__")
        out += [f"• {line}" for line in voice.reply_kit(r)]
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- delivery
def _x_session():
    if not all(C.X_KEYS.values()):
        return None
    from requests_oauthlib import OAuth1Session
    return OAuth1Session(C.X_KEYS["X_API_KEY"], C.X_KEYS["X_API_SECRET"],
                         C.X_KEYS["X_ACCESS_TOKEN"], C.X_KEYS["X_ACCESS_SECRET"])


def _upload_media(s, path) -> str | None:
    """X API v2 media upload. Images go up in one request; GIFs use the chunked initialize/append/finalize
    flow. Returns the media id, or None (the post then goes out as text only)."""
    path = Path(path)
    try:
        data = path.read_bytes()
        if path.suffix.lower() != ".gif":
            r = s.post("https://api.x.com/2/media/upload", files={"media": (path.name, data, "image/png")},
                       data={"media_category": "tweet_image"}, timeout=60)
            if r.status_code < 300:
                return r.json()["data"]["id"]
            print(f"[x] image upload failed {r.status_code}: {r.text[:200]}")
            return None
        base = "https://api.x.com/2/media/upload"
        r = s.post(f"{base}/initialize", json={"media_type": "image/gif", "total_bytes": len(data),
                                                "media_category": "tweet_gif"}, timeout=30)
        if r.status_code >= 300:
            print(f"[x] gif init failed {r.status_code}: {r.text[:200]}")
            return None
        mid = r.json()["data"]["id"]
        chunk = 1024 * 1024
        for i in range(0, len(data), chunk):
            r = s.post(f"{base}/{mid}/append", files={"media": ("chunk", data[i:i + chunk], "application/octet-stream")},
                       data={"segment_index": str(i // chunk)}, timeout=60)
            if r.status_code >= 300:
                print(f"[x] gif append failed {r.status_code}: {r.text[:200]}")
                return None
        r = s.post(f"{base}/{mid}/finalize", timeout=30)
        if r.status_code >= 300:
            print(f"[x] gif finalize failed {r.status_code}: {r.text[:200]}")
            return None
        info = (r.json().get("data") or {}).get("processing_info")
        for _ in range(15):                        # GIFs are processed before they can be attached
            if not info or info.get("state") == "succeeded":
                return mid
            if info.get("state") == "failed":
                print(f"[x] gif processing failed: {info}")
                return None
            time.sleep(min(int(info.get("check_after_secs", 2)), 5))
            r = s.get(base, params={"command": "STATUS", "media_id": mid}, timeout=30)
            info = (r.json().get("data") or {}).get("processing_info") if r.status_code < 300 else None
        return mid
    except Exception as e:                         # noqa: BLE001 - never lose the post over a picture
        print(f"[x] media upload error: {e}")
        return None


def post_x(text: str, reply_to: str | None = None, quote: str | None = None, mirror: bool = True,
           media=None) -> str | None:
    """One public post: X API v2 (pay-per-use, about $0.015 for a post with no link), copied to the free
    Telegram channel. Returns the X post id.

    `media` is a picture or GIF path to attach. `quote` is ignored unless X_QUOTES is on: quote posts
    aren't available on the pay-per-use API, so results and recaps go out as their own posts."""
    if mirror:
        telegram.send_media(C.TG_FREE, media, text)
    s = _x_session()
    if not s:
        print("[x] keys not set; skipping auto-post. Text saved to data/posts/.")
        return None
    body = {"text": text}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}
    if quote and C.X_QUOTES:
        body["quote_tweet_id"] = quote
    if media:
        mid = _upload_media(s, media)
        if mid:
            body["media"] = {"media_ids": [mid]}
    r = s.post("https://api.x.com/2/tweets", json=body, timeout=30)
    if r.status_code >= 300:
        print(f"[x] post failed {r.status_code}: {r.text[:300]}")
        return None
    return r.json()["data"]["id"]


def post_thread(posts: list[str], media=None) -> list[str]:
    """Posts each item as a reply to the one before. Stops if one fails, so a thread is never half-broken.
    `media` goes on the first post."""
    telegram.send_media(C.TG_FREE, media, "\n\n".join(posts))   # one message on Telegram, a thread on X
    ids, prev = [], None
    for i, text in enumerate(posts):
        pid = post_x(text, reply_to=prev, mirror=False, media=media if i == 0 else None)
        if not pid:
            break
        ids.append(pid)
        prev = pid
    return ids


def _discord(url: str, text: str) -> bool:
    if not url:
        return False
    ok = True
    while text:                      # Discord messages max out at 2,000 characters
        cut = text[:1990]
        if len(text) > 1990 and "\n" in cut:
            cut = cut.rsplit("\n", 1)[0]
        r = requests.post(url, json={"content": cut}, timeout=30)
        ok = ok and r.status_code < 300
        text = text[len(cut):].lstrip("\n")
    return ok


def post_discord(text: str) -> bool:
    """Members: the paid Telegram VIP channel and/or the Discord members channel."""
    telegram.send(C.TG_VIP, text)
    return _discord(C.DISCORD_WEBHOOK_URL, text)


def post_owner(text: str) -> bool:
    """Just you: reply kits and alerts, in your private chat with the bot and/or a private Discord channel."""
    telegram.send(C.TG_OWNER, text)
    return _discord(C.DISCORD_OWNER_WEBHOOK_URL, text)


# ---------------------------------------------------------------- selling in the free Telegram channel
def premium_message(row: dict, market: list[str]) -> str:
    u = float(row["units"])
    return "\n".join([f"🎯 Premium Pick · {_emoji(row['sport'])} {C.SPORTS[row['sport']][0]}", "",
                      f"{row['label']} ({row['price_american']}) at {_book(row['book'])}", stake_line(u),
                      f"⏰ {_when(row['commence_time'], day=True)} · good to {row['play_to']}", stamped(row), ""]
                     + market)


def vip_teaser(board: list[dict]) -> str:
    sports = {}
    for b in board:
        k = C.SPORTS[b["sport"]][0]
        sports[k] = sports.get(k, 0) + 1
    mix = ", ".join(f"{n} {k}" for k, n in sorted(sports.items(), key=lambda x: -x[1]))
    top = max(board, key=lambda b: float(b["ev"]))
    when = parse_time(board[0]["posted_at"]).astimezone(TZ)
    return "\n".join([f"🔒 VIP board is in: {len(board)} play{'s' if len(board) > 1 else ''} today ({mix})", "",
                      f"Top play: {float(top['units']):g}u, {_emoji(top['sport'])} {C.SPORTS[top['sport']][0]}, "
                      f"{_when(top['commence_time'])}.",
                      f"🕒 Stamped {when:%-I:%M %p} ET and sealed. Fingerprints are on the public ledger, so nothing "
                      "can change after the fact.", "", "Unlock 👇"])


def vip_results(day: str, rows: list[dict], season: list[dict]) -> str:
    st, all_ = store.stats(rows), store.stats(season)
    lines = [(f"📊 VIP {day}: {rec(st)}, {units_money(st['profit'])} "
              + ("✅" if st["profit"] > 0 else "❌" if st["profit"] < 0 else "➖")), ""]
    for r in rows:
        mark = {"win": "✅", "loss": "❌", "push": "➖"}.get(r.get("result"), "⏳")
        lines.append(f"{mark} {r['label']} ({r['price_american']}) · {float(r['units']):g}u")
    lines += ["", f"VIP all-time: {record_line(all_)}"]
    if st["profit"] < 0:
        lines.append("Rough day, posted like every other. That's the deal.")
    lines += ["", "Get tomorrow's board 👇"]
    return "\n".join(lines)


def save_post(kind: str, date: str, text: str | list[str]):
    d = C.DATA / "posts"
    d.mkdir(parents=True, exist_ok=True)
    if isinstance(text, list):
        text = "\n\n---- next post in thread ----\n\n".join(text)
    f = d / f"{date}_{kind}.txt"
    with f.open("a") as fh:
        fh.write(text + "\n\n========\n\n")
