"""Agency data intake (prototype of inter-agency integration).

Agencies submit data in three ways, all going through the same validation and storage (ingest_records):

    1. CSV upload in the Data intake screen            POST /api/intake/{kind}/csv     (signed-in user)
    2. JSON from an agency system with an API key       POST /api/intake/{kind}         header X-API-Key: ge_xxx_yyy
    3. JSON from a signed-in user (same endpoint)       POST /api/intake/{kind}         bearer token

Kinds: sightings, patrol_reports, radar_contacts, watchlist, incidents, ais_positions.

Phase 2: direct connectors to NIMASA / Navy / coastal radar systems become scheduled clients of this same intake
(or adapters that call ingest_records), so the record contract defined here is the integration contract.
Every record keeps its source agency; records marked visibility=agency are seen only by that agency and admins and
are not used by the shared risk engine (so their content cannot leak through alert evidence).
"""
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from . import audit, config, db, security

router = APIRouter(prefix="/api")

KINDS = {
    "sightings": "Vessel sightings (visual, patrol or aircraft)",
    "patrol_reports": "Patrol reports",
    "radar_contacts": "Radar contacts (coastal or ship radar)",
    "watchlist": "Watchlist entries",
    "incidents": "Incident reports (batch)",
    "ais_positions": "AIS positions",
}
OBS_KINDS = {"sightings": "sighting", "patrol_reports": "patrol_report", "radar_contacts": "radar_contact"}
MAX_RECORDS = 2000


# ------------------------------------------------------------------ record models (the integration contract)
def _time(v: str) -> str:
    try:
        t = dt.datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("use ISO 8601 UTC, e.g. 2026-11-04T05:40:00Z")
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    now = dt.datetime.now(dt.timezone.utc)
    if t > now + dt.timedelta(minutes=5) or t < now - dt.timedelta(days=730):
        raise ValueError("must be within the last two years and not in the future")
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _imo_ok(imo: str) -> bool:
    d = [int(c) for c in imo]
    return sum(x * w for x, w in zip(d[:6], range(7, 1, -1))) % 10 == d[6]


