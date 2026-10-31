"""Sealed picks: members' board plays are committed publicly as fingerprints before games start,
then revealed after. Anyone can check that sha256(play + salt) matches the fingerprint posted earlier,
which proves the play was not edited or added after the fact."""
import hashlib
import json
import secrets
from cryptography.fernet import Fernet

FIELDS = ["date", "sport", "event_id", "market", "side", "point", "book", "price", "units"]


def canonical(play: dict) -> str:
    return json.dumps({k: play.get(k) for k in FIELDS}, sort_keys=True, separators=(",", ":"))


def fingerprint(play: dict, salt: str) -> str:
    return hashlib.sha256((canonical(play) + "|" + salt).encode()).hexdigest()


def seal_board(plays: list[dict], key: str) -> tuple[list[str], bytes]:
    for p in plays:
        p["salt"] = secrets.token_hex(16)
        p["seal"] = fingerprint(p, p["salt"])
    blob = Fernet(key.encode()).encrypt(json.dumps(plays).encode())
    return [p["seal"] for p in plays], blob


def unseal(blob: bytes, key: str) -> list[dict]:
    return json.loads(Fernet(key.encode()).decrypt(blob))


def verify(play: dict) -> bool:
    return fingerprint(play, play["salt"]) == play["seal"]


def new_key() -> str:
    return Fernet.generate_key().decode()
