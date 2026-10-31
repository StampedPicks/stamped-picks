"""Quick checks of the betting math. Run: python -m tests.test_math"""
from ledger.model import devig, american, fmt_american
from ledger.grading import grade, clv
from ledger.seal import seal_board, unseal, verify, new_key

# -110 / -110 should de-vig to 50/50
a, b = devig([1.909, 1.909])
assert abs(a - .5) < 1e-6 and abs(a + b - 1) < 1e-9
# favorite keeps more probability than simple normalising would give the longshot
p = devig([1.25, 4.20])
assert abs(sum(p) - 1) < 1e-9 and p[0] > (1 / 1.25) / (1 / 1.25 + 1 / 4.2)
assert american(1.909) == -110 and american(2.5) == 150 and fmt_american(2.5) == "+150"

game = {"completed": True, "home_team": "Home", "away_team": "Away",
        "scores": [{"name": "Home", "score": "24"}, {"name": "Away", "score": "20"}]}
row = lambda **k: {"units": "1", "price": "1.909", "point": "", **k}
assert grade(row(market="h2h", side="Home"), game)[0] == "win"
assert grade(row(market="h2h", side="Away"), game)[0] == "loss"
assert grade(row(market="spreads", side="Home", point="-4"), game)[0] == "push"
assert grade(row(market="spreads", side="Home", point="-4.5"), game)[0] == "loss"
assert grade(row(market="spreads", side="Away", point="4.5"), game)[0] == "win"
assert grade(row(market="totals", side="Over", point="43.5"), game)[0] == "win"
assert grade(row(market="totals", side="Under", point="43.5"), game)[0] == "loss"
assert abs(grade(row(market="h2h", side="Home"), game)[2] - 0.909) < 1e-9

# bet at +110 (2.10) when the close says 50% -> +5% CLV
assert abs(clv(2.10, 0.5) - 0.05) < 1e-9

k = new_key()
plays = [{"date": "2026-10-30", "sport": "basketball_nba", "event_id": "x", "market": "h2h", "side": "A",
          "point": "", "book": "fanduel", "price": "2.1", "units": "1"}]
seals, blob = seal_board(plays, k)
back = unseal(blob, k)
assert verify(back[0]) and back[0]["seal"] == seals[0]
back[0]["price"] = "2.2"          # tampering breaks the seal
assert not verify(back[0])
print("all math checks passed")
