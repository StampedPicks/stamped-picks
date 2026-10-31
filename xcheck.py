"""Checks the X keys without posting anything: asks X which account the keys belong to."""
from ledger import social

s = social._x_session()
if not s:
    raise SystemExit("[x] one or more X keys are missing")
r = s.get("https://api.x.com/2/users/me", timeout=30)
print(f"[x] users/me -> {r.status_code}: {r.text[:300]}")
if r.status_code >= 300:
    raise SystemExit(1)
