# Stamped Picks: pick engine

Model-picked plays, explained, posted to X automatically, updated live, and graded in public against the closing line.

## What it posts (all automatic, all times Eastern)

One GitHub job runs every 5 minutes. Each run checks the date and time and does whatever is due, once.

### NFL and UFC card days

| When | Post |
|---|---|
| Day before, 12 PM | **Hype post** (big slates only: 4+ NFL games, 5+ fights), built from last week's real results: "Last week we CASHED on UFC: 3-1, +4.20u (+$420) 💰 We plan to do the same this week." or "Last week was a tough one on the NFL card (1-2, -1.10u). We've been grinding the numbers on this week's games…" |
| Day before, 8 PM | **The card**: up to 3 picks, each with units and dollars, the "good to" price, the numbers, the matchup and the biggest risk |
| Game day, 10 AM NFL / 12 PM UFC | **Gameday reminder** quoting the card: where each line is now, ✔ still worth it or ⚠️ don't chase |
| ~1 hour before each kickoff / fight | **Pre-game update**: line move plus late injury, lineup or weigh-in news |
| During games | **Live check-ins** at halftime and end of the 3rd: covering by X, need X more, total on pace for X. With conversational lines like "Love where this one sits 😤" or "Need a small push from here." |
| Each fight / game final | **Result** right away: "CASH IT ✅", units and dollars, card so far |
| Card finished | **Card recap** quoting the card: record, units, dollars, season record, streak ("4 in a row. The heater continues 🔥") |

NFL covers Thursday, Sunday and Monday. TNF and MNF get a card only if there's an edge (no hype post for single games). A game day runs 6 AM to 6 AM, so a UFC main event after midnight still counts as Saturday's card.

### Daily Play (NBA, NHL, MLB, college)

| When | Post |
|---|---|
| 11 AM | The pick thread (play, units/dollars, numbers, matchup, units explainer) + Members Board to Discord |
| ~1 hour before | Pre-game update |
| During | Check-ins (NBA: half and end of 3 · NHL: after periods 1 and 2 · MLB: through 5 innings · college hoops: half) |
| Final | Result quoting the pick, with record, units, dollars and streak |

### Weekly

Sunday 11 PM: week in review by NFL, UFC and Daily Play, with units and dollars.

### Streak hooks

At 3+ straight wins, posts add lines like "🔥 We've cashed 4 straight." At 3+ straight losses they say so plainly: "Rough stretch (3 straight losses). Same process, every pick still on the ledger." Every number in a hook comes from the ledger.

### Units in dollars

Every post converts units at $100 a unit (`UNIT_DOLLARS`): "💵 Bet 1.5 units (= $150 if 1 unit is $100)". Size follows the edge: 0.5u leans, 1u, 1.5u, and 2u only for 6%+ edges confirmed by Pinnacle and 2+ books.

## Replying under other cappers' posts

**The bot does not do this, on purpose.** X's automation rules ban automated replies to posts found by search; replies must go only to people who opted in, and breaking that gets apps and accounts suspended. Losing the account would end the business.

Instead, with every pick it sends you a **reply kit** in a private Discord channel: 3 ready-to-paste replies per pick that mention the play, the number and the "good to" price. Find the big cappers' posts about that game and paste them yourself. It takes a couple of minutes, and it's the fastest legitimate way to get the account seen.

## How the model picks

1. **Find the slate (free).** Event lists cost nothing, so it always knows what's on tomorrow before spending credits.
2. **Pull prices.** Moneyline, spread and total (moneyline only for fights) from 3 sharp books (Pinnacle, LowVig, BetOnline) and 6 books your followers can bet (DraftKings, FanDuel, BetMGM, Caesars, BetRivers, Fanatics).
3. **Strip the vig, price every bet.** Edge = fair probability × the sportsbook's decimal odds − 1, at the exact same line.
4. **Filter and score.** Odds −200 to +200, no stale quotes, no 10%+ "edges" (bad lines). Confidence from Pinnacle, sharp books agreeing, 2+ books offering it, and line movement.
5. **Pick.** Daily Play: the top score (half-size lean or "no play" if nothing clears 1.5%). Cards: up to 3 clear edges, one per game, no leans.
6. **Explain.** The numbers come straight from the model. The matchup comes from Claude with live web search, using that day's results only. Words like "lock" and "guarantee" are filtered out.
7. **Track it live.** ESPN's free public scoreboards for halftime, periods and fight results. Bets are graded the moment a game goes final. The Odds API's scores feed re-checks anything missed the next morning.
8. **Measure.** The closing line is captured right before kickoff, and CLV is recorded for every play.

## Telegram, selling, and crypto by DM

| Chat | Who's in it | What it gets |
|---|---|---|
| **Free channel** | Everyone | Every public post (copied from X), VIP teasers ("🔒 VIP board is in: 5 plays today…"), next-morning VIP results (wins *and* losses), and buy buttons. Pinned menu with all options and an anti-scam warning |
| **VIP channel** | Paying members | The full Members Board at 11 AM, plus every update and result |
| **Premium Pick channel** | Single-pick buyers | The day's top VIP play only |
| **Your private chat with the bot** | You | Reply kits, alerts, and your control commands |

**Buying.** Each button opens a Whop checkout: 🎯 Premium Pick, 📅 Day Pass, 🗓 Weekly, ⭐ Monthly, 👑 Yearly. Whop's Telegram integration adds the buyer to the right channel and removes them when their time ends. (Telegram requires purchases made *inside* a bot to use Telegram Stars, so the buttons send people to Whop's site instead.)

