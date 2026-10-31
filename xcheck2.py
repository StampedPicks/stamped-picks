"""Diagnoses X keys without printing them: length, stray whitespace, expected format; then tries users/me."""
import os, re
from requests_oauthlib import OAuth1Session

names = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
expect = {"X_API_KEY": 25, "X_API_SECRET": 50, "X_ACCESS_SECRET": 45}
vals = {}
for n in names:
    raw = os.getenv(n, "")
    v = raw.strip().strip('"').strip("'")
    vals[n] = v
    note = []
    if raw != v:
        note.append("had spaces/quotes/newline around it")
    if re.search(r"\s", v):
        note.append("has a space inside")
    if n == "X_ACCESS_TOKEN":
        note.append("format ok (starts with account id)" if re.match(r"^\d+-\w+$", v) else "WRONG format (should be digits-dash-letters)")
    elif n in expect:
        note.append(f"expected about {expect[n]} chars")
    print(f"{n}: {len(v)} chars; " + "; ".join(note))
same = [(a, b) for a in names for b in names if a < b and vals[a] and vals[a] == vals[b]]
if same:
    print("SAME VALUE pasted into:", same)
s = OAuth1Session(vals["X_API_KEY"], vals["X_API_SECRET"], vals["X_ACCESS_TOKEN"], vals["X_ACCESS_SECRET"])
r = s.get("https://api.x.com/2/users/me", timeout=30)
print(f"users/me (cleaned keys) -> {r.status_code}: {r.text[:200]}")
