-- GulfEye production schema for PostgreSQL 15+ with PostGIS 3.
-- The MVP runs on SQLite (backend/db.py) with the same tables; positions there are lat/lon columns.
-- Here they are geography points with GiST indexes so correlation queries ("incidents within 15 km of a track",
-- "alerts within 25 km") become ST_DWithin lookups instead of Python loops.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE agencies (id SERIAL PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL);

CREATE TABLE users (
  id SERIAL PRIMARY KEY, email CITEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','analyst','operator','viewer')),
  agency_id INT NOT NULL REFERENCES agencies(id), pw_hash TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
  failed INT NOT NULL DEFAULT 0, locked_until TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE revoked_tokens (jti TEXT PRIMARY KEY, exp BIGINT NOT NULL);

CREATE TABLE registry (
  vessel_id TEXT PRIMARY KEY, imo TEXT, mmsi TEXT, name TEXT, flag TEXT, call_sign TEXT, owner TEXT, manager TEXT,
  built INT, gross_tonnage INT, length_m INT, class_society TEXT, flag_history JSONB, former_names JSONB, last_psc JSONB,
  watchlist TEXT, notes TEXT, source TEXT NOT NULL, synthetic BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX ix_registry_owner ON registry(owner);

-- AIS positions from every adapter (file, push ingest, licensed feeds)
CREATE TABLE positions (
  id BIGSERIAL PRIMARY KEY, vessel_id TEXT NOT NULL, t TIMESTAMPTZ NOT NULL, geom GEOGRAPHY(POINT, 4326) NOT NULL,
  sog REAL, cog REAL, source TEXT NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_positions_vessel_t ON positions(vessel_id, t);
CREATE INDEX ix_positions_geom ON positions USING GIST(geom);

CREATE TABLE vessel_risk (
  vessel_id TEXT PRIMARY KEY, score REAL NOT NULL, level TEXT NOT NULL, indicators JSONB NOT NULL, reasons JSONB NOT NULL,
  steps JSONB NOT NULL, features JSONB NOT NULL, ml_score REAL, model_version TEXT NOT NULL, computed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE alerts (
  id TEXT PRIMARY KEY, vessel_id TEXT, kind TEXT NOT NULL, level TEXT NOT NULL, score REAL NOT NULL,
  geom GEOGRAPHY(POINT, 4326), observed_at TIMESTAMPTZ NOT NULL, title TEXT NOT NULL, behaviours JSONB NOT NULL,
  evidence JSONB NOT NULL, actions JSONB NOT NULL, status TEXT NOT NULL CHECK (status IN ('new','acknowledged','investigating','closed')),
  outcome TEXT, assigned_agency INT REFERENCES agencies(id), created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL, synthetic BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_alerts_geom ON alerts USING GIST(geom);
CREATE TABLE alert_history (
  id BIGSERIAL PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alerts(id), user_id INT REFERENCES users(id),
  from_status TEXT, to_status TEXT NOT NULL, note TEXT, at TIMESTAMPTZ NOT NULL
);

CREATE TABLE incidents (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, geom GEOGRAPHY(POINT, 4326) NOT NULL,
  place TEXT, occurred_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('reported','verified','under_investigation','closed')),
  visibility TEXT NOT NULL CHECK (visibility IN ('shared','agency')), agency_id INT NOT NULL REFERENCES agencies(id),
  reporter_id INT REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL, synthetic BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_incidents_geom ON incidents USING GIST(geom);
CREATE INDEX ix_incidents_time ON incidents(occurred_at);
CREATE TABLE incident_vessels (
  incident_id TEXT NOT NULL REFERENCES incidents(id), vessel_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'involved' CHECK (role IN ('suspect','victim','witness','involved')), PRIMARY KEY (incident_id, vessel_id)
);
CREATE TABLE incident_history (
  id BIGSERIAL PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id), user_id INT REFERENCES users(id),
  from_status TEXT, to_status TEXT NOT NULL, note TEXT, at TIMESTAMPTZ NOT NULL
);
CREATE TABLE attachments (
  id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id), filename TEXT NOT NULL, stored_name TEXT NOT NULL,
  mime TEXT NOT NULL, size INT NOT NULL, sha256 TEXT NOT NULL, uploaded_by INT REFERENCES users(id), at TIMESTAMPTZ NOT NULL
);

CREATE TABLE comments (id BIGSERIAL PRIMARY KEY, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  user_id INT NOT NULL REFERENCES users(id), body TEXT NOT NULL, at TIMESTAMPTZ NOT NULL);
CREATE TABLE shares (id BIGSERIAL PRIMARY KEY, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  from_agency INT NOT NULL REFERENCES agencies(id), to_agency INT NOT NULL REFERENCES agencies(id),
  user_id INT NOT NULL REFERENCES users(id), message TEXT, at TIMESTAMPTZ NOT NULL, read_at TIMESTAMPTZ);

-- append-only: revoke UPDATE/DELETE from the application role in production
CREATE TABLE audit_log (id BIGSERIAL PRIMARY KEY, at TIMESTAMPTZ NOT NULL, user_id INT, email TEXT, agency TEXT,
  action TEXT NOT NULL, target TEXT, detail JSONB, ip INET, ok BOOLEAN NOT NULL DEFAULT TRUE);
CREATE INDEX ix_audit_at ON audit_log(at);
CREATE TABLE meta (key TEXT PRIMARY KEY, value JSONB NOT NULL);

-- Example: incidents within 15 km of a vessel's track in the last 12 months
-- SELECT DISTINCT i.* FROM incidents i JOIN positions p ON p.vessel_id = $1
--   WHERE i.occurred_at > now() - interval '12 months' AND ST_DWithin(i.geom, p.geom, 15000);
