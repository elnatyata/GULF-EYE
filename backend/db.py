"""SQLite storage for the MVP. The same tables exist for PostgreSQL/PostGIS in schema_postgis.sql.

Positions are stored as plain lat/lon columns here; in PostGIS they become geography(Point, 4326) columns with GiST
indexes, and the spatial queries in ml/correlation.py map to ST_DWithin.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS agencies (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL,
  agency_id INTEGER NOT NULL REFERENCES agencies(id), pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  failed INTEGER NOT NULL DEFAULT 0, locked_until TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revoked_tokens (jti TEXT PRIMARY KEY, exp INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS registry (
  vessel_id TEXT PRIMARY KEY, imo TEXT, mmsi TEXT, name TEXT, flag TEXT, call_sign TEXT, owner TEXT, manager TEXT,
  built INTEGER, gross_tonnage INTEGER, length_m INTEGER, class_society TEXT, flag_history TEXT, former_names TEXT,
  last_psc TEXT,
  watchlist TEXT, notes TEXT, source TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS positions_ingested (
  id INTEGER PRIMARY KEY, vessel_id TEXT NOT NULL, t TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
  sog REAL, cog REAL, source TEXT NOT NULL, received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pos_vessel ON positions_ingested(vessel_id, t);
CREATE TABLE IF NOT EXISTS vessel_risk (
  vessel_id TEXT PRIMARY KEY, score REAL NOT NULL, level TEXT NOT NULL, indicators TEXT NOT NULL, reasons TEXT NOT NULL,
  steps TEXT NOT NULL, features TEXT NOT NULL, ml_score REAL, model_version TEXT NOT NULL, computed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY, vessel_id TEXT, kind TEXT NOT NULL, level TEXT NOT NULL, score REAL NOT NULL,
  lat REAL, lon REAL, observed_at TEXT NOT NULL, title TEXT NOT NULL, behaviours TEXT NOT NULL, evidence TEXT NOT NULL,
  actions TEXT NOT NULL, status TEXT NOT NULL, outcome TEXT, assigned_agency INTEGER, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, source TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS alert_history (
  id INTEGER PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alerts(id), user_id INTEGER, from_status TEXT,
  to_status TEXT NOT NULL, note TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, lat REAL NOT NULL,
  lon REAL NOT NULL, place TEXT, occurred_at TEXT NOT NULL, status TEXT NOT NULL, visibility TEXT NOT NULL,
  agency_id INTEGER NOT NULL REFERENCES agencies(id), reporter_id INTEGER, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, source TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_inc_time ON incidents(occurred_at);
CREATE TABLE IF NOT EXISTS incident_vessels (
  incident_id TEXT NOT NULL REFERENCES incidents(id), vessel_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'involved', PRIMARY KEY (incident_id, vessel_id)
);
CREATE TABLE IF NOT EXISTS incident_history (
  id INTEGER PRIMARY KEY, incident_id TEXT NOT NULL, user_id INTEGER, from_status TEXT, to_status TEXT NOT NULL,
  note TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id), filename TEXT NOT NULL,
  stored_name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
  uploaded_by INTEGER, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY, target_type TEXT NOT NULL, target_id TEXT NOT NULL, user_id INTEGER NOT NULL,
  body TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shares (
  id INTEGER PRIMARY KEY, target_type TEXT NOT NULL, target_id TEXT NOT NULL, from_agency INTEGER NOT NULL,
  to_agency INTEGER NOT NULL, user_id INTEGER NOT NULL, message TEXT, at TEXT NOT NULL, read_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER, email TEXT, agency TEXT, action TEXT NOT NULL,
  target TEXT, detail TEXT, ip TEXT, ok INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit_log(at);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- agency data intake (prototype of the inter-agency integration; phase 2 connectors call the same intake)
CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY, agency_id INTEGER NOT NULL REFERENCES agencies(id), name TEXT NOT NULL, prefix TEXT UNIQUE NOT NULL,
  key_hash TEXT NOT NULL, scopes TEXT NOT NULL, created_by INTEGER, created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY, agency_id INTEGER NOT NULL REFERENCES agencies(id), user_id INTEGER, api_key_id INTEGER,
  channel TEXT NOT NULL, kind TEXT NOT NULL, filename TEXT, received_at TEXT NOT NULL, records INTEGER NOT NULL,
  accepted INTEGER NOT NULL, rejected INTEGER NOT NULL, errors TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL REFERENCES submissions(id), agency_id INTEGER NOT NULL,
  kind TEXT NOT NULL, ref TEXT, observed_at TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, vessel_id TEXT,
  match TEXT, reported_mmsi TEXT, reported_name TEXT, length_m REAL, speed_kn REAL, course REAL, confidence REAL,
  description TEXT, visibility TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_obs_time ON observations(observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_obs_ref ON observations(agency_id, kind, ref) WHERE ref IS NOT NULL;
CREATE TABLE IF NOT EXISTS watchlist (
  id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL REFERENCES submissions(id), agency_id INTEGER NOT NULL,
  ref TEXT, vessel_id TEXT, match TEXT, mmsi TEXT, imo TEXT, name TEXT, level TEXT NOT NULL, reason TEXT NOT NULL,
  listed_at TEXT NOT NULL, valid_until TEXT, visibility TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_wl_ref ON watchlist(agency_id, ref) WHERE ref IS NOT NULL;
-- cross-agency merging: every decision is kept (merged, rejected as not a duplicate, or undone)
CREATE TABLE IF NOT EXISTS incident_merges (
  id INTEGER PRIMARY KEY, primary_id TEXT NOT NULL REFERENCES incidents(id), duplicate_id TEXT NOT NULL REFERENCES incidents(id),
  status TEXT NOT NULL, note TEXT, decided_by INTEGER, decided_at TEXT NOT NULL, undone_by INTEGER, undone_at TEXT
);
"""

_lock = threading.RLock()
_conn = None


def connect():
    global _conn
    with _lock:
        if _conn is None:
            config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys = ON")
            _conn.execute("PRAGMA journal_mode = WAL")
            _conn.executescript(SCHEMA)
            _migrate(_conn)
        return _conn


def _migrate(conn):
    """Add columns introduced after a database was created (SQLite has no ADD COLUMN IF NOT EXISTS)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(incidents)")}
    if "merged_into" not in cols:
        conn.execute("ALTER TABLE incidents ADD COLUMN merged_into TEXT")


def reset_connection():
    """Used by tests to point at a fresh database."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


@contextmanager
def tx():
    conn = connect()
    with _lock:
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in connect().execute(sql, args).fetchall()]


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def run(sql, args=()):
    with _lock:
        return connect().execute(sql, args)


def get_meta(key, default=None):
    row = one("SELECT value FROM meta WHERE key = ?", (key,))
    return json.loads(row["value"]) if row else default


def set_meta(key, value):
    run("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))


def loads(row, *fields):
    """Decode JSON text columns in place."""
    if row is None:
        return None
    for f in fields:
        if row.get(f) is not None:
            row[f] = json.loads(row[f])
    return row
