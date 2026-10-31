"""Four simulated weeks through the real 15-minute scheduler with a fake X account and fake research.
Run: python -m tests.test_posts

Checks: every post fits 280 characters; hype at noon and the card at 8 PM the day before; the gameday
reminder quotes the card; one pre-game update per play, before kickoff; live check-ins only while the game
is on and never twice for the same checkpoint; results reply in the right thread after the game; card
recaps quote the card once every pick is graded; the Daily Play result quotes its pick; a research outage
never blocks a post."""
import io
import contextlib
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ledger import config as C, social, research, store
from ledger.model import parse_time
from ledger.social import x_len
from tests.simulate import simulate

ET = ZoneInfo("America/New_York")
clock = {"t": None}
posted = []          # dicts: id, text, reply_to, quote, t


def fake_post_x(text, reply_to=None, quote=None, mirror=True, media=None):
    pid = str(5000 + len(posted))
    posted.append({"id": pid, "text": text, "reply_to": reply_to, "quote": quote, "t": clock["t"], "media": media})
    return pid


def fake_research(row):
    if int(row["pick_no"]) % 6 == 0:       # an outage every 6th pick
        return None
    who = "fighter" if row["sport"] == "mma_mixed_martial_arts" else "team"
    return {"points": [f"[{who} advantage from today's news]", "[recent form note]", "[injury or lineup edge]"],
            "risk": "[the biggest thing that could beat this bet]", "news": "", "sources": []}