class _Base(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    ref: str | None = Field(default=None, max_length=60, description="the agency's own record id; used to reject duplicates")
    visibility: Literal["shared", "agency"] = "shared"


class _Pos(_Base):
    observed_at: str
    lat: float
    lon: float

    @field_validator("observed_at")
    @classmethod
    def _t(cls, v):
        return _time(v)

    @model_validator(mode="after")
    def _aoi(self):
        a = config.AOI
        if not (a["lat0"] <= self.lat <= a["lat1"] and a["lon0"] <= self.lon <= a["lon1"]):
            raise ValueError(f"position {self.lat}, {self.lon} is outside the Gulf of Guinea area")
        return self


class SightingIn(_Pos):
    mmsi: str | None = Field(default=None, pattern=r"^\d{9}$")
    imo: str | None = Field(default=None, pattern=r"^\d{7}$")
    vessel_name: str | None = Field(default=None, max_length=80)
    length_m: float | None = Field(default=None, gt=0, le=500)
    speed_kn: float | None = Field(default=None, ge=0, le=60)
    course: float | None = Field(default=None, ge=0, lt=360)
    description: str = Field(default="", max_length=1000)


class PatrolReportIn(SightingIn):
    description: str = Field(min_length=3, max_length=2000)
    unit: str | None = Field(default=None, max_length=60)


class RadarContactIn(_Pos):
    mmsi: str | None = Field(default=None, pattern=r"^\d{9}$")
    length_m: float | None = Field(default=None, gt=0, le=500)
    speed_kn: float | None = Field(default=None, ge=0, le=60)
    course: float | None = Field(default=None, ge=0, lt=360)
    confidence: float | None = Field(default=None, ge=0, le=1)
    sensor: str | None = Field(default=None, max_length=60)
    description: str = Field(default="", max_length=1000)


class WatchlistIn(_Base):
    mmsi: str | None = Field(default=None, pattern=r"^\d{9}$")
    imo: str | None = Field(default=None, pattern=r"^\d{7}$")
    name: str | None = Field(default=None, max_length=80)
    level: Literal["monitor", "suspect"] = "monitor"
    reason: str = Field(min_length=3, max_length=500)
    valid_until: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def _ident(self):
        if not (self.mmsi or self.imo or self.name):
            raise ValueError("give at least one of mmsi, imo or name")
        if self.imo and not _imo_ok(self.imo):
            raise ValueError(f"IMO {self.imo} has an invalid check digit")
        return self


class IncidentRecordIn(_Pos):
    type: str
    title: str = Field(min_length=3, max_length=140)
    description: str = Field(min_length=3, max_length=4000)
    place: str | None = Field(default=None, max_length=120)
    mmsi: list[str] = Field(default_factory=list, max_length=10)
    role: Literal["suspect", "victim", "witness", "involved"] = "involved"

    @field_validator("mmsi", mode="before")
    @classmethod
    def _split(cls, v):
        if isinstance(v, str):
            v = [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]
        for x in v:
            if not (len(x) == 9 and x.isdigit()):
                raise ValueError(f"mmsi {x!r} must be 9 digits")
        return v


class AISPositionIn(_Pos):
    mmsi: str = Field(pattern=r"^\d{9}$")
    speed_kn: float = Field(default=0, ge=0, le=60)
    course: float = Field(default=0, ge=0, lt=360)


MODELS = {"sightings": SightingIn, "patrol_reports": PatrolReportIn, "radar_contacts": RadarContactIn,
          "watchlist": WatchlistIn, "incidents": IncidentRecordIn, "ais_positions": AISPositionIn}
EXAMPLES = {
    "sightings": {"ref": "NN-SGT-0412", "observed_at": "2026-11-04T06:10:00Z", "lat": 3.45, "lon": 8.76, "mmsi": "610438987",
                  "vessel_name": "FV Red Lantern", "speed_kn": 3.5, "course": 140, "description": "Two canoes alongside, men on deck"},
    "patrol_reports": {"ref": "NN-PR-0098", "observed_at": "2026-11-04T07:00:00Z", "lat": 3.9, "lon": 6.95, "unit": "NNS patrol craft",
                       "description": "Sweep of the Bonny fairway; fast skiff fled north-west at our approach", "mmsi": ""},
    "radar_contacts": {"ref": "CR-77812", "observed_at": "2026-11-04T05:50:00Z", "lat": 5.902, "lon": 3.903, "length_m": 55,
                       "speed_kn": 0.2, "confidence": 0.8, "sensor": "Lagos coastal radar"},
    "watchlist": {"ref": "MPOL-WL-21", "mmsi": "613658626", "imo": "", "name": "MT Bight Runner", "level": "monitor",
                  "reason": "Linked to a 2026 fuel-smuggling investigation", "valid_until": "2027-06-30"},
    "incidents": {"ref": "NPA-INC-311", "type": "armed_robbery", "title": "Stores stolen at anchor", "observed_at": "2026-11-03T02:30:00Z",
                  "lat": 6.33, "lon": 3.36, "place": "Lagos anchorage", "description": "Robbers boarded at night and took mooring ropes.",
                  "mmsi": "610765617", "role": "victim"},
    "ais_positions": {"mmsi": "351559081", "observed_at": "2026-11-04T07:40:00Z", "lat": 4.95, "lon": 5.05, "speed_kn": 6, "course": 120},
}


# ------------------------------------------------------------------ helpers
def _known_vessels():
    from .app import ais_data
    v = {vid: info["name"] for vid, info in ais_data()["vessels"].items()}
    reg = db.q("SELECT vessel_id, imo, name FROM registry")
    return v, {r["imo"]: r["vessel_id"] for r in reg if r["imo"]}, {r["name"].lower(): r["vessel_id"] for r in reg}


def match_vessel(mmsi=None, imo=None, name=None, cache=None):
    """Resolve the vessel an agency record is about. Returns (vessel_id or None, how it was matched)."""
    by_id, by_imo, by_name = cache or _known_vessels()
    if mmsi and mmsi in by_id:
        return mmsi, "mmsi"
    if imo and imo in by_imo:
        return by_imo[imo], "imo"
    if name and name.lower() in by_name:
        return by_name[name.lower()], "name"
    if mmsi:
        return None, "mmsi not in the vessel picture"
    return None, "unidentified"


def _clean(row: dict) -> dict:
    """CSV cells come as strings: drop empty ones so optional fields stay optional."""
    return {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()
            if k and v is not None and not (isinstance(v, str) and v.strip() == "")}


def ingest_records(agency: dict, kind: str, records: list, *, user=None, key=None, channel="api", filename=None,
                   synthetic=False) -> dict:
    """Validate and store a batch. Bad records are rejected one by one with a reason; good ones are kept."""
    if kind not in MODELS:
        raise HTTPException(404, f"unknown kind; use one of {', '.join(KINDS)}")
    if not isinstance(records, list) or not records:
        raise HTTPException(422, "send a non-empty list of records")
    if len(records) > MAX_RECORDS:
        raise HTTPException(413, f"at most {MAX_RECORDS} records per submission")
    now = audit.now_iso()
    model = MODELS[kind]
    cache = _known_vessels()
    ok, errors = [], []
    for n, raw in enumerate(records, 1):
        try:
            rec = model.model_validate(_clean(raw) if isinstance(raw, dict) else raw)
        except ValidationError as e:
            errors.append({"record": n, "ref": (raw or {}).get("ref") if isinstance(raw, dict) else None,
                           "error": "; ".join(f"{'.'.join(map(str, x['loc'])) or 'record'}: {x['msg'].replace('Value error, ', '')}" for x in e.errors())})
            continue
        if kind == "incidents":
            from .app import INCIDENT_TYPES
            if rec.type not in INCIDENT_TYPES:
                errors.append({"record": n, "ref": rec.ref, "error": f"type must be one of {', '.join(INCIDENT_TYPES)}"})
                continue
        ok.append((n, rec))

    created = []
    with db.tx() as c:
        cur = c.execute("""INSERT INTO submissions(agency_id, user_id, api_key_id, channel, kind, filename, received_at, records,
                           accepted, rejected, errors, synthetic) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (agency["id"], user["id"] if user else None, key["id"] if key else None, channel, kind, filename, now,
                         len(records), 0, 0, "[]", int(synthetic)))
        sid = cur.lastrowid
        for n, r in ok:
            if r.ref and kind in OBS_KINDS and c.execute("SELECT 1 FROM observations WHERE agency_id=? AND kind=? AND ref=?",
                                                         (agency["id"], OBS_KINDS[kind], r.ref)).fetchone():
                errors.append({"record": n, "ref": r.ref, "error": "duplicate ref: already submitted"})
                continue
            if r.ref and kind == "watchlist" and c.execute("SELECT 1 FROM watchlist WHERE agency_id=? AND ref=?", (agency["id"], r.ref)).fetchone():
                errors.append({"record": n, "ref": r.ref, "error": "duplicate ref: already submitted"})
                continue
            if kind in OBS_KINDS:
                vid, how = match_vessel(getattr(r, "mmsi", None), getattr(r, "imo", None), getattr(r, "vessel_name", None), cache)
                desc = r.description
                if kind == "patrol_reports" and r.unit:
                    desc = f"[{r.unit}] {desc}"
                if kind == "radar_contacts" and r.sensor:
                    desc = f"[{r.sensor}] {desc}".strip()
                cur = c.execute("""INSERT INTO observations(submission_id, agency_id, kind, ref, observed_at, lat, lon, vessel_id, match,
                                   reported_mmsi, reported_name, length_m, speed_kn, course, confidence, description, visibility, synthetic, created_at)
                                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (sid, agency["id"], OBS_KINDS[kind], r.ref, r.observed_at, r.lat, r.lon, vid, how, getattr(r, "mmsi", None),
                                 getattr(r, "vessel_name", None), r.length_m, r.speed_kn, r.course, getattr(r, "confidence", None),
                                 desc, r.visibility, int(synthetic), now))
                created.append(f"OBS-{cur.lastrowid}")
            elif kind == "watchlist":
                vid, how = match_vessel(r.mmsi, r.imo, r.name, cache)
                cur = c.execute("""INSERT INTO watchlist(submission_id, agency_id, ref, vessel_id, match, mmsi, imo, name, level, reason,
                                   listed_at, valid_until, visibility, synthetic) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (sid, agency["id"], r.ref, vid, how, r.mmsi, r.imo, r.name, r.level, r.reason, now, r.valid_until,
                                 r.visibility, int(synthetic)))
                created.append(f"WL-{cur.lastrowid}")
            elif kind == "incidents":
                n_inc = c.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] + 1
                iid = f"INC-{r.observed_at[:4]}-{n_inc:04d}"
                c.execute("""INSERT INTO incidents(id, type, title, description, lat, lon, place, occurred_at, status, visibility, agency_id,
                             reporter_id, created_at, updated_at, source, synthetic) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (iid, r.type, r.title, r.description + (f" (agency ref {r.ref})" if r.ref else ""), r.lat, r.lon, r.place,
                           r.observed_at, "reported", r.visibility, agency["id"], user["id"] if user else None, now, now,
                           f"agency_intake:{channel}", int(synthetic)))
                c.execute("INSERT INTO incident_history(incident_id, user_id, from_status, to_status, note, at) VALUES(?,?,?,?,?,?)",
                          (iid, user["id"] if user else None, None, "reported", f"Received from {agency['code']} via {channel} (submission {sid}).", now))
                for m in r.mmsi:
                    vid, _ = match_vessel(m, cache=cache)
                    if vid:
                        c.execute("INSERT OR IGNORE INTO incident_vessels(incident_id, vessel_id, role) VALUES(?,?,?)", (iid, vid, r.role))
                created.append(iid)
            elif kind == "ais_positions":
                c.execute("INSERT INTO positions_ingested(vessel_id, t, lat, lon, sog, cog, source, received_at) VALUES(?,?,?,?,?,?,?,?)",
                          (r.mmsi, r.observed_at, r.lat, r.lon, r.speed_kn, r.course, f"agency:{agency['code']}", now))
                created.append(r.mmsi)
        errors.sort(key=lambda e: e["record"])
        c.execute("UPDATE submissions SET accepted=?, rejected=?, errors=? WHERE id=?",
                  (len(created), len(errors), json.dumps(errors), sid))
    return {"submission": sid, "kind": kind, "received": len(records), "accepted": len(created), "rejected": len(errors),
            "created": created, "errors": errors}


def _after_intake(result, agency, actor_label, user=None, ip=None, key=None):
    from .app import broadcast
    from .ml.engine import run_engine
    audit.log(user, "intake.submit", f"{result['kind']}:{result['submission']}",
              {"agency": agency["code"], "via": actor_label, "accepted": result["accepted"], "rejected": result["rejected"]},
              ip=ip, email=None if user else f"api-key:{key['name']}" if key else None)
    engine = None
    if result["accepted"]:             # new evidence can change risk scores
        engine = run_engine(user)
        for aid in engine["created"]:
            broadcast({"type": "alert.created", "id": aid})
    broadcast({"type": "intake.received", "kind": result["kind"], "by": agency["code"], "accepted": result["accepted"]})
    result["engine"] = engine
    return result


# ------------------------------------------------------------------ authentication: user token or agency API key
def _key_from_header(request: Request):
    raw = request.headers.get("x-api-key", "")
    parts = raw.split("_")
    if len(parts) != 3 or parts[0] != "ge":
        return None
    k = db.one("SELECT * FROM api_keys WHERE prefix = ? AND revoked_at IS NULL", (parts[1],))
    if not k or not hmac.compare_digest(k["key_hash"], hashlib.sha256(raw.encode()).hexdigest()):
        return None
    return k


def intake_actor(request: Request):
    """Either a signed-in user with intake:submit, or a valid agency API key."""
    if request.headers.get("x-api-key"):
        k = _key_from_header(request)
        if not k:
            audit.log(None, "intake.auth", None, "invalid or revoked API key", ip=request.client.host if request.client else None,
                      ok=False, email="api-key")
            raise HTTPException(401, "invalid or revoked API key")
        agency = db.one("SELECT * FROM agencies WHERE id = ?", (k["agency_id"],))
        db.run("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (audit.now_iso(), k["id"]))
        return {"key": {**k, "scopes": json.loads(k["scopes"])}, "user": None, "agency": agency}
    from .app import current_user
    user = current_user(request)
    if "intake:submit" not in security.perms(user["role"]):
        audit.log(user, "denied", "intake:submit", ip=user["ip"], ok=False)
        raise HTTPException(403, "your role cannot submit agency data")
    agency = db.one("SELECT * FROM agencies WHERE id = ?", (user["agency_id"],))
    return {"key": None, "user": user, "agency": agency}


# ------------------------------------------------------------------ routes
@router.get("/intake/kinds")
def kinds():
    out = []
    for k, label in KINDS.items():
        schema = MODELS[k].model_json_schema()
        out.append({"kind": k, "label": label, "fields": list(schema["properties"]), "required": schema.get("required", []),
                    "example": EXAMPLES[k]})
    return {"kinds": out, "max_records": MAX_RECORDS}


@router.get("/intake/templates/{kind}.csv")
def template(kind: str):
    if kind not in MODELS:
        raise HTTPException(404, "unknown kind")
    fields = [f for f in MODELS[kind].model_json_schema()["properties"]]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerow({f: EXAMPLES[kind].get(f, "") for f in fields})
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="gulfeye_{kind}_template.csv"'})


class IntakeIn(BaseModel):
    records: list[dict] = Field(min_length=1, max_length=MAX_RECORDS)


@router.post("/intake/{kind}")
def submit_json(kind: str, body: IntakeIn, request: Request, actor=Depends(intake_actor)):
    if kind not in MODELS:
        raise HTTPException(404, "unknown kind")
    key, user, agency = actor["key"], actor["user"], actor["agency"]
    if key and kind not in key["scopes"]:
        raise HTTPException(403, f"this API key is not allowed to submit {kind}")
    res = ingest_records(agency, kind, body.records, user=user, key=key, channel="api" if key else "json")
    return _after_intake(res, agency, f"key {key['name']}" if key else "user", user=user,
                         ip=request.client.host if request.client else None, key=key)


@router.post("/intake/{kind}/csv")
async def submit_csv(kind: str, request: Request, file: UploadFile = File(...), actor=Depends(intake_actor)):
    if kind not in MODELS:
        raise HTTPException(404, "unknown kind")
    key, user, agency = actor["key"], actor["user"], actor["agency"]
    if key and kind not in key["scopes"]:
        raise HTTPException(403, f"this API key is not allowed to submit {kind}")
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file larger than 5 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(415, "the CSV must be UTF-8 text")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise HTTPException(422, "the CSV has no data rows (first line must be the column names)")
    allowed = set(MODELS[kind].model_json_schema()["properties"])
    unknown = [c for c in rows[0] if c and c.strip() not in allowed]
    if unknown:
        raise HTTPException(422, f"unknown column(s): {', '.join(unknown)}. Download the template for the expected columns.")
    res = ingest_records(agency, kind, rows, user=user, key=key, channel="csv", filename=(file.filename or "")[:120])
    return _after_intake(res, agency, "csv", user=user, ip=request.client.host if request.client else None, key=key)


def _reader(request: Request):
    from .app import current_user
    user = current_user(request)
    if "read" not in security.perms(user["role"]):
        raise HTTPException(403, "no read permission")
    return user


def _visible_sql(user, alias=""):
    p = f"{alias}." if alias else ""
    if user["role"] == "admin":
        return "1=1", ()
    return f"({p}visibility = 'shared' OR {p}agency_id = ?)", (user["agency_id"],)


def _codes():
    return {a["id"]: a["code"] for a in db.q("SELECT id, code FROM agencies")}


@router.get("/intake/submissions")
def submissions(user=Depends(_reader), limit: int = 100):
    codes = _codes()
    where, args = ("1=1", ()) if user["role"] == "admin" else ("agency_id = ?", (user["agency_id"],))
    rows = db.q(f"SELECT * FROM submissions WHERE {where} ORDER BY id DESC LIMIT ?", (*args, min(limit, 500)))
    users = {u["id"]: u["name"] for u in db.q("SELECT id, name FROM users")}
    keys = {k["id"]: k["name"] for k in db.q("SELECT id, name FROM api_keys")}
    for r in rows:
        r["agency"] = codes[r["agency_id"]]
        r["errors"] = json.loads(r["errors"])
        r["by"] = keys.get(r["api_key_id"]) and f"API key: {keys[r['api_key_id']]}" or users.get(r["user_id"]) or "seed"
    stats = db.q("""SELECT agency_id, COUNT(*) AS submissions, SUM(accepted) AS accepted, SUM(rejected) AS rejected, MAX(received_at) AS last
                    FROM submissions GROUP BY agency_id""")
    for s in stats:
        s["agency"] = codes[s["agency_id"]]
    return {"submissions": rows, "by_agency": stats, "scope": "all agencies" if user["role"] == "admin" else codes[user["agency_id"]]}


def _obs_out(o, codes, names):
    o["id_label"] = f"OBS-{o['id']}"
    o["agency"] = codes[o["agency_id"]]
    o["vessel_name"] = names.get(o["vessel_id"]) if o["vessel_id"] else None
    return o


@router.get("/observations")
def observations(user=Depends(_reader), date_from: str = "", kind: str = "", vessel: str = "", limit: int = 500):
    where, args = _visible_sql(user)
    sql, params = f"SELECT * FROM observations WHERE {where}", list(args)
    if date_from:
        sql += " AND observed_at >= ?"
        params.append(date_from)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if vessel:
        sql += " AND vessel_id = ?"
        params.append(vessel)
    rows = db.q(sql + " ORDER BY observed_at DESC LIMIT ?", (*params, min(limit, 2000)))
    codes = _codes()
    names = _known_vessels()[0]
    return {"observations": [_obs_out(o, codes, names) for o in rows]}


@router.get("/observations/{oid}")
def observation(oid: int, user=Depends(_reader)):
    o = db.one("SELECT * FROM observations WHERE id = ?", (oid,))
    if not o or (o["visibility"] == "agency" and o["agency_id"] != user["agency_id"] and user["role"] != "admin"):
        raise HTTPException(404, "not found")
    from .ml.geo import haversine_km
    out = _obs_out(o, _codes(), _known_vessels()[0])
    sub = db.one("SELECT id, channel, received_at, filename FROM submissions WHERE id = ?", (o["submission_id"],))
    out["submission"] = sub
    near = []
    for a in db.q("SELECT id, title, level, lat, lon FROM alerts WHERE status != 'closed'"):
        d = haversine_km(o["lat"], o["lon"], a["lat"], a["lon"])
        if d <= 25:
            near.append({**a, "distance_km": round(d, 1)})
    out["nearby_alerts"] = sorted(near, key=lambda x: x["distance_km"])[:6]
    audit.log(user, "observation.view", f"OBS-{oid}", ip=user["ip"])
    return out


@router.get("/watchlist")
def watchlist(user=Depends(_reader)):
    where, args = _visible_sql(user)
    rows = db.q(f"SELECT * FROM watchlist WHERE {where} ORDER BY listed_at DESC", args)
    codes = _codes()
    names = _known_vessels()[0]
    for r in rows:
        r["agency"] = codes[r["agency_id"]]
        r["vessel_name"] = names.get(r["vessel_id"]) if r["vessel_id"] else None
    return {"entries": rows}


class WatchlistPatch(BaseModel):
    active: bool


@router.patch("/watchlist/{wid}")
def patch_watchlist(wid: int, body: WatchlistPatch, request: Request):
    from .app import current_user
    user = current_user(request)
    w = db.one("SELECT * FROM watchlist WHERE id = ?", (wid,))
    if not w:
        raise HTTPException(404, "not found")
    if user["role"] != "admin" and (w["agency_id"] != user["agency_id"] or "intake:submit" not in security.perms(user["role"])):
        raise HTTPException(403, "only the listing agency can change this entry")
    db.run("UPDATE watchlist SET active = ? WHERE id = ?", (int(body.active), wid))
    audit.log(user, "watchlist.update", f"WL-{wid}", {"active": body.active}, ip=user["ip"])
    from .ml.engine import run_engine
    run_engine(user)
    return {"ok": True}


# ------------------------------------------------------------------ API keys for agency systems
class KeyIn(BaseModel):
    name: str = Field(min_length=3, max_length=60)
    scopes: list[Literal["sightings", "patrol_reports", "radar_contacts", "watchlist", "incidents", "ais_positions"]] = Field(min_length=1)
    agency: str | None = None     # admins may create keys for another agency


def _key_manager(request: Request):
    from .app import current_user
    user = current_user(request)
    if "keys:manage" not in security.perms(user["role"]):
        audit.log(user, "denied", "keys:manage", ip=user["ip"], ok=False)
        raise HTTPException(403, "your role cannot manage API keys")
    return user


@router.get("/apikeys")
def list_keys(user=Depends(_key_manager)):
    where, args = ("1=1", ()) if user["role"] == "admin" else ("agency_id = ?", (user["agency_id"],))
    rows = db.q(f"SELECT id, agency_id, name, prefix, scopes, created_at, last_used_at, revoked_at FROM api_keys WHERE {where} ORDER BY id DESC", args)
    codes = _codes()
    for r in rows:
        r["agency"] = codes[r["agency_id"]]
        r["scopes"] = json.loads(r["scopes"])
    return {"keys": rows}


@router.post("/apikeys", status_code=201)
def create_key(body: KeyIn, user=Depends(_key_manager)):
    agency_id = user["agency_id"]
    if body.agency and body.agency != user["agency_code"]:
        if user["role"] != "admin":
            raise HTTPException(403, "you can only create keys for your own agency")
        ag = db.one("SELECT id FROM agencies WHERE code = ?", (body.agency,))
        if not ag:
            raise HTTPException(422, "unknown agency")
        agency_id = ag["id"]
    prefix = secrets.token_hex(4)
    raw = f"ge_{prefix}_{secrets.token_urlsafe(24).replace('_', '-')}"
    db.run("INSERT INTO api_keys(agency_id, name, prefix, key_hash, scopes, created_by, created_at) VALUES(?,?,?,?,?,?,?)",
           (agency_id, body.name, prefix, hashlib.sha256(raw.encode()).hexdigest(), json.dumps(sorted(set(body.scopes))), user["id"], audit.now_iso()))
    audit.log(user, "apikey.create", body.name, {"prefix": prefix, "agency_id": agency_id, "scopes": body.scopes}, ip=user["ip"])
    return {"key": raw, "prefix": prefix, "note": "Shown once. Store it in the agency system's secret store; GulfEye keeps only a hash."}


@router.delete("/apikeys/{kid}")
def revoke_key(kid: int, user=Depends(_key_manager)):
    k = db.one("SELECT * FROM api_keys WHERE id = ?", (kid,))
    if not k or (user["role"] != "admin" and k["agency_id"] != user["agency_id"]):
        raise HTTPException(404, "not found")
    db.run("UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (audit.now_iso(), kid))
    audit.log(user, "apikey.revoke", k["name"], {"prefix": k["prefix"]}, ip=user["ip"])
    return {"ok": True}


# ------------------------------------------------------------------ demo seed (simulated agency submissions)
def seed_demo():
    if db.one("SELECT COUNT(*) AS n FROM submissions")["n"]:
        return False
    batches = []
    for name in ("agency_submissions.json", "agency_submissions_bulk.json"):
        path = config.SEED_DIR / name
        if path.exists():
            with open(path, encoding="utf-8") as f:
                batches += json.load(f)["batches"]
    if not batches:
        return False
    for b in batches:
        agency = db.one("SELECT * FROM agencies WHERE code = ?", (b["agency"],))
        res = ingest_records(agency, b["kind"], b["records"], channel=b.get("channel", "api"), filename=b.get("filename"), synthetic=True)
        db.run("UPDATE submissions SET received_at = ? WHERE id = ?", (b["received_at"], res["submission"]))
    audit.log(None, "system.seed_intake", None, {"batches": len(batches)}, email="system")
    return True
