"""Append-only audit log. Every write, every login attempt and every export goes through here."""
import datetime as dt
import json

from . import db


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(user: dict | None, action: str, target: str | None = None, detail=None, ip: str | None = None, ok: bool = True,
        email: str | None = None):
    db.run(
        "INSERT INTO audit_log(at, user_id, email, agency, action, target, detail, ip, ok) VALUES(?,?,?,?,?,?,?,?,?)",
        (now_iso(), user["id"] if user else None, (user or {}).get("email") or email, (user or {}).get("agency_code"),
         action, target, json.dumps(detail) if isinstance(detail, (dict, list)) else detail, ip, int(ok)),
    )
