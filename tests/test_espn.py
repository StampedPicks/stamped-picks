"""Live-feed matching and bet-status checks. Run: python -m tests.test_espn"""
from ledger import espn, live

# ESPN names differ from sportsbook names in a few known ways.
side = lambda display, nick, short="", loc="", abbr="": {
    "names": {espn.norm(x) for x in (display, short, loc, abbr) if x}, "nick": espn.norm(nick), "score": 0, "lines": []}
assert espn.team_match("Los Angeles Clippers", side("LA Clippers", "Clippers"))
assert espn.team_match("Ohio State Buckeyes", side("Ohio State Buckeyes", "Buckeyes"))
assert espn.team_match("Kansas City Chiefs", side("Kansas City Chiefs", "Chiefs"))
assert not espn.team_match("Kansas City Chiefs", side("Las Vegas Raiders", "Raiders"))
assert espn.fighter_key("José Aldo") == espn.fighter_key("Jose Aldo")

# a real-shaped scoreboard: NFL game at halftime
raw = {"events": [{"id": "1", "date": "2026-11-01T18:00Z", "competitions": [{
    "status": {"period": 2, "type": {"state": "in", "completed": False, "shortDetail": "Halftime"}},
    "competitors": [
        {"homeAway": "home", "score": "17", "team": {"displayName": "Kansas City Chiefs", "name": "Chiefs"},
         "linescores": [{"value": 7}, {"value": 10}]},
        {"homeAway": "away", "score": "10", "team": {"displayName": "Las Vegas Raiders", "name": "Raiders"},
         "linescores": [{"value": 3}, {"value": 7}]}]}]}]}
row = {"sport": "americanfootball_nfl", "home": "Kansas City Chiefs", "away": "Las Vegas Raiders",
       "commence_time": "2026-11-01T18:00:00Z", "market": "spreads", "side": "Kansas City Chiefs", "point": "-3.5"}
g, swapped = espn.find_game(row, espn.games(raw))
assert g and not swapped
latest, reached = live.checkpoints_due(row["sport"], g, [])
assert latest == ("half", 2), latest
st = live.bet_status(row, g, swapped, 2)
assert st["margin"] == 3.5 and live.describe(row, st) == "Covering by 3.5."
assert live.checkpoints_due(row["sport"], g, ["half"])[0] is None, "never post halftime twice"

# runner was late: game already in the 4th → only "End of 3" posts, halftime is marked done (never sent stale)
g["period"], g["detail"] = 4, "Q4 12:10"
latest, reached = live.checkpoints_due(row["sport"], g, [])
assert latest[0] == "q4" and "half" in reached

# totals pace
row_t = dict(row, market="totals", side="Over", point="44.5")
st = live.bet_status(row_t, g, False, 2)
assert st["total"] == 27 and round(st["pace"]) == 54

# UFC: fight winner from the card
raw_ufc = {"events": [{"id": "c", "name": "UFC 320", "date": "2026-11-01T02:00Z", "competitions": [
    {"id": "f1", "date": "2026-11-01T04:30Z", "status": {"type": {"state": "post", "completed": True, "shortDetail": "Final"},
                                                         "result": {"displayName": "KO/TKO"}},
     "competitors": [{"athlete": {"displayName": "Ilia Topuria"}, "winner": True},
                     {"athlete": {"displayName": "Max Holloway"}, "winner": False}]}]}]}
fr = {"home": "Ilia Topuria", "away": "Max Holloway", "sport": "mma_mixed_martial_arts",
      "commence_time": "2026-11-01T04:30:00Z"}
f = espn.find_fight(fr, espn.fights(raw_ufc))
assert f and f["result"] == "KO/TKO" and f["fighters"][0]["winner"]
assert espn.games({}) == [] and espn.fights({"events": [{"competitions": [{}]}]}) == [], "bad data must not crash"
print("all live-feed checks passed")