def main():
    root = Path("test_out").resolve()
    social.post_x = fake_post_x
    research.pick_research = fake_research
    research.pregame_news = lambda row: "[late injury or lineup note]"
    start = datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        simulate(28, start, root, seed=11, before_tick=lambda t: clock.update(t=t))
    rows, state = store.read(store.LEDGER), store.load_state()
    by_id = {p["id"]: p for p in posted}
    et = lambda p: p["t"].astimezone(ET)

    over = [p for p in posted if x_len(p["text"]) > 280]
    assert not over, f"{len(over)} posts over 280 chars, e.g. {over[0]['text']!r}"
    assert rows, "no plays"

    def chain(main_id):
        """Every post id in a thread: replies of replies, starting from main."""
        ids, grew = {main_id}, True
        while grew:
            grew = False
            for p in posted:
                if p["reply_to"] in ids and p["id"] not in ids:
                    ids.add(p["id"])
                    grew = True
        return ids

    last_day = (start + timedelta(days=28)).astimezone(ET).date().isoformat()
    cards = sorted({r["card"] for r in rows if r["card"] and r["card"].split("|")[1] < last_day})
    rows_done = [r for r in rows if not r["card"] or r["card"] in cards]
    for cid in cards:
        sport, day = cid.split("|")
        cfg = C.CARD_SPORTS[sport]
        main = by_id[state["threads"][cid]["main"]]
        assert main["reply_to"] is None and "card" in main["text"].lower()
        hh = int(cfg["card"][:2])
        assert et(main).date().isoformat() < day and et(main).hour == hh, f"{cid} card posted {et(main)}"
        rem = [p for p in posted if p["quote"] == main["id"] and "Today's" in p["text"].split("\n")[0]]
        assert len(rem) == 1 and et(rem[0]).date().isoformat() == day and et(rem[0]).hour == int(cfg["reminder"][:2]), cid
        ids = chain(main["id"]) | chain(rem[0]["id"])
        picks = [r for r in rows if r["card"] == cid]
        for r in picks:
            res = [p for p in posted if p["id"] in ids and r["label"] in p["text"].split("\n")[0]
                   and ("✅" in p["text"] or "❌" in p["text"] or "Push" in p["text"])]
            assert len(res) == 1, f"{cid} #{r['pick_no']}: {len(res)} result replies"
            assert res[0]["t"] > parse_time(r["commence_time"])
        recap = [p for p in posted if p["quote"] == main["id"] and p is not rem[0]]
        assert len(recap) == 1 and all(r["result"] for r in picks), f"{cid} recap"
    for r in [r for r in rows_done if r["result"]]:
        start_t = parse_time(r["commence_time"])
        near = lambda p, before, after: start_t - timedelta(hours=before) < p["t"] < start_t + timedelta(hours=after)
        mine = [p for p in posted if p["text"].startswith("⏱️") and r["label"] in p["text"] and near(p, 3, 0)]
        assert len(mine) == 1, f"#{r['pick_no']} has {len(mine)} pre-game updates"
        assert mine[0]["t"] < parse_time(r["commence_time"])
        lv = [p for p in posted if r["label"] in p["text"] and near(p, 3, 6) and any(k in p["text"] for k in
              ("Halftime", "End of 3", "After 1 period", "After 2 periods", "Through 5 innings"))]
        assert all(p["t"] > start_t for p in lv)
        assert len(lv) <= 2, f"#{r['pick_no']} {len(lv)} live posts"
        if not r["card"]:
            main = state["threads"][f"daily{r['pick_no']}"]["main"]
            assert et(by_id[main]).hour == 11
            assert sum(1 for p in posted if p["quote"] == main) == 1, f"daily #{r['pick_no']} result quote"
    hype = [p for p in posted if "Picks drop tonight" in p["text"]]
    assert all(et(p).hour == 12 for p in hype) and hype
    assert any("CASHED" in p["text"] or "tough one" in p["text"].lower() or "Not our week" in p["text"] for p in hype)
    kinds = Counter()
    for p in posted:
        first = p["text"].split("\n")[0]
        kinds["hype" if "Picks drop tonight" in p["text"] else "card thread" if p["reply_to"] is None and "card" in first
              and not p["quote"] else "reminder" if "Today's" in first and p["quote"] else "pre-game" if first.startswith("⏱️")
              else "live check-in" if any(k in p["text"] for k in ("Halftime", "End of 3", "period", "innings"))
              else "recap" if p["quote"] and "card" in first else "result"
              if any(k in first for k in ("✅", "❌", "Push")) else "other"] += 1
    print(f"{len(rows)} plays ({len(cards)} cards), {len(posted)} posts over 28 days, "
          f"longest {max(x_len(p['text']) for p in posted)} chars")
    print("posts by type:", dict(kinds))

    def show(p, label):
        print(f"\n=== {label} · {et(p):%a %b %-d %-I:%M %p} ET · {x_len(p['text'])} chars"
              f"{' · reply' if p['reply_to'] else ''}{' · quote' if p['quote'] else ''} ===\n{p['text']}")
    ufc = max((c for c in cards if c.startswith("mma")), key=lambda c: (sum(r["card"] == c for r in rows), c))
    nfl = next(c for c in reversed(cards) if c.startswith("americanfootball_nfl") and
               sum(r["card"] == c for r in rows) >= 2)
    for cid in (nfl, ufc):
        m = by_id[state["threads"][cid]["main"]]
        hy = [p for p in hype if et(p).date() == et(m).date() and C.SPORTS[cid.split('|')[0]][1] in p["text"]]
        if hy:
            show(hy[0], "hype")
        i = posted.index(m)
        for p in posted[i:i + 3]:
            show(p, "card thread")
        ids = chain(m["id"])
        rem = next(p for p in posted if p["quote"] == m["id"] and "Today's" in p["text"])
        ids |= chain(rem["id"])
        show(rem, "gameday reminder")
        for p in [p for p in posted if p["id"] in ids and p["t"] > rem["t"]][:6]:
            show(p, "during the games")
        show(next(p for p in posted if p["quote"] == m["id"] and p is not rem), "card recap")
    shutil.rmtree(root, ignore_errors=True)
    gifs = [p for p in posted if p.get("media") and str(p["media"]).endswith(".gif")]
    pngs = [p for p in posted if p.get("media") and str(p["media"]).endswith(".png")]
    assert gifs and pngs, "result GIFs and pick/card images should be attached"
    assert all(Path(p["media"]).exists() and Path(p["media"]).stat().st_size < 5_000_000 for p in gifs + pngs)
    print(f"graphics: {len(pngs)} images, {len(gifs)} result GIFs attached")
    print("\nall posting checks passed")


if __name__ == "__main__":
    main()
