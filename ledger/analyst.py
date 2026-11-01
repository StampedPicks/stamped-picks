"""The analyst: a second opinion on the model's best market edges, with live research, before anything posts.

The pricing model finds where a sportsbook pays more than the sharp market says a bet is worth. That's the
edge, and it's measurable. But an edge on paper can be a stale line, a missed injury, or a weather call the
market already knows about. So the top candidates go to Claude with web search, which checks each one like a
handicapper would:

  * injuries and lineups (who's in, who's out, who's questionable)
  * weather for outdoor games (wind is the big one for totals)
  * matchup: pace, pressure vs protection, run game vs run defense, red-zone and third-down form
  * line movement and betting splits (share of bets vs share of money) where reported
  * what respected handicappers and public cappers are saying about the game, as one input

It returns a verdict per candidate: bet or pass, a 1-10 confidence, the headline angle, and the reasons. The
final score = market edge x analyst confidence. A "pass" never posts, however big the edge looks. If the
analyst can't run (no key, outage), the model's market reasons still go out alone, so a pick is never blocked.
"""
from __future__ import annotations
import json
import re
from zoneinfo import ZoneInfo

from . import config as C, research, sim
from .model import parse_time, fmt_american

TZ = ZoneInfo(C.DISPLAY_TZ)
MAX_CANDIDATES = 8
MAX_SEARCHES = 20
GAMES_PER_CALL = 5         # all-games research: games per research call


def _bet_text(p) -> str:
    if p.market == "totals":
        return f"{p.side.upper()} {p.point:g} total points ({p.away} at {p.home})"
    if p.market == "spreads":
        return f"{p.label} ({p.away} at {p.home})"
    return f"{p.side} to win outright ({p.away} at {p.home})"


def _shortlist(cands, n=MAX_CANDIDATES) -> list:
    """Best candidates by model score, at most two per game, so the analyst sees a spread of games and bet types."""
    per_game, out = {}, []
    for p in sorted(cands, key=lambda p: p.score, reverse=True):
        if per_game.get(p.event_id, 0) >= 2:
            continue
        per_game[p.event_id] = per_game.get(p.event_id, 0) + 1
        out.append(p)
        if len(out) == n:
            break
    return out


def _prompt(sport: str, shortlist, extra: str = "") -> str:
    league = C.SPORTS.get(sport, ("sports",))[0] if sport else "multi-sport"
    lines = []
    for i, p in enumerate(shortlist, 1):
        when = parse_time(p.commence_time).astimezone(TZ).strftime("%a %-I:%M %p ET")
        move = (" Sharp line has moved toward this side." if p.movement > 0.005 else
                " Sharp line has moved against this side." if p.movement < -0.005 else "")
        conv = f" (sharp books hang {p.sharp_point:g}; priced across the difference)" if p.sharp_point is not None else ""
        lines.append(f"{i}. {_bet_text(p)} at {C.BET_BOOKS.get(p.book, p.book)} {fmt_american(p.price)}, {when}. "
                     f"Sharp-market fair chance {p.fair_prob:.1%} (fair odds {fmt_american(1 / p.fair_prob)}), "
                     f"edge {p.ev:+.1%}{conv}.{move}{sim.prompt_line(p)}")
    return f"""You are the lead handicapper for a {league} betting account whose whole brand is credibility: every pick is
stamped before the game and graded in public. A pricing model compared sportsbook prices to the sharp market
and flagged these candidate bets for today:

{chr(10).join(lines)}

For EACH candidate, use web search to check it like a sharp handicapper would. Cover every angle you can confirm:
- WHO IS PLAYING: official injury report and inactives; every questionable player, how limited he was in
  practice (DNP / limited / full), and how much he is likely to play (snap share or minutes) if active; QB and
  starting-lineup changes; offensive-line and secondary injuries; players returning from injury
- WEATHER AND STADIUM: dome or outdoor, forecast wind (15+ mph matters), rain/snow/cold, surface (grass/turf),
  altitude, and how this stadium has played for totals
- HOME FIELD AND CROWD: home/road form, crowd noise and travel crowd, time-zone travel, short week or rest
  advantage, bye weeks, back-to-backs, divisional familiarity, revenge or letdown spots, motivation and standings
- THE MATCHUP: pace and play volume, pass rush vs pass protection, run game vs run defense, explosive plays,
  red-zone and third-down efficiency, turnovers, special teams, coaching tendencies (4th downs, tempo)
- RECENT FORM: last 3-5 games for both teams, scoring trends, and how the totals/spreads have landed lately
- MARKET: line movement since open and why, betting splits (percent of bets vs percent of money), steam moves
- THE CROWD ON X: what respected handicappers and well-known public cappers are saying about this game

{extra}
Then decide: does the football support the market edge, or is there a reason the edge is fake (stale line,
injury the price already reflects, bad weather for an Over, etc.)? Say "pass" only when a SPECIFIC fact you found
cuts against the bet (name it in risk). Not finding news is NOT a reason to pass: the market edge and the
simulation stand on their own, so with nothing found either way say "bet" with confidence 5.

Work fast: keep your notes between searches to a line or two, and do not write a report. When the research is
done, reply with the JSON right away.

Rules:
- Only state facts you found in today's search results or current-season stats. Never invent a number.
- Never use the words lock, guarantee, sure thing, can't lose, hammer, or max bet.
- Don't mention sportsbooks or "the model" in angle/reasons; write like a handicapper talking to fans.
- angle: one punchy line, at most 110 characters, the core reason to like the bet.
- reasons: 3 to 5 specific, checkable points, each at most 180 characters.
- context: up to 6 lines covering injuries (with likely playing time), weather/stadium, rest/travel, splits and
  line movement, and capper takes, each at most 160 characters (empty list if nothing found).
- risk: the single biggest thing that beats this bet, at most 140 characters.
- confidence: 1-10, how strongly the research supports the bet (5 = neutral, research adds nothing either way).

Reply with only this JSON, nothing else:
{{"picks": [{{"n": 1, "verdict": "bet" or "pass", "confidence": 7, "angle": "...", "reasons": ["..."],
"context": ["..."], "risk": "..."}}]}}"""


