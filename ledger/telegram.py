"""Telegram delivery through the Bot API (free). The bot only posts into your channels; payments happen
on Whop's checkout page, opened by URL buttons."""
import re
import requests
from . import config as C

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000                      # Telegram allows 4,096 characters per message


def clean(text: str) -> str:
    """Our Discord-style **bold** and __underline__ markers would show as symbols in plain Telegram text."""
    return re.sub(r"(\*\*|__)", "", text)


def _call(method: str, **body):
    r = requests.post(API.format(token=C.TELEGRAM_BOT_TOKEN, method=method), json=body, timeout=30)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not data.get("ok"):
        print(f"[telegram] {method} failed: {str(data)[:300] or r.status_code}")
        return None
    return data["result"]


def send(chat: str, text: str, buttons: list[list[dict]] | None = None, pin: bool = False) -> int | None:
    """Sends text (split if long). Buttons and pin apply to the last part. Returns the last message id."""
    if not (C.TELEGRAM_BOT_TOKEN and chat and text):
        return None
    text, mid = clean(text), None
    parts = []
    while text:
        cut = text[:LIMIT]
        if len(text) > LIMIT and "\n" in cut:
            cut = cut.rsplit("\n", 1)[0]
        parts.append(cut)
        text = text[len(cut):].lstrip("\n")
    for i, part in enumerate(parts):
        body = {"chat_id": chat, "text": part, "disable_web_page_preview": True}
        if buttons and i == len(parts) - 1:
            body["reply_markup"] = {"inline_keyboard": buttons}
        res = _call("sendMessage", **body)
        mid = res["message_id"] if res else mid
    if pin and mid:
        _call("pinChatMessage", chat_id=chat, message_id=mid, disable_notification=True)
    return mid


def send_media(chat: str, path, text: str = "", buttons=None) -> int | None:
    """A photo (.png/.jpg) or an animation (.gif) with the text as its caption. Captions max out at 1,024
    characters, so longer text goes in a message right after. Falls back to text only if the upload fails."""
    if not (C.TELEGRAM_BOT_TOKEN and chat):
        return None
    if not path:
        return send(chat, text, buttons)
    text = clean(text or "")
    gif = str(path).lower().endswith(".gif")
    method, field = ("sendAnimation", "animation") if gif else ("sendPhoto", "photo")
    caption = text if len(text) <= 1024 else ""
    data = {"chat_id": chat}
    if caption:
        data["caption"] = caption
    if buttons and caption:
        import json
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        with open(path, "rb") as fh:
            r = requests.post(API.format(token=C.TELEGRAM_BOT_TOKEN, method=method), data=data,
                              files={field: fh}, timeout=60)
        res = r.json()
    except Exception as e:                     # noqa: BLE001 - never lose the post over a picture
        print(f"[telegram] {method} failed: {e}")
        res = {}
    if not res.get("ok"):
        print(f"[telegram] {method} failed: {str(res)[:300]}")
        return send(chat, text, buttons)
    mid = res["result"]["message_id"]
    if text and not caption:
        mid = send(chat, text, buttons) or mid
    return mid


def sales_buttons(only: tuple = ()) -> list[list[dict]]:
    """Checkout buttons for the options that have a Whop link, two per row."""
    items = [{"text": f"{label} · {price}", "url": url} for key, label, price, url in C.SALES
             if url and (not only or key in only)]
    return [items[i:i + 2] for i in range(0, len(items), 2)]


def selling() -> bool:
    return bool(C.TELEGRAM_BOT_TOKEN and C.TG_FREE and (any(url for *_, url in C.SALES) or C.OWNER_TG_USERNAME))


def chats() -> list[tuple]:
    """Chats the bot has seen recently (post something in each channel first)."""
    seen = {}
    for u in _call("getUpdates") or []:
        for key in ("channel_post", "message", "my_chat_member"):
            chat = (u.get(key) or {}).get("chat")
            if chat:
                seen[chat["id"]] = (chat["id"], chat.get("type"), chat.get("title") or chat.get("username"))
    return list(seen.values())
