"""Telegram side, against a fake Telegram: free/VIP/Premium/owner routing, buy buttons, VIP teasers and
results, and the crypto membership flow (/grant → join → renewal warning → removal), all through the real
5-minute scheduler. Run: python -m tests.test_telegram"""
import io
import contextlib
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ledger import config as C, telegram, research, social, store
from tests.simulate import simulate

sent, calls, inbox = [], [], []        # messages sent, every API call, updates waiting for the bot
links = {}


def fake_call(method, **body):
    calls.append((method, body))
    if method == "sendMessage":
        sent.append(body)
        return {"message_id": len(sent)}
    if method == "createChatInviteLink":
        link = f"https://t.me/+fake{len(links) + 1}"
        links[link] = body
        return {"invite_link": link}
    if method == "getUpdates":          # like Telegram: ids are given in arrival order, kept until confirmed
        for u in sorted(inbox, key=lambda u: u["_at"]):
            if u["_at"] <= clock["t"] and "update_id" not in u:
                upd["n"] += 1
                u["update_id"] = upd["n"]
        return [u for u in inbox if "update_id" in u and u["update_id"] >= body.get("offset", 0)]
    return True


clock = {"t": None}
upd = {"n": 0}


def queue(at, message=None, join_link=None, chat=None):
    u = {"_at": at}
    if message:
        u["message"] = {"chat": {"id": int(C.TG_OWNER)}, "text": message}
    if join_link:
        u["chat_member"] = {"chat": {"id": chat}, "invite_link": {"invite_link": join_link},
                            "new_chat_member": {"status": "member",
                                                "user": {"id": 777, "username": "mike_bets", "first_name": "Mike"}}}
    inbox.append(u)


def main():
    root = Path("tg_out").resolve()
    C.TELEGRAM_BOT_TOKEN, C.TG_FREE, C.TG_VIP, C.TG_PREMIUM, C.TG_OWNER = "t", "@stampedfree", "-1001", "-1002", "4242"
    C.OWNER_TG_USERNAME = "StampedHQ"
    C.MIN_EV = 0.005        # this test is about delivery, so let thin simulated slates still fill a VIP board
    C.SALES = [(k, l, p, f"https://whop.com/stamped/{k}") for k, l, p, _ in C.SALES]
    telegram._call = fake_call
    C.GRAPHICS = False      # media uploads would hit the real Bot API; graphics are covered in test_posts
    social.post_x = (lambda f: (lambda text, reply_to=None, quote=None, mirror=True, media=None:
                                (telegram.send(C.TG_FREE, text) if mirror else None, "x" + str(len(sent)))[1]))(None)
    research.pick_research = lambda r: None
    research.pregame_news = lambda r: ""
    start = datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc)
    queue(start + timedelta(hours=10), message="/grant 3 $15 USDC trial")
    queue(start + timedelta(hours=12), message="/members")
    queue(start + timedelta(days=6), message="/sales")
    queue(start + timedelta(days=6, minutes=5), message="/menu")
    queue(start + timedelta(days=6, minutes=10), message="/bogus")

    def before(t):
        clock["t"] = t
        # once the grant link exists, Mike joins 20 minutes later
        if links and not any(u.get("chat_member") for u in inbox):
            queue(t + timedelta(minutes=20), join_link=next(iter(links)), chat=int(C.TG_VIP))

    with contextlib.redirect_stdout(io.StringIO()):
        simulate(9, start, root, seed=5, before_tick=before)
    by_chat = {}
    for m in sent:
        by_chat.setdefault(str(m["chat_id"]), []).append(m)
    free, vip, prem, owner = (by_chat.get(c, []) for c in (C.TG_FREE, C.TG_VIP, C.TG_PREMIUM, C.TG_OWNER))
    t = lambda ms, s: [m for m in ms if s in m["text"]]

    assert t(free, "Daily Play #"), "free channel should mirror the public picks"
    assert t(free, "WIN") or t(free, "CASH") or t(free, "✅"), "free channel should get results"
    teasers = t(free, "VIP board is in")
    assert teasers and all("reply_markup" in m for m in teasers), "teasers need buy buttons"
    btn = [b["text"] for row in teasers[0]["reply_markup"]["inline_keyboard"] for b in row]
    assert any("Premium Pick" in b for b in btn) and any("crypto" in b for b in btn), btn
    vres = t(free, "📊 VIP ")
    assert vres and all("reply_markup" in m for m in vres), "VIP results go to the free channel with buttons"
    assert t(vip, "Members Board"), "VIP gets the board"
    assert prem and all(m["text"].startswith("🎯 Premium Pick") for m in prem), "Premium channel gets only the top play"
    assert t(owner, "Reply kit"), "you get the reply kits"
    assert t(owner, "Grant #1") and "https://t.me/+fake1" in t(owner, "Grant #1")[0]["text"]
    assert links["https://t.me/+fake1"]["member_limit"] == 1, "invite links must be single-use"
    assert t(owner, "joined (grant #1"), "join should be detected"
    assert t(owner, "1 active crypto member"), "/members"
    assert t(owner, "Good time to DM them about renewing"), "renewal warning"
    assert t(owner, "ended. Removed"), "removal at expiry"
    bans = [b for m, b in calls if m == "banChatMember" and b["user_id"] == 777]
    unbans = [b for m, b in calls if m == "unbanChatMember" and b["user_id"] == 777]
    assert bans and unbans, "expired member is removed but can rejoin later"
    assert t(owner, "Crypto grants, last 30 days: 1"), "/sales"
    menus = [b for m, b in calls if m == "pinChatMessage"]
    assert menus and t(free, "We never DM you first"), "/menu posts and pins"
    assert t(owner, "/grant month"), "unknown commands get the help text"
    raw = (root / "data" / "members.enc").read_bytes()
    assert b"mike" not in raw and b"USDC" not in raw, "member records must be encrypted"
    print(f"free channel {len(free)} msgs · VIP {len(vip)} · Premium {len(prem)} · you {len(owner)}")
    for label, m in (("VIP teaser (free channel)", teasers[0]), ("VIP results (free channel)", vres[0]),
                     ("Premium Pick", prem[0]), ("/grant reply", t(owner, "Grant #1")[0]),
                     ("pinned menu", t(free, "We never DM you first")[0])):
        rows = m.get("reply_markup", {}).get("inline_keyboard", [])
        print(f"\n=== {label} ===\n{m['text']}" + ("".join("\n[ " + " ] [ ".join(b["text"] for b in r) + " ]" for r in rows)))
    shutil.rmtree(root, ignore_errors=True)
    print("\nall Telegram checks passed")


if __name__ == "__main__":
    main()