def _batches(cands, all_games: bool, n: int) -> list[list]:
    """Which candidates the analyst researches, in batches of one research call each.
    Default: the best n candidates (at most two per game), one call. all_games: every game in the slate gets
    researched (its two best options), GAMES_PER_CALL games per call, so nothing we post goes out unchecked."""
    if not all_games:
        return [_shortlist(cands, n)]
    games = {}
    for p in sorted(cands, key=lambda p: p.score, reverse=True):
        if len(games.setdefault(p.event_id, [])) < 2:
            games[p.event_id].append(p)
    order = list(games.values())                   # best games first
    return [[p for g in order[i:i + GAMES_PER_CALL] for p in g] for i in range(0, len(order), GAMES_PER_CALL)]


def _research(sport, shortlist, extra, searches):
    prompt = _prompt(sport, shortlist, extra)
    text, sources = research._call(prompt, searches, max_tokens=16000)
    try:
        return research._json(text), sources
    except Exception:                              # noqa: BLE001 - long search turns sometimes end without the JSON
        print("[analyst] no verdict JSON after research; asking for it from the notes")
        follow = (prompt + "\n\nHere are the research notes you already gathered:\n\n" + text[-20000:]
                  + "\n\nDo not search again. Using only these notes, reply now with ONLY the JSON described above. "
                  "Where the notes have nothing on a candidate, give it verdict bet, confidence 5, and leave its "
                  "context empty; pass only on a specific fact in the notes.")
        text2, _ = research._call(follow, 1, max_tokens=8000)
        return research._json(text2), sources


def review(sport: str, cands: list, n: int = MAX_CANDIDATES, extra: str = "", searches: int = MAX_SEARCHES,
           all_games: bool = False) -> list:
    """Returns the candidates with analyst verdicts applied: passes removed, scores re-weighted by confidence,
    and p.analysis filled in. Candidates not researched are kept but ranked lower. If the analyst can't run,
    returns cands unchanged."""
    if not C.ANTHROPIC_API_KEY or not cands:
        return cands
    passed, shortlisted = set(), []
    for shortlist in _batches(cands, all_games, n):
        shortlisted += shortlist
        try:
            data, sources = _research(sport, shortlist, extra, searches)
        except Exception as e:                    # noqa: BLE001 - research never blocks the pick
            print(f"[analyst] batch skipped: {e}")
            continue
        verdicts = {int(v.get("n", 0)): v for v in data.get("picks", []) if isinstance(v, dict)}
        for i, p in enumerate(shortlist, 1):
            v = verdicts.get(i)
            if not v:
                continue
            conf = max(1, min(10, int(v.get("confidence") or 5)))
            reasons = [r for r in (research._clean(x, 185) for x in (v.get("reasons") or [])[:5]) if r]
            p.analysis = {
                "verdict": "pass" if str(v.get("verdict", "")).lower().startswith("pass") else "bet",
                "confidence": conf,
                "angle": research._clean(v.get("angle"), 115),
                "reasons": reasons,
                "context": [c for c in (research._clean(x, 165) for x in (v.get("context") or [])[:6]) if c],
                "risk": research._clean(v.get("risk"), 145),
                "sources": sources[:8],
            }
            if p.analysis["verdict"] == "pass":
                passed.add(id(p))
                print(f"[analyst] PASS {p.label} {fmt_american(p.price)}: {p.analysis['risk'] or p.analysis['angle']}")
                continue
            p.score *= 0.6 + 0.08 * conf           # confidence 5 = x1.0, 10 = x1.4, 1 = x0.68
            print(f"[analyst] {conf}/10 {p.label} {fmt_american(p.price)}: {p.analysis['angle']}")
    # Passes are removed; un-reviewed candidates stay but rank below reviewed "bet" calls of similar edge.
    reviewed = {id(p) for p in shortlisted if getattr(p, "analysis", None)}
    passed_sides = {(p.event_id, p.market, p.side) for p in shortlisted if id(p) in passed}
    out = []
    for p in cands:
        if id(p) in passed or (p.event_id, p.market, p.side) in passed_sides:
            continue                               # a pass also rules out the same bet at other lines
        if id(p) not in reviewed:
            p.score *= 0.85
        out.append(p)
    return out


def notes(p) -> dict | None:
    """The analyst's research in the shape the post templates use (same as research.pick_research)."""
    a = getattr(p, "analysis", None)
    if not a or not a.get("reasons"):
        return None
    return {"points": a["reasons"], "risk": a.get("risk", ""), "news": "", "angle": a.get("angle", ""),
            "context": a.get("context", []), "confidence": a.get("confidence"), "sources": a.get("sources", [])}
