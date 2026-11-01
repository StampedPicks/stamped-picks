"""The voice: short, human hooks on top of the posts, written fresh each time by Claude Haiku (cheap, no search).

The numbers, records and picks in a post never come from here: the templates build those from the ledger.
The writer only adds the line a person reads first, from facts we hand it, so it can't invent a stat. If it
fails or says something off-limits, the post goes out with the template's own first line.

Style it's told to copy: a sharp bettor talking to his followers, not a brand. Short sentences, an opinion,
a little attitude, one or two emojis at most. Think "The Packers are 3.5 point favorites. This line is
stupid 😂" or "Spent all week on this game. It's not what you'd expect." No hashtags (added later), no
"lock", "guarantee", "max bet", "free money".
"""
from __future__ import annotations
import re

import requests

from . import config as C, research

MODEL = getattr(C, "WRITER_MODEL", "claude-haiku-5-5")
STYLE = """You write the first line of posts for Stamped Picks, a sports betting account on X. Every pick is
stamped before the game and graded in public, so the voice is confident but honest.

Voice: a sharp bettor talking to his followers. Human, casual, a little cocky, never corporate. Short sentences.
Have a take. One or two emojis at most. Vary it: questions, hot takes, "nobody's talking about...", "this line
is stupid", "spent all week on this one", stories. Never sound like a press release or a bot.

Hard rules:
- Use ONLY the facts given below. Never invent a stat, injury, trend, record or quote.
- Never use: lock, guarantee, guaranteed, can't lose, sure thing, free money, max bet, hammer.
- No hashtags, no links, no @mentions.
- If told the side is SECRET, do not reveal or hint which side or which way on the total we're betting.
- At most {limit} characters. Reply with only the line(s), nothing else."""
BANNED = re.compile(r"\b(lock|guarantee\w*|can'?t lose|sure thing|free money|max bet|hammer\w*)\b|#|@|https?://", re.I)


def _ask(prompt: str, max_tokens: int = 1000) -> str:
    """Haiku first (cheap); if it errors or comes back empty, the main model writes it."""
    for model in (MODEL, C.ANTHROPIC_MODEL):
        try:
            r = requests.post(research.API, timeout=60, json={
                "model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
                headers={"x-api-key": C.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            if r.status_code >= 300:
                print(f"[writer] {model}: {r.status_code} {r.text[:160]}")
                continue
            data = r.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
            if text:
                return text
            print(f"[writer] {model}: empty reply (stop: {data.get('stop_reason')})")
        except Exception as e:                    # noqa: BLE001
            print(f"[writer] {model}: {e}")
    return ""


def hook(kind: str, facts: str, limit: int = 180, secret: bool = False) -> str | None:
    """A human first line for a post. kind: what the post is (e.g. 'premium teaser', 'card'). facts: everything
    the writer may use. Returns None if the writer is off or what it wrote breaks a rule."""
    if not C.ANTHROPIC_API_KEY or not getattr(C, "WRITER", True):
        return None
    prompt = (STYLE.format(limit=limit) + f"\n\nPost type: {kind}\n"
              + ("The side we're betting is SECRET.\n" if secret else "") + f"Facts:\n{facts}\n\nWrite it now.")
    try:
        text = _ask(prompt).strip().strip('"').strip()
    except Exception as e:                        # noqa: BLE001 - a hook is never worth a missed post
        print(f"[writer] skipped: {e}")
        return None
    if not text or len(text) > limit + 20 or BANNED.search(text):
        print(f"[writer] rejected: {text[:120]!r}")
        return None
    return text
