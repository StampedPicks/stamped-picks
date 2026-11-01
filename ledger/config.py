"""All settings in one place. Secrets come from environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("LEDGER_DATA_DIR", ROOT / "data"))
DOCS = Path(os.getenv("LEDGER_DOCS_DIR", ROOT / "docs"))

# ---------------------------------------------------------------- brand
BRAND_NAME = os.getenv("BRAND_NAME", "Stamped Picks")
BRAND_HANDLE = os.getenv("BRAND_HANDLE", "@StampedPicks")
LEDGER_URL = os.getenv("LEDGER_URL", "")      # e.g. https://you.github.io/stamped-picks/
REPO_URL = os.getenv("REPO_URL", "")          # e.g. https://github.com/you/stamped-picks
DISPLAY_TZ = os.getenv("DISPLAY_TZ", "America/New_York")
# X charges ~$0.20 for an API post containing a link vs ~$0.015 without one.
LINK_IN_POSTS = os.getenv("LINK_IN_POSTS", "false").lower() == "true"

# ---------------------------------------------------------------- odds data
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
# "free" = 500 credits/month. "paid" = any paid plan (unlocks multi-snapshot line movement).
ODDS_PLAN = os.getenv("ODDS_PLAN", "free")
# Credits the Daily Play scan may spend. Free plan math: ~8 scan + ~2 per pick for closing lines, plus
# ~30/week for NFL and UFC cards ≈ 450/month. The $30 plan (20,000 credits) removes the squeeze.
SCAN_CREDIT_BUDGET = int(os.getenv("SCAN_CREDIT_BUDGET", "8" if ODDS_PLAN == "free" else "120"))

SPORTS = {
    "americanfootball_nfl": ("NFL", "🏈"),
    "americanfootball_ncaaf": ("CFB", "🏈"),
    "basketball_nba": ("NBA", "🏀"),
    "basketball_ncaab": ("CBB", "🏀"),
    "baseball_mlb": ("MLB", "⚾"),
    "icehockey_nhl": ("NHL", "🏒"),
    "mma_mixed_martial_arts": ("UFC/MMA", "🥊"),
    "boxing_boxing": ("Boxing", "🥊"),
}
MARKETS = ["h2h", "spreads", "totals"]
# Sports that only have some markets. Fights are winner-only (moneyline).
SPORT_MARKETS = {"mma_mixed_martial_arts": ["h2h"], "boxing_boxing": ["h2h"]}
# What the bet is on, in plain words, for the write-up.
SUBJECT = {"mma_mixed_martial_arts": "fighter", "boxing_boxing": "fighter"}

# Sharp books set the "true" price. Weight = how much we trust each one.
PRICING_BOOKS = {"pinnacle": 1.0, "lowvig": 0.5, "betonlineag": 0.5}
# Books followers can actually bet at. Picks only ever come from these.
# Keep PRICING_BOOKS + BET_BOOKS at 10 or fewer so a call costs 1 credit per market.
BET_BOOKS = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "williamhill_us": "Caesars",
    "betrivers": "BetRivers",
    "fanatics": "Fanatics",
}

# ---------------------------------------------------------------- model rules
MIN_EV = float(os.getenv("MIN_EV", "0.015"))            # 1.5% edge to qualify as a full play
FALLBACK_MIN_EV = float(os.getenv("FALLBACK_MIN_EV", "0.005"))  # used only if nothing clears MIN_EV
ALWAYS_PICK = os.getenv("ALWAYS_PICK", "true").lower() == "true"
# When nothing clears MIN_EV, the Daily Play and NFL/UFC cards still go out with the best-priced plays on the
# board, marked as leans and bet smaller. A lean must be within this much of the sharp fair price (-0.02 = the
# best number on the board is no worse than 2% under fair). Set ALWAYS_PICK=false to go back to "no edge, no bet".
LEAN_FLOOR = float(os.getenv("LEAN_FLOOR", "-0.02"))
MAX_EV = 0.10                 # anything above 10% is almost always a stale/bad line: skip it
MIN_DECIMAL, MAX_DECIMAL = 1.50, 3.00   # -200 to +200 only. No longshots, no heavy chalk.
EARLIEST_START_MIN = 60       # game must start at least 60 min after the pick
LATEST_START_HOURS = 16       # ...and within today's slate
MAX_QUOTE_AGE_MIN = 90        # ignore prices a book hasn't refreshed in 90 min
CLOSE_WINDOW_MIN = 60         # start capturing the closing line this close to game time
BOARD_SIZE = 5                # how many edges go to the paid members board
UNIT_MODE = os.getenv("UNIT_MODE", "tiered")   # "tiered" = 1u / 1.5u / 2u by edge, 0.5u on leans. "flat" = always 1u.
UNIT_DOLLARS = int(os.getenv("UNIT_DOLLARS", "100"))   # posts translate units to dollars at this amount per unit

# ---------------------------------------------------------------- outputs
def _x_keys() -> dict:
    """X's four keys, matched by their shape so they work even if pasted into the wrong slots:
    access token = digits-dash-letters, consumer key = 25 chars, consumer secret = 50, token secret = 45."""
    import re as _re
    names = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
    vals = [os.getenv(n, "").strip().strip("\"'") for n in names]
    found = {}
    for v in vals:
        if _re.match(r"^\d+-\w+$", v):
            found["X_ACCESS_TOKEN"] = v
        elif len(v) == 25:
            found["X_API_KEY"] = v
        elif len(v) == 50:
            found["X_API_SECRET"] = v
        elif len(v) == 45:
            found["X_ACCESS_SECRET"] = v
    return found if len(found) == 4 else dict(zip(names, vals))


X_KEYS = _x_keys()
# Standard X accounts get 280 characters per post, so the pick goes out as a short thread.
# With X Premium (25,000 characters) set this to true and it goes out as one long post.
X_PREMIUM = os.getenv("X_PREMIUM", "false").lower() == "true"
# Quote posts need X's Enterprise API; on pay-per-use, results and recaps go out as their own posts.
X_QUOTES = os.getenv("X_QUOTES", "false").lower() == "true"
# Pictures on posts (pick tickets, card images, CASHED stamp GIFs). Turn off with GRAPHICS=false.
GRAPHICS = os.getenv("GRAPHICS", "true").lower() == "true"

# Matchup research: Claude with web search writes the team/player/fighter edge and checks injury news.
# Without a key, posts still go out with the betting-market reasons only.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
RESEARCH_MAX_SEARCHES = int(os.getenv("RESEARCH_MAX_SEARCHES", "5"))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")   # paid members channel
DISCORD_OWNER_WEBHOOK_URL = os.getenv("DISCORD_OWNER_WEBHOOK_URL", "")   # your private channel: reply kits, alerts
BOARD_KEY = os.getenv("BOARD_KEY", "")   # Fernet key that seals the members board until games start

# ---------------------------------------------------------------- card days (NFL + UFC)
# These sports get the full build-up: hype post the day before, the card the night before,
# a game-day reminder, then live updates. All times are Eastern. A "game day" runs 6 AM to 6 AM ET,
# so a UFC main event after midnight still counts as Saturday's card.
CARD_SPORTS = {
    "americanfootball_nfl": {"name": "NFL", "max_picks": 3, "min_picks": 3, "hype": "12:00", "card": "20:00", "reminder": "10:00",
                             "min_games_for_hype": 4},   # Sundays get hype; TNF/MNF just get the card
    "mma_mixed_martial_arts": {"name": "UFC", "max_picks": 3, "min_picks": 2, "hype": "12:00", "card": "20:00", "reminder": "12:00",
                               "min_games_for_hype": 5},
    # Big fights only: a fight counts when at least BIG_FIGHT_MIN_BOOKS of the 6 US books post it.
    "boxing_boxing": {"name": "Boxing", "max_picks": 2, "min_picks": 1, "hype": "12:00", "card": "20:00", "reminder": "12:00",
                      "min_games_for_hype": 1},
}
BIG_FIGHT_MIN_BOOKS = 5
DAY_START_HOUR = 6
# Daily Play and the other jobs, Eastern time.
SCHEDULE = {"grade": "06:00", "daily": "11:00", "recap": "23:00"}   # recap runs on Sundays
TASK_GRACE_HOURS = 4          # a job that's late (runner delays) still runs if it's within 4 hours of its time
PREGAME_UPDATE_MIN = (40, 80)  # post the pre-game update when kickoff is 40–80 minutes away
FINAL_CLOSE_MIN = 25           # take the final closing-line snapshot inside 25 minutes of kickoff

# ---------------------------------------------------------------- live scores (ESPN, free, no key)
ESPN = {
    "americanfootball_nfl": ("football/nfl", 4, [("half", 3), ("q4", 4)]),
    "americanfootball_ncaaf": ("football/college-football", 4, [("half", 3), ("q4", 4)]),
    "basketball_nba": ("basketball/nba", 4, [("half", 3), ("q4", 4)]),
    "basketball_ncaab": ("basketball/mens-college-basketball", 2, [("half", 2)]),
    "icehockey_nhl": ("hockey/nhl", 3, [("p1", 2), ("p2", 3)]),
    "baseball_mlb": ("baseball/mlb", 9, [("mid", 6)]),
    "mma_mixed_martial_arts": ("mma/ufc", 1, []),
}
# (path, regulation periods, [(checkpoint, post it once this period has started)])
# A checkpoint uses the period-by-period scores, so a late runner still reports the halftime score correctly.

# ---------------------------------------------------------------- Telegram
# One bot (make it with @BotFather), added as an admin to each channel. Public channels can use "@name";
# private channels need their number (-100...). Run `python run.py tgchats` to list them.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_FREE = os.getenv("TG_FREE_CHAT", "")          # free channel: every public post, wins, VIP results, buy buttons
TG_VIP = os.getenv("TG_VIP_CHAT", "")            # paid channel: Members Board + everything else
TG_PREMIUM = os.getenv("TG_PREMIUM_CHAT", "")    # single-pick buyers: the day's top VIP play only
TG_OWNER = os.getenv("TG_OWNER_CHAT", "")        # just you: reply kits, alerts
# Public join link for the free channel, shown as a button on the ledger page. Worked out from TG_FREE_CHAT
# when that's an @name; set it yourself if the channel uses a private invite link.
TG_FREE_URL = os.getenv("TG_FREE_URL", "") or (f"https://t.me/{TG_FREE[1:]}" if TG_FREE.startswith("@") else "")
VIP_URL = os.getenv("VIP_URL", "")               # optional "Get VIP" button on the ledger page (e.g. your Whop store)

# ---------------------------------------------------------------- selling (Whop)
# Each option = a Whop product. Whop takes the payment and its Telegram integration adds the buyer to the
# right channel and removes them when their time is up. Leave a link empty to hide that button.
# Telegram requires purchases made inside a bot to use Telegram Stars, so these buttons open Whop's
# checkout in the browser rather than selling inside Telegram.
SALES = [   # (key, button label, price, Whop checkout link) -- prices are only for the button text
    ("pick", "🎯 Premium Pick", os.getenv("PRICE_PICK", "$5"), os.getenv("WHOP_PICK_URL", "")),
    ("day", "📅 Day Pass", os.getenv("PRICE_DAY", "$10"), os.getenv("WHOP_DAY_URL", "")),
    ("week", "🗓 Weekly", os.getenv("PRICE_WEEK", "$15"), os.getenv("WHOP_WEEK_URL", "")),
    ("month", "⭐ Monthly", os.getenv("PRICE_MONTH", "$45"), os.getenv("WHOP_MONTH_URL", "")),
    ("year", "👑 Yearly", os.getenv("PRICE_YEAR", "$299"), os.getenv("WHOP_YEAR_URL", "")),
]

# ---------------------------------------------------------------- crypto memberships by DM
# Users tap "Pay with crypto", which opens a DM with you. After you confirm the payment on-chain, message
# the bot "/grant month" (in your private chat with it) and it hands you a one-time invite link to forward.
OWNER_TG_USERNAME = os.getenv("OWNER_TG_USERNAME", "")   # your Telegram @username, without the @
CRYPTO_NOTE = os.getenv("CRYPTO_NOTE", "")               # e.g. "USDC (Base) 0xABC… · USDT (TRON) T… · BTC bc1…"
PLANS = {   # plan: (days of access, which channel)
    "pick": (1, "premium"), "day": (1, "vip"), "week": (7, "vip"), "month": (30, "vip"), "year": (365, "vip"),
}
INVITE_HOURS = 48          # a granted invite link works once and expires after this long if unused
RENEWAL_WARNING_DAYS = 2   # tells you when someone's membership is about to end, so you can DM them to renew
