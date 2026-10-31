"""Research module test against fake Claude API replies. Run: python -m tests.test_research"""
from ledger import config as C, research

ROW = {"sport": "basketball_nba", "market": "spreads", "side": "Celtics", "point": "-4.5", "label": "Celtics -4.5",
       "home": "Celtics", "away": "Knicks", "commence_time": "2026-11-02T23:30:00Z", "pick_no": "3"}


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


calls = []


def scripted(replies):
    def post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return replies.pop(0)
    return post


paused = Resp({"stop_reason": "pause_turn", "content": [
    {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "Celtics Knicks injury report"}}]})
final = Resp({"stop_reason": "end_turn", "content": [
    {"type": "text", "text": "Here is what I found."},
    {"type": "text", "text": '{"points": ["Boston is 7-1 against the spread at home this season", '
                             '"This is a lock, Knicks are cooked", '
                             '"New York is on the second night of a back-to-back [1]"], '
                             '"risk": "Brunson has torched Boston\'s drop coverage in both meetings", "news": ""}',
     "citations": [{"type": "web_search_result_location", "url": "https://example.com/injuries", "title": "x"}]}]})

C.ANTHROPIC_API_KEY = "test"
research.requests.post = scripted([paused, final])
out = research.pick_research(ROW)
assert out, "research should succeed"
assert len(calls) == 2 and calls[1]["messages"][1]["role"] == "assistant", "paused turn must be sent back"
assert calls[0]["tools"][0]["type"] == "web_search_20250305"
assert all("lock" not in p.lower() for p in out["points"]), "banned word must be dropped"
assert len(out["points"]) == 2 and "[1]" not in out["points"][1], out["points"]
assert out["sources"] == ["https://example.com/injuries"]
print("points:", out["points"], "\nrisk:", out["risk"])

research.requests.post = scripted([Resp({}, 529)])
assert research.pick_research(ROW) is None, "an API outage must not block the pick"
research.requests.post = scripted([Resp({"stop_reason": "end_turn", "content": [{"type": "text", "text": "no json"}]})])
assert research.pick_research(ROW) is None
research.requests.post = scripted([Resp({"stop_reason": "end_turn", "content": [
    {"type": "text", "text": '{"news": "Brunson (ankle) upgraded to probable and expected to play tonight"}'}]})])
assert "Brunson" in research.pregame_news(ROW)
C.ANTHROPIC_API_KEY = ""
assert research.pick_research(ROW) is None, "no key = market reasons only"
print("all research checks passed")
