"""Reply-to-win: give the premium pick away to a few people who reply, before it's public.

  1. With each premium pick, a standalone post: "Reply 🔒 and we'll DM today's premium play to 3 of you at
     <time>." Replies push the post (and the profile) out to more people.
  2. At draw time (about 75 minutes before the game) we read the replies, pick winners at random (one entry
     per account, never us) and DM each the pick.
  3. A short follow-up under the giveaway post: "Winners picked, check your DMs." No @-tags (automated
     mentions break X's rules), and we only DM people who asked by replying.

DMs need the X app set to "Read and write and Direct message" (then regenerate the access token and
secret). If a DM fails, the winners' handles and the pick go to the owner's Telegram so they can be sent
by hand, and the public follow-up only says what actually happened.
"""
from __future__ import annotations
import random
from datetime import timedelta

from . import config as C, social, store, telegram
from .model import parse_time

WINNERS = int(getattr(C, "GIVEAWAY_WINNERS", 3))


def schedule(state, now, row, post, when_text: str):
    """Posts the giveaway for a sealed premium pick and remembers when to draw."""
    if not getattr(C, "GIVEAWAY", True):
        return
    start = parse_time(row["commence_time"])
    draw = max(now + timedelta(minutes=45), start - timedelta(minutes=75))
    if draw >= start - timedelta(minutes=10):
        return                                    # not enough time before the game
    text = "\n".join([
        f"🎁 Giving away today's premium play to {WINNERS} of you, before it goes public.",
        "",
        f"Reply 🔒 to this post. We'll DM {WINNERS} random replies at {draw.astimezone(social.TZ):%-I:%M %p} ET.",
        "",
        "Follow us so the DM can reach you. One entry per account.",
    ])
    text = social.tagged(text, "#FreePicks #SportsBetting #GamblingTwitter")
    social.save_post("giveaway", row["date"], text)
    pid = social.post_x(text, mirror=False) if post else None
    if pid:
        state.setdefault("giveaways", {})[pid] = {"pick_no": row["pick_no"], "draw": draw.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                  "done": False, "when": when_text}


def _replies(s, pid, me) -> list[tuple[str, str]]:
    r = s.get("https://api.x.com/2/tweets/search/recent", timeout=30, params={
        "query": f"conversation_id:{pid} is:reply", "max_results": 100, "expansions": "author_id",
        "user.fields": "username"})
    if r.status_code >= 300:
        print(f"[giveaway] replies failed {r.status_code}: {r.text[:200]}")
        return []
    users = {u["id"]: u.get("username", "") for u in (r.json().get("includes") or {}).get("users", [])}
    seen, out = set(), []
    for t in r.json().get("data", []) or []:
        a = t.get("author_id")
        if a and a != me and a not in seen:
            seen.add(a)
            out.append((a, users.get(a, "")))
    return out


def _dm(s, uid, text) -> bool:
    r = s.post(f"https://api.x.com/2/dm_conversations/with/{uid}/messages", json={"text": text}, timeout=30)
    if r.status_code >= 300:
        print(f"[giveaway] DM failed {r.status_code}: {r.text[:200]}")
        return False
    return True


def run(now, state, post, member_text):
    """Draws any giveaway that's due. member_text(row) builds the DM for the sealed pick."""
    for pid, g in (state.get("giveaways") or {}).items():
        if g.get("done") or now < parse_time(g["draw"]):
            continue
        g["done"] = True
        row = next((r for r in store.vault_read() + store.read(store.LEDGER) if r["pick_no"] == g["pick_no"]), None)
        s = social._x_session()
        if not row or not s or not post:
            continue
        me = (s.get("https://api.x.com/2/users/me", timeout=30).json().get("data") or {}).get("id")
        entries = _replies(s, pid, me)
        if not entries:
            print(f"[giveaway] {pid}: no replies")
            continue
        winners = random.sample(entries, min(WINNERS, len(entries)))
        text = "🎁 You won today's Stamped Picks premium play:\n\n" + member_text(row)
        sent = [w for w in winners if _dm(s, w[0], text)]
        missed = [w for w in winners if w not in sent]
        print(f"[giveaway] {pid}: {len(entries)} entries, DM'd {len(sent)}, missed {len(missed)}")
        if missed and C.TG_OWNER:
            telegram.send(C.TG_OWNER, "Giveaway winners to DM by hand: " + ", ".join("@" + w[1] for w in missed)
                          + "\n\n" + text)
        if sent:
            social.post_x(f"✅ {len(sent)} winner{'s' if len(sent) > 1 else ''} picked from {len(entries)} replies. "
                          "Check your DMs 🔒\n\nDidn't win? Premium members get it every day.", reply_to=pid,
                          mirror=False)
        elif missed and C.TG_OWNER:
            social.post_x(f"✅ Winners picked from {len(entries)} replies. We'll be in your DMs shortly 🔒",
                          reply_to=pid, mirror=False)
    # forget giveaways older than 2 days
    gs = state.get("giveaways") or {}
    for pid in [p for p, g in gs.items() if g.get("done") and parse_time(g["draw"]) < now - timedelta(days=2)]:
        gs.pop(pid, None)
