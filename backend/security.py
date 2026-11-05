"""Passwords, signed tokens, roles and permissions.

Tokens are HMAC-SHA256 signed (same structure as a JWT with alg HS256), carry an expiry and a token id that is
checked against a revocation table on logout. Passwords use PBKDF2-SHA256 with a per-user salt.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time

from . import config, db

PBKDF2_ROUNDS = 240_000

# What each role may do. Everything is also limited by incident visibility (see can_see_incident).
ROLES = {
    "admin": {"label": "Administrator", "perms": {
        "read", "alerts:update", "incidents:create", "incidents:update", "engine:run", "export", "share", "comment",
        "audit:read", "users:manage", "ingest", "intake:submit", "keys:manage"}},
    "analyst": {"label": "Intelligence analyst", "perms": {
        "read", "alerts:update", "incidents:create", "incidents:update", "engine:run", "export", "share", "comment",
        "intake:submit", "keys:manage"}},
    "operator": {"label": "Operations officer", "perms": {
        "read", "alerts:update", "incidents:create", "incidents:update", "share", "comment", "intake:submit"}},
    "viewer": {"label": "Liaison (read only)", "perms": {"read", "comment"}},
}
PERMISSION_LABELS = {
    "read": "See the maritime picture, vessels, shared alerts and incidents",
    "alerts:update": "Acknowledge, assign and close alerts",
    "incidents:create": "Report incidents and upload evidence",
    "incidents:update": "Change incident status",
    "engine:run": "Re-run the threat-detection engine",
    "export": "Export reports (CSV, JSON)",
    "share": "Share alerts, incidents and vessel files with other agencies",
    "comment": "Add notes to shared items",
    "audit:read": "Read the full audit log",
    "users:manage": "Create users and change roles",
    "ingest": "Push AIS positions for any source (platform level)",
    "intake:submit": "Submit agency data (sightings, patrol reports, radar contacts, watchlist, incidents)",
    "keys:manage": "Create and revoke API keys for their agency's systems",
}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${b64(salt)}${b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), unb64(salt), int(rounds))
        return hmac.compare_digest(dk, unb64(digest))
    except (ValueError, TypeError):
        return False


def make_token(user: dict) -> tuple[str, int]:
    exp = int(time.time() + config.TOKEN_HOURS * 3600)
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"sub": user["id"], "role": user["role"], "agency": user["agency_id"], "exp": exp,
                           "jti": secrets.token_hex(12)}).encode())
    sig = b64(hmac.new(config.secret_key(), f"{header}.{body}".encode(), hashlib.sha256).digest())
    return f"{header}.{body}.{sig}", exp


def read_token(token: str) -> dict | None:
    try:
        header, body, sig = token.split(".")
        expected = b64(hmac.new(config.secret_key(), f"{header}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        claims = json.loads(unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if claims.get("exp", 0) < time.time():
        return None
    if db.one("SELECT 1 AS x FROM revoked_tokens WHERE jti = ?", (claims.get("jti"),)):
        return None
    return claims


def revoke(claims: dict):
    db.run("INSERT OR IGNORE INTO revoked_tokens(jti, exp) VALUES(?, ?)", (claims["jti"], claims["exp"]))
    db.run("DELETE FROM revoked_tokens WHERE exp < ?", (int(time.time()),))


def perms(role: str) -> set:
    return ROLES.get(role, {"perms": set()})["perms"]


def can_see_incident(user: dict, inc: dict) -> bool:
    if user["role"] == "admin" or inc["visibility"] == "shared" or inc["agency_id"] == user["agency_id"]:
        return True
    # restricted incidents become visible to an agency that received an explicit share
    return bool(db.one("SELECT 1 AS x FROM shares WHERE target_type = 'incident' AND target_id = ? AND to_agency = ?",
                       (inc["id"], user["agency_id"])))