**Crypto by DM.** The "💸 Pay with crypto" button opens a DM with you. You send your wallet, they pay, you check the transaction on the blockchain explorer, then message the bot:

```
/grant month $45 USDC @mike     → a one-time invite link to forward (expires in 48h)
/members                        → who's active, days left
/extend 7 14   /revoke 7   /sales   /menu   /status   /settle 12 win   /help
```

The bot answers within about 5 minutes. When the person joins it tells you, warns you 2 days before their time ends (so you can DM about renewing), and removes them when it does. Member names and notes are encrypted with BOARD_KEY before saving, so they never show in the public repo. Use stablecoins (USDC/USDT) so prices don't swing, and keep a record of crypto income for taxes.

**Scam warning.** Paid pick channels get impersonated: fake accounts DM your followers asking for payment. The pinned menu says you never DM first and names the only handle to pay. Keep it pinned.

**Paid plays are full edges only.** VIP and Premium never get leans. On a thin day members get fewer plays, not weaker ones.

## Accounts you need

| Account | What it does | Cost |
|---|---|---|
| [The Odds API](https://the-odds-api.com) | Odds | Free plan works (about 300–450 of the 500 monthly credits). $30/month for breathing room |
| GitHub (public repo) | Runs every 5 minutes, hosts the ledger page, keeps the timestamped receipts | Free |
| X developer app (pay-per-use) | Posting | About $0.015 per post with no link. About 300–400 posts a month (football Sundays run 20–30), so roughly $5–10/month |
| Anthropic API | Matchup write-ups and pre-game news | About $8–15/month with NFL and UFC cards |
| Telegram bot (via @BotFather) | Free channel, VIP channel, Premium Pick channel, and your private control chat | Free |
| Whop | Card checkout for Premium Pick, Day Pass, Weekly, Monthly, Yearly; adds/removes buyers in Telegram | A percentage of each sale |
| Discord (optional) | Same members/private routing if you also want Discord | Free |
| ESPN scoreboards | Live scores | Free, no key |

## Setup (about 30 minutes)

1. **Odds key.** Sign up free at the-odds-api.com.
2. **GitHub.** Create a **public** repo and upload this folder.
3. **Secrets.** Settings → Secrets and variables → Actions → New repository secret:
   - `ODDS_API_KEY`
   - `BOARD_KEY`: run `python run.py newkey`. Keep a copy.
   - `ANTHROPIC_API_KEY`
   - `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`. Set the X app's user authentication to **Read and write** *before* generating the access token.
   - `TELEGRAM_BOT_TOKEN`: message @BotFather on Telegram → /newbot. Add the bot as an **admin** in each channel (with "Invite users" and "Ban users" on).
   - Optional: `DISCORD_WEBHOOK_URL` (members), `DISCORD_OWNER_WEBHOOK_URL` (just you)
4. **Variables:** `BRAND_NAME`, `BRAND_HANDLE`, `LEDGER_URL`; optional `UNIT_DOLLARS`, `X_PREMIUM`, `ODDS_PLAN=paid`.
   - Telegram: `TG_FREE_CHAT` (e.g. `@stampedpicks`), `TG_VIP_CHAT`, `TG_PREMIUM_CHAT`, `TG_OWNER_CHAT`, `OWNER_TG_USERNAME`. Private channels use a number like `-100…`: post something in each, send yourself a message to the bot, then Actions → Run workflow → `tgchats` lists every id.
   - Whop: `WHOP_PICK_URL`, `WHOP_DAY_URL`, `WHOP_WEEK_URL`, `WHOP_MONTH_URL`, `WHOP_YEAR_URL` (checkout links), and `PRICE_*` to change button prices. Empty link = no button. Then run `menu` once to post and pin the buy menu.
5. **Ledger page:** Settings → Pages → Deploy from branch → `main` → `/docs`.
6. **Dry run first.** Leave the X keys out for a week. Everything still runs and the posts are saved in `data/posts/`, so you can read them before anything goes public.

## Commands

```
python run.py tick                    # what the schedule runs every 5 minutes
python run.py daily --no-post         # today's Daily Play, text only
python run.py card nfl --no-post      # tomorrow's NFL card, text only
python run.py settle 12 win "Topuria by KO, round 2"   # grade play #12 by hand
python run.py demo                    # 45 simulated days -> demo_out/docs/index.html
python -m tests.test_posts && python -m tests.test_espn && python -m tests.test_math && python -m tests.test_research
```

All of these work from GitHub too: Actions → Stamped Picks → Run workflow → type the command.

## Tuning (`ledger/config.py`)

| Setting | What it controls |
|---|---|
| `CARD_SPORTS` | Which sports get card days, max picks, and the hype/card/reminder times |
| `SCHEDULE` | Daily Play, backup grading and weekly recap times |
| `ESPN` | Which live check-ins each sport gets |
| `MIN_EV`, `ALWAYS_PICK`, `UNIT_MODE`, `UNIT_DOLLARS` | Edge bar, leans, bet sizing, dollars per unit |

## Honest limits

- **ESPN's scoreboards are free but undocumented.** If a field changes, live check-ins pause; bets still get graded the next morning from The Odds API. UFC results come only from ESPN, so a fight it misses needs a one-line `settle`.
- **Results vary.** In the 45-day simulation the model beat the closing line on 90% of plays and still finished slightly down. That's normal over 70 bets: the edge shows up over hundreds of plays, and CLV shows it first. Posts never promise profits.
