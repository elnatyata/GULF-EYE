"""Paths and settings. Everything can be overridden with environment variables."""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = WEB / "data"                                   # simulated AIS/SAR/geo files (existing pipeline output)
VAR = Path(os.environ.get("GULFEYE_VAR", ROOT / "var"))   # database, uploads, secret (not served)
DB_PATH = Path(os.environ.get("GULFEYE_DB", VAR / "gulfeye.sqlite3"))
UPLOADS = VAR / "uploads"
SEED_DIR = ROOT / "backend" / "seed_data"

TOKEN_HOURS = float(os.environ.get("GULFEYE_TOKEN_HOURS", "8"))
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_UPLOADS = {  # extension: (mime, magic-bytes check)
    ".pdf": ("application/pdf", lambda b: b.startswith(b"%PDF")),
    ".png": ("image/png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    ".txt": ("text/plain", lambda b: b"\x00" not in b[:4096]),
    ".csv": ("text/csv", lambda b: b"\x00" not in b[:4096]),
}
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_MINUTES = 5

# Demo accounts are created on first start. Change the password with GULFEYE_DEMO_PASSWORD before showing it to anyone.
DEMO_PASSWORD = os.environ.get("GULFEYE_DEMO_PASSWORD", "GulfEye-demo-2026")
# One-click sign-in as a demo account (for judges and presentations). Set GULFEYE_DEMO_LOGIN=0 before real use:
# then only email + password work. It only ever applies to the seeded *.demo accounts.
TRUST_PROXY = os.environ.get("GULFEYE_TRUST_PROXY", "0").lower() in ("1", "true", "yes", "on")
DEMO_LOGIN = os.environ.get("GULFEYE_DEMO_LOGIN", "1").lower() not in ("0", "false", "no", "off")

# Gulf of Guinea working box (lon/lat). Incident positions outside it are rejected.
AOI = {"lon0": -5.0, "lon1": 12.0, "lat0": -2.0, "lat1": 8.0}


def secret_key() -> bytes:
    """Signing key for tokens: env var, else a random key kept in var/ (created once)."""
    env = os.environ.get("GULFEYE_SECRET")
    if env:
        return env.encode()
    VAR.mkdir(parents=True, exist_ok=True)
    path = VAR / "secret.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        os.chmod(path, 0o600)
    return path.read_text().strip().encode()
