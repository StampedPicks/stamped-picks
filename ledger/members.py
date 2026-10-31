"""Crypto memberships sold by DM, managed from your private chat with the bot.

You confirm the payment yourself (check the transaction on the blockchain explorer), then send the bot:
  /grant month            30 days of VIP (also: pick, day, week, year, or a number of days like 45)
  /grant month $45 USDC @mike   anything after the plan is saved as a note for your records
  /members                who's active, days left, and unused invites
  /extend 7 14            add 14 days to grant #7
  /revoke 7               remove grant #7 now
  /sales                  last 30 days of crypto grants, by plan
  /settle 12 win          grade play #12 by hand (fights the live feed missed)
  /menu                   post and pin the buy menu in the free channel
  /status                 today's plays and the record
  /help

The bot answers on its next run (every 5 minutes). Member records are encrypted with BOARD_KEY before
they're saved, so names and payments never appear in the public repo."""
import json
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
from . import config as C, telegram, store

FILE = lambda: C.DATA / "members.enc"


def load() -> dict:
    f = FILE()
    if not f.exists() or not C.BOARD_KEY:
        return {"grants": [], "offset": 0}
    return json.loads(Fernet(C.BOARD_KEY.encode()).decrypt(f.read_bytes()))


def save(db: dict):
    if not C.BOARD_KEY:
        return
    FILE().parent.mkdir(parents=True, exist_ok=True)
    FILE().write_bytes(Fernet(C.BOARD_KEY.encode()).encrypt(json.dumps(db).encode()))


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _chat(kind):
    return C.TG_PREMIUM if kind == "premium" else C.TG_VIP


def _reply(text):
    telegram.send(C.TG_OWNER, text)


def _who(g):
    return f"@{g['username']}" if g.get("username") else (g.get("name") or f"grant #{g['id']}")


# ---------------------------------------------------------------- commands
def cmd_grant(db, now, args):
    if not args:
        return "Usage: /grant month (or pick, day, week, year, or a number of days). Add a note after it."
    plan = args[0].lower().rstrip("s")
    plan = {"monthly": "month", "weekly": "week", "yearly": "year", "daily": "day", "single": "pick"}.get(plan, plan)
    if plan in C.PLANS:
        days, kind = C.PLANS[plan]
    elif plan.rstrip("d").isdigit():
        days, kind, plan = int(plan.rstrip("d")), "vip", f"{int(plan.rstrip('d'))}-day pass"
    else:
        return f"Unknown plan '{args[0]}'. Use pick, day, week, month, year, or a number of days."
    chat = _chat(kind)
    if not chat:
        return f"The {'Premium Pick' if kind == 'premium' else 'VIP'} channel isn't set up yet (TG_{kind.upper()}_CHAT)."
    gid = max([g["id"] for g in db["grants"]] or [0]) + 1
    link = telegram._call("createChatInviteLink", chat_id=chat, member_limit=1, name=f"grant {gid}",
                          expire_date=int((now + timedelta(hours=C.INVITE_HOURS)).timestamp()))
    if not link:
        return "Couldn't make an invite link. Is the bot an admin in that channel with 'Invite users' on?"
    db["grants"].append({"id": gid, "plan": plan, "days": days, "kind": kind, "note": " ".join(args[1:]),
                         "created": _iso(now), "link": link["invite_link"], "status": "pending"})
    return (f"✅ Grant #{gid}: {plan} ({days} day{'s' if days != 1 else ''} from when they join).\n\n"
            f"Forward them this link. It works once and expires in {C.INVITE_HOURS}h:\n{link['invite_link']}")


def cmd_members(db, now, args):
    act = [g for g in db["grants"] if g["status"] == "active"]
    pend = [g for g in db["grants"] if g["status"] == "pending"]
    lines = [f"👥 {len(act)} active crypto member{'s' if len(act) != 1 else ''}"]
    for g in sorted(act, key=lambda g: g["expires"]):
        left = (_t(g["expires"]) - now).days
        lines.append(f"#{g['id']} {_who(g)} · {g['plan']} · {left}d left" + (f" · {g['note']}" if g["note"] else ""))
    if pend:
        lines += ["", f"⏳ {len(pend)} invite{'s' if len(pend) != 1 else ''} not used yet: "
                  + ", ".join(f"#{g['id']} ({g['plan']})" for g in pend)]
    return "\n".join(lines)


def _remove(g, now):
    chat = _chat(g["kind"])
    if g.get("user_id"):
        telegram._call("banChatMember", chat_id=chat, user_id=g["user_id"], until_date=int(now.timestamp()) + 60)
        telegram._call("unbanChatMember", chat_id=chat, user_id=g["user_id"], only_if_banned=True)
    if g.get("link"):
        telegram._call("revokeChatInviteLink", chat_id=chat, invite_link=g["link"])


def cmd_revoke(db, now, args):
    g = next((g for g in db["grants"] if args and str(g["id"]) == args[0].lstrip("#")), None)
    if not g:
        return "Usage: /revoke 7 (the grant number from /members)"
    _remove(g, now)
    g["status"] = "revoked"
    return f"Removed {_who(g)} (grant #{g['id']})."


def cmd_extend(db, now, args):
    g = next((g for g in db["grants"] if len(args) == 2 and str(g["id"]) == args[0].lstrip("#")), None)
    if not g or not args[1].rstrip("d").isdigit():
        return "Usage: /extend 7 14 (grant number, extra days)"
    extra = int(args[1].rstrip("d"))
    if g["status"] == "active":
        g["expires"] = _iso(_t(g["expires"]) + timedelta(days=extra))
        g.pop("warned", None)
        return f"Grant #{g['id']} ({_who(g)}) now ends {_t(g['expires']):%b %-d}."
    g["days"] += extra
    return f"Grant #{g['id']} will now run {g['days']} days once they join."


