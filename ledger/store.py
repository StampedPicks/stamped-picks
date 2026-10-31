"""Files on disk: the public ledger CSVs, odds snapshots, and run state."""
import csv
import json
from datetime import datetime
from pathlib import Path
from . import config as C

LEDGER = C.DATA / "ledger.csv"
BOARD_LEDGER = C.DATA / "board_ledger.csv"
SEALS = C.DATA / "board_seals.csv"
STATE = C.DATA / "state.json"
SNAPS = C.DATA / "snapshots"

COLUMNS = ["date", "pick_no", "posted_at", "tier", "sport", "event_id", "commence_time", "home", "away",
           "market", "side", "point", "label", "book", "price", "price_american", "play_to", "fair_prob",
           "ev", "units", "n_sources", "movement", "close_price", "close_fair_prob", "clv",
           "result", "final_score", "profit", "card"]
BOARD_COLUMNS = COLUMNS + ["seal", "salt"]


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write(path: Path, rows: list[dict], columns=COLUMNS):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def append(path: Path, row: dict, columns=COLUMNS):
    rows = read(path)
    rows.append(row)
    write(path, rows, columns)


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(s: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


def save_snapshot(sport: str, when: datetime, events: list[dict]):
    d = SNAPS / when.strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    for e in events:
        e["_snapshot_time"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    (d / f"{sport}_{when.strftime('%H%M')}.json").write_text(json.dumps(events))


def snapshot_history(sport: str, before: datetime, days: int = 7) -> dict:
    """{event_id: [snapshots oldest-first]} from saved files, for line-movement scoring."""
    hist: dict = {}
    if not SNAPS.exists():
        return hist
    for f in sorted(SNAPS.glob(f"*/{sport}_*.json")):
        for e in json.loads(f.read_text()):
            t = datetime.fromisoformat(e["_snapshot_time"].replace("Z", "+00:00"))
            if t < before and (before - t).days <= days:
                hist.setdefault(e["id"], []).append(e)
    for v in hist.values():
        v.sort(key=lambda e: e["_snapshot_time"])
    return hist


def stats(rows: list[dict]) -> dict:
    done = [r for r in rows if r.get("result") in ("win", "loss", "push")]
    w = sum(r["result"] == "win" for r in done)
    l = sum(r["result"] == "loss" for r in done)
    p = sum(r["result"] == "push" for r in done)
    profit = sum(float(r["profit"] or 0) for r in done)
    risked = sum(float(r["units"]) for r in done if r["result"] != "push")
    clvs = [float(r["clv"]) for r in rows if r.get("clv") not in (None, "")]
    return {
        "wins": w, "losses": l, "pushes": p, "profit": profit,
        "roi": profit / risked if risked else 0.0,
        "win_pct": w / (w + l) if (w + l) else 0.0,
        "avg_clv": sum(clvs) / len(clvs) if clvs else None,
        "beat_close": sum(c > 0 for c in clvs) / len(clvs) if clvs else None,
        "n_clv": len(clvs), "pending": sum(1 for r in rows if not r.get("result")),
    }
