"""Matchup research for the write-up, done by Claude with live web search.

The pricing model picks the bet. This module only explains it: matchup advantages, form, injuries
and the biggest risk, found in today's search results. If anything fails, it returns None and the
post goes out with the betting-market reasons alone, so a research hiccup never blocks a pick.
"""
import json
import re
import requests
from . import config as C
from .model import parse_time

API = "https://api.anthropic.com/v1/messages"
BANNED = re.compile(r"\b(lock|locks|guarantee\w*|can'?t lose|sure thing|free money|easy money|max bet|hammer)\b", re.I)


def _when(row):
    from zoneinfo import ZoneInfo
    return parse_time(row["commence_time"]).astimezone(ZoneInfo(C.DISPLAY_TZ)).strftime("%A %-I:%M %p ET")


def _bet_words(row):
    sport = C.SPORTS.get(row["sport"], (row["sport"],))[0]
    if row["market"] == "totals":
        return f"the {row['side']} {row['point']} total points/runs/goals in {row['away']} at {row['home']}"
    if row["market"] == "spreads":
        return f"{row['side']} on the spread ({row['label']}) in {row['away']} at {row['home']}"
    who = C.SUBJECT.get(row["sport"], "team")
    vs = row["away"] if row["side"] == row["home"] else row["home"]
    return f"{row['side']} to win outright (the {who}) against {vs} ({sport})"


def _call(prompt: str, max_searches: int) -> tuple[str, list[str]]:
    headers = {"x-api-key": C.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    body = {
        "model": C.ANTHROPIC_MODEL, "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches,
                   "user_location": {"type": "approximate", "country": "US", "timezone": C.DISPLAY_TZ}}],
    }
    text, sources = "", []
    for _ in range(4):   # a long search turn can pause; send it back to continue
        r = requests.post(API, headers=headers, json=body, timeout=180)
        r.raise_for_status()
        data = r.json()
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
                for c in block.get("citations") or []:
                    if c.get("url") and c["url"] not in sources:
                        sources.append(c["url"])
        if data.get("stop_reason") != "pause_turn":
            break
        body["messages"] = body["messages"][:1] + [{"role": "assistant", "content": data["content"]}]
    return text, sources


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in research reply")
    return json.loads(m.group(0))


def _clean(s, limit):
    s = re.sub(r"\s+", " ", str(s or "")).strip().strip("•-– ").strip()
    s = re.sub(r"\[\d+\]|【[^】]*】", "", s).strip()          # stray citation markers
    if not s or BANNED.search(s):
        return ""
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return s


def pick_research(row: dict) -> dict | None:
    if not C.ANTHROPIC_API_KEY:
        return None
    who = C.SUBJECT.get(row["sport"], "team")
    prompt = f"""You are the research desk for a sports betting account that posts one play a day.
A pricing model already chose today's play. You are NOT choosing or second-guessing it. Your job is to
explain the matchup in plain words a casual fan understands.

Game: {row['away']} at {row['home']}, {_when(row)}.
The play: {_bet_words(row)}.

Use web search to find current, checkable facts that support this side: matchup advantages
({'reach, style, striking/grappling edge, record, camp and weigh-in news' if who == 'fighter'
else 'recent form, key players, injuries and lineups, rest and travel, pace or style matchups'}).
Then find the single biggest risk to this bet.

Rules:
- Only use facts you found in search results from the last 14 days, or current-season stats.
- Never invent a number. If you can't confirm something, leave it out.
- Never use the words lock, guarantee, sure thing, can't lose, hammer, or max bet.
- Don't mention odds, sportsbooks, or the model.
- Each point at most 95 characters. The risk at most 110 characters.

Reply with only this JSON, nothing else:
{{"points": ["...", "...", "..."], "risk": "...", "news": "latest injury/lineup note, or an empty string"}}"""
    try:
        text, sources = _call(prompt, C.RESEARCH_MAX_SEARCHES)
        d = _json(text)
        points = [p for p in (_clean(x, 100) for x in d.get("points", [])[:3]) if p]
        if not points:
            return None
        return {"points": points, "risk": _clean(d.get("risk"), 115),
                "news": _clean(d.get("news"), 120), "sources": sources[:6]}
    except Exception as e:   # research must never block the pick
        print(f"[research] skipped: {e}")
        return None


def pregame_news(row: dict) -> str:
    if not C.ANTHROPIC_API_KEY:
        return ""
    prompt = f"""{row['away']} at {row['home']} starts {_when(row)}, in about an hour.
Search for the very latest injury, lineup, scratch, goalie/pitcher confirmation, weather (outdoor games)
or weigh-in news for this game. Only report something confirmed in the last 24 hours.
Reply with only JSON: {{"news": "one line, at most 100 characters, or an empty string if nothing new"}}"""
    try:
        text, _ = _call(prompt, 2)
        return _clean(_json(text).get("news"), 105)
    except Exception as e:
        print(f"[research] pre-game news skipped: {e}")
        return ""