def cmd_sales(db, now, args):
    since = now - timedelta(days=30)
    recent = [g for g in db["grants"] if _t(g["created"]) >= since and g["status"] != "pending"]
    by = {}
    for g in recent:
        by.setdefault(g["plan"], []).append(g)
    lines = [f"💸 Crypto grants, last 30 days: {len(recent)}"]
    for plan, gs in sorted(by.items()):
        lines.append(f"• {plan}: {len(gs)}")
        lines += [f"   #{g['id']} {_who(g)} {g['note']}".rstrip() for g in gs]
    return "\n".join(lines)


def menu_text() -> str:
    lines = ["💳 Get Stamped Picks VIP", "",
             "Free here: the Daily Play, NFL and UFC cards, and every result.",
             "VIP: the full Members Board every day, stamped before game time and graded in public.", ""]
    for key, label, price, url in C.SALES:
        if url:
            days, kind = C.PLANS[key]
            what = "today's top VIP play" if key == "pick" else f"{days} day{'s' if days > 1 else ''} of VIP"
            lines.append(f"{label} {price}: {what}")
    if C.OWNER_TG_USERNAME:
        lines += ["", f"💸 Prefer crypto? DM @{C.OWNER_TG_USERNAME}. Same plans, same prices."]
    lines += ["", f"⚠️ We never DM you first. Only pay through these buttons"
              + (f" or @{C.OWNER_TG_USERNAME}" if C.OWNER_TG_USERNAME else "")
              + ". Anyone else asking for payment is a scammer.", "", "21+. Bet what you can afford."]
    return "\n".join(lines)


def menu_buttons() -> list:
    rows = telegram.sales_buttons()
    if C.OWNER_TG_USERNAME:
        rows.append([{"text": "💸 Pay with crypto", "url": f"https://t.me/{C.OWNER_TG_USERNAME}"}])
    return rows


def cmd_menu(db, now, args):
    mid = telegram.send(C.TG_FREE, menu_text(), menu_buttons(), pin=True)
    return "Menu posted and pinned in the free channel." if mid else "Couldn't post the menu (check TG_FREE_CHAT)."


def cmd_status(db, now, args):
    rows = store.read(store.LEDGER)
    st = store.stats(rows)
    open_ = [r for r in rows if not r.get("result")]
    lines = [f"📊 {st['wins']}-{st['losses']}-{st['pushes']} · {st['profit']:+.2f}u · {len(rows)} plays logged"]
    if open_:
        lines += ["", "Open plays:"] + [f"#{r['pick_no']} {r['label']} {r['price_american']} · {r['units']}u" for r in open_]
    return "\n".join(lines)


HELP = __doc__.split("\n\n")[1]
COMMANDS = {"grant": cmd_grant, "members": cmd_members, "revoke": cmd_revoke, "extend": cmd_extend,
            "sales": cmd_sales, "menu": cmd_menu, "status": cmd_status}


# ---------------------------------------------------------------- each run
def process(now) -> list[tuple]:
    """Reads new Telegram updates: your commands and people joining through granted links. Removes
    members whose time is up. Returns actions run.py must do itself (e.g. settle)."""
    if not (C.TELEGRAM_BOT_TOKEN and C.TG_OWNER):
        return []
    if not C.BOARD_KEY:
        print("[members] BOARD_KEY not set; crypto memberships are off")
        return []
    db, actions = load(), []
    body = {"allowed_updates": ["message", "chat_member"], "timeout": 0}
    if db.get("offset"):
        body["offset"] = db["offset"] + 1
    updates = telegram._call("getUpdates", **body) or []
    for u in updates:
        db["offset"] = u["update_id"]
        m = u.get("message") or {}
        if m and str(m.get("chat", {}).get("id")) == str(C.TG_OWNER) and m.get("text", "").startswith("/"):
            word, *args = m["text"].split()
            name = word[1:].split("@")[0].lower()
            if name == "settle":
                actions.append(("settle", args))
            elif name in COMMANDS:
                _reply(COMMANDS[name](db, now, args))
            else:
                _reply(HELP)
        cm = u.get("chat_member")
        if cm and (cm.get("new_chat_member") or {}).get("status") == "member":
            link = (cm.get("invite_link") or {}).get("invite_link", "")
            g = next((g for g in db["grants"] if g["status"] == "pending" and g["link"] == link), None)
            if g:
                user = cm["new_chat_member"]["user"]
                g.update(status="active", user_id=user["id"], username=user.get("username", ""),
                         name=" ".join(x for x in (user.get("first_name"), user.get("last_name")) if x),
                         joined=_iso(now), expires=_iso(now + timedelta(days=g["days"])))
                _reply(f"🎉 {_who(g)} joined (grant #{g['id']}, {g['plan']}). Access ends {_t(g['expires']):%b %-d}.")
    for g in db["grants"]:
        if g["status"] == "active":
            left = _t(g["expires"]) - now
            if left <= timedelta(0):
                _remove(g, now)
                g["status"] = "expired"
                _reply(f"⌛ {_who(g)}'s {g['plan']} ended. Removed from the channel (grant #{g['id']}).")
            elif left <= timedelta(days=C.RENEWAL_WARNING_DAYS) and not g.get("warned"):
                g["warned"] = True
                _reply(f"🔔 {_who(g)}'s {g['plan']} ends {_t(g['expires']):%b %-d}. Good time to DM them about renewing.")
        elif g["status"] == "pending" and now - _t(g["created"]) > timedelta(hours=C.INVITE_HOURS):
            g["status"] = "unused"
            _reply(f"Grant #{g['id']} ({g['plan']}) was never used and its link expired. /grant again if needed.")
    save(db)
    return actions
