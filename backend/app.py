"""GulfEye API (FastAPI). Serves the existing web/ front end and the /api routes.

    python -m backend            # http://127.0.0.1:8765
"""
import asyncio
import csv
import datetime as dt
import hashlib
import importlib.util
import io
import json
import logging
import re
import secrets
import time
from collections import Counter, defaultdict, deque
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import audit, config, db, security, seed
from .adapters import AIS, statuses
from .ml import correlation
from .ml.engine import DISCLAIMER, MODEL_VERSION, run_engine
from .ml.geo import haversine_km, zone_at

log = logging.getLogger("gulfeye")


@asynccontextmanager
async def lifespan(app_: FastAPI):
    config.VAR.mkdir(parents=True, exist_ok=True)
    config.UPLOADS.mkdir(parents=True, exist_ok=True)
    db.connect()
    from . import intake
    fresh = seed.seed(run_engine=False)
    demo = intake.seed_demo()
    if fresh or demo:
        run_engine(None)
    app_.state.loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="GulfEye API", version="1.0", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)

# merge routes first: /api/incidents/duplicates and /api/incidents/merges must win over /api/incidents/{iid}
from .merge import router as merge_router  # noqa: E402

app.include_router(merge_router)

INCIDENT_TYPES = {
    "piracy_attack": "Piracy attack", "armed_robbery": "Sea robbery / armed robbery", "boarding": "Boarding",
    "kidnapping": "Kidnapping", "suspicious_approach": "Suspicious approach or movement", "illegal_fishing": "Illegal fishing",
    "oil_theft": "Oil theft / illegal bunkering", "sts_transfer": "Unauthorised ship-to-ship transfer",
    "smuggling": "Smuggling", "pollution": "Pollution or spill", "search_rescue": "Distress / search and rescue", "other": "Other",
}
INCIDENT_FLOW = {"reported": {"verified", "closed"}, "verified": {"under_investigation", "closed"},
                 "under_investigation": {"closed"}, "closed": {"under_investigation"}}
ALERT_FLOW = {"new": {"acknowledged", "investigating", "closed"}, "acknowledged": {"investigating", "closed"},
              "investigating": {"closed"}, "closed": {"investigating"}}
VESSEL_ROLES = {"suspect", "victim", "witness", "involved"}


# ------------------------------------------------------------------ startup, middleware, errors
@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                                               "connect-src 'self' ws: wss:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    if request.url.path.endswith((".js", ".css", ".html", "/")) or request.url.path.startswith(("/api", "/data")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"error": "internal error"}, status_code=500)


# ------------------------------------------------------------------ auth
def _client_ip(request):
    # Behind a hosting proxy (Render, Railway, nginx) the real address is in X-Forwarded-For.
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if fwd:
            return fwd[:64]
    return request.client.host if request.client else None


def current_user(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else None
    claims = security.read_token(token) if token else None
    if not claims:
        raise HTTPException(401, "not signed in")
    user = db.one("""SELECT u.id, u.email, u.name, u.role, u.agency_id, u.active, a.code AS agency_code, a.name AS agency_name
                     FROM users u JOIN agencies a ON a.id = u.agency_id WHERE u.id = ?""", (claims["sub"],))
    if not user or not user["active"]:
        raise HTTPException(401, "account disabled")
    user["claims"] = claims
    user["ip"] = _client_ip(request)
    return user


def need(perm):
    def dep(user: dict = Depends(current_user)):
        if perm not in security.perms(user["role"]):
            audit.log(user, "denied", perm, ip=user["ip"], ok=False)
            raise HTTPException(403, f"your role ({user['role']}) cannot do this ({perm})")
        return user
    return dep


_ip_hits: dict[str, deque] = defaultdict(deque)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request):
    ip = _client_ip(request) or "?"
    hits = _ip_hits[ip]
    now = time.time()
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= 20:
        raise HTTPException(429, "too many attempts, wait a minute")
    hits.append(now)
    email = body.email.strip().lower()
    u = db.one("SELECT u.*, a.code AS agency_code FROM users u JOIN agencies a ON a.id = u.agency_id WHERE lower(email) = ?", (email,))
    if u and u["locked_until"] and u["locked_until"] > audit.now_iso():
        audit.log(u, "auth.login", email, "account locked", ip=ip, ok=False)
        raise HTTPException(423, "account locked after repeated failures, try again in a few minutes")
    if not u or not u["active"] or not security.verify_password(body.password, u["pw_hash"]):
        if u:
            failed = u["failed"] + 1
            lock = None
            if failed >= config.LOGIN_MAX_FAILURES:
                lock = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=config.LOGIN_LOCK_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
                failed = 0
            db.run("UPDATE users SET failed = ?, locked_until = ? WHERE id = ?", (failed, lock, u["id"]))
        audit.log(u, "auth.login", email, "bad credentials", ip=ip, ok=False, email=email)
        raise HTTPException(401, "wrong email or password")
    db.run("UPDATE users SET failed = 0, locked_until = NULL WHERE id = ?", (u["id"],))
    token, exp = security.make_token(u)
    audit.log(u, "auth.login", email, ip=ip)
    return {"token": token, "expires": exp, "user": _me(u)}


class DemoLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)


def _demo_accounts():
    from . import seed
    return {e: (name, role, ag) for e, name, role, ag in seed.USERS}


@app.get("/api/auth/demo")
def demo_accounts():
    """Public: whether one-click demo sign-in is on, and which demo accounts it offers."""
    if not config.DEMO_LOGIN:
        return {"enabled": False, "accounts": []}
    rows = db.q("SELECT u.email, u.name, u.role, a.code, a.name AS agency_name FROM users u JOIN agencies a ON a.id = u.agency_id WHERE u.active = 1")
    demo = _demo_accounts()
    return {"enabled": True, "accounts": [{"email": r["email"], "name": r["name"], "role": r["role"],
                                           "role_label": security.ROLES[r["role"]]["label"], "agency": r["code"], "agency_name": r["agency_name"]}
                                          for r in rows if r["email"] in demo]}


@app.post("/api/auth/demo")
def demo_login(body: DemoLoginIn, request: Request):
    """Sign in as one of the seeded demo accounts without a password (only while GULFEYE_DEMO_LOGIN is on)."""
    ip = _client_ip(request) or "?"
    if not config.DEMO_LOGIN:
        raise HTTPException(403, "demo sign-in is turned off on this server")
    email = body.email.strip().lower()
    if email not in _demo_accounts():
        raise HTTPException(403, "demo sign-in only works for the demo accounts")
    u = db.one("SELECT u.*, a.code AS agency_code FROM users u JOIN agencies a ON a.id = u.agency_id WHERE lower(email) = ?", (email,))
    if not u or not u["active"]:
        raise HTTPException(403, "this demo account is disabled")
    token, exp = security.make_token(u)
    audit.log(u, "auth.login", email, "one-click demo sign-in", ip=ip)
    return {"token": token, "expires": exp, "user": _me(u)}


def _me(u):
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"],
            "role_label": security.ROLES[u["role"]]["label"], "agency_id": u["agency_id"], "agency": u.get("agency_code"),
            "permissions": sorted(security.perms(u["role"]))}


@app.post("/api/auth/logout")
def logout(user=Depends(current_user)):
    security.revoke(user["claims"])
    audit.log(user, "auth.logout", ip=user["ip"])
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    out = _me(user)
    out["agency_name"] = user["agency_name"]
    return out


# ------------------------------------------------------------------ public status (unchanged contract from serve.py)
def _serve_module():
    spec = importlib.util.spec_from_file_location("gulfeye_serve", config.ROOT / "scripts" / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SERVE = _serve_module()


@app.get("/api/health")
def health():
    return {"ok": True, "app": "GulfEye", "backend": True, "model": MODEL_VERSION}


@app.get("/api/status")
def status():
    import os
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {"available": has_key, "model": SERVE.MODEL if has_key else None}


class InterpretIn(BaseModel):
    text: str = Field(max_length=600)
    context: dict = {}


@app.post("/api/interpret")
def interpret(body: InterpretIn, user=Depends(current_user)):
    try:
        return SERVE.call_model(body.text, body.context)
    except PermissionError:
        raise HTTPException(503, "language model not configured")
    except Exception as exc:  # network or parsing problem: the browser falls back to its rule-based reader
        raise HTTPException(502, str(exc)[:200])


@app.get("/data/{name}.json")
def data_file(name: str, user=Depends(current_user)):
    if not re.fullmatch(r"[a-z_]+", name) or not (config.DATA / f"{name}.json").exists():
        raise HTTPException(404, "not found")
    return FileResponse(config.DATA / f"{name}.json", media_type="application/json")


# ------------------------------------------------------------------ helpers
def demo_now() -> dt.datetime:
    return dt.datetime.fromisoformat(AIS.raw()["meta"]["now"].replace("Z", "+00:00"))


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _users_by_id():
    return {u["id"]: u for u in db.q("SELECT u.id, u.name, a.code AS agency FROM users u JOIN agencies a ON a.id = u.agency_id")}


def _visible(user):
    return lambda inc: security.can_see_incident(user, inc)


_ais_cache = {"mtime": None, "data": None}


def ais_data():
    """AIS tracks (file + ingested rows), cached until the file or the ingest table changes."""
    import os
    key = (os.path.getmtime(AIS.path), db.one("SELECT COUNT(*) AS n FROM positions_ingested")["n"])
    if _ais_cache["mtime"] != key:
        _ais_cache.update(mtime=key, data=AIS.load())
    return _ais_cache["data"]


def risk_rows():
    rows = db.q("SELECT * FROM vessel_risk")
    return {r["vessel_id"]: db.loads(r, "indicators", "reasons", "steps", "features") for r in rows}


def alert_out(a, users=None):
    a = db.loads(dict(a), "behaviours", "evidence", "actions")
    v = ais_data()["vessels"].get(a["vessel_id"]) if a.get("vessel_id") else None
    a["vessel"] = {"id": v["id"], "name": v["name"], "flag": v["flag"], "type": v["type"]} if v else None
    a["zone"] = (zone_at(_geo(), a["lon"], a["lat"]) or {}).get("name") if a.get("lat") is not None else None
    a["disclaimer"] = DISCLAIMER
    return a


_geo_cache = {}


def _geo():
    if "g" not in _geo_cache:
        from .adapters import GEO
        _geo_cache["g"] = GEO.load()
    return _geo_cache["g"]


def incident_out(inc, users=None):
    inc = dict(inc)
    inc["type_label"] = INCIDENT_TYPES.get(inc["type"], inc["type"])
    # a primary incident shows the vessels named in every report merged into it
    inc["vessels"] = db.q("""SELECT iv.vessel_id, iv.role, iv.incident_id FROM incident_vessels iv JOIN incidents i ON i.id = iv.incident_id
                            WHERE i.id = ? OR i.merged_into = ? GROUP BY iv.vessel_id""", (inc["id"], inc["id"]))
    others = db.q("SELECT i.id, a.code FROM incidents i JOIN agencies a ON a.id = i.agency_id WHERE i.merged_into = ?", (inc["id"],))
    inc["merged_reports"] = [o["id"] for o in others]
    names = ais_data()["vessels"]
    for v in inc["vessels"]:
        v["name"] = (names.get(v["vessel_id"]) or db.one("SELECT name FROM registry WHERE vessel_id=?", (v["vessel_id"],)) or {}).get("name")
    ag = db.one("SELECT code, name FROM agencies WHERE id = ?", (inc["agency_id"],))
    inc["agency"] = ag["code"]
    inc["agency_name"] = ag["name"]
    inc["agencies"] = sorted({ag["code"], *[o["code"] for o in others]})
    inc["attachments"] = db.q("SELECT id, filename, mime, size, at FROM attachments WHERE incident_id = ?", (inc["id"],))
    z = zone_at(_geo(), inc["lon"], inc["lat"])
    inc["zone"] = z["name"] if z else None
    return inc


# ------------------------------------------------------------------ realtime
class Hub:
    def __init__(self):
        self.clients: dict[WebSocket, dict] = {}

    async def send(self, event: dict, agency_only: int | None = None):
        dead = []
        for ws, user in list(self.clients.items()):
            if agency_only is not None and user["role"] != "admin" and user["agency_id"] != agency_only:
                continue
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.pop(ws, None)


hub = Hub()


def broadcast(event: dict, agency_only: int | None = None):
    """Schedule a push from sync route handlers (they run in a worker thread)."""
    loop = getattr(app.state, "loop", None)
    if loop:
        asyncio.run_coroutine_threadsafe(hub.send({**event, "at": audit.now_iso()}, agency_only), loop)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = Query("")):
    app.state.loop = asyncio.get_running_loop()
    claims = security.read_token(token)
    if not claims:
        await ws.close(code=4401)
        return
    user = db.one("SELECT id, role, agency_id FROM users WHERE id = ? AND active = 1", (claims["sub"],))
    if not user:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.clients[ws] = user
    await ws.send_json({"type": "hello", "clients": len(hub.clients)})
    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.pop(ws, None)


# ------------------------------------------------------------------ dashboard summary
@app.get("/api/summary")
def summary(user=Depends(need("read"))):
    now = demo_now()
    risk = risk_rows()
    vis = _visible(user)
    incs = [i for i in db.q("SELECT * FROM incidents WHERE merged_into IS NULL") if vis(i)]
    d30, d60 = _iso(now - dt.timedelta(days=30)), _iso(now - dt.timedelta(days=60))
    last30 = [i for i in incs if i["occurred_at"] >= d30]
    prev30 = [i for i in incs if d60 <= i["occurred_at"] < d30]
    alerts = db.q("SELECT * FROM alerts")
    open_alerts = [a for a in alerts if a["status"] != "closed"]
    users = _users_by_id()
    recent = db.q("""SELECT at, action, target, user_id, detail FROM audit_log WHERE action IN
                     ('incident.create','incident.status','alert.status','engine.run','share.create','ingest.ais')
                     ORDER BY id DESC LIMIT 12""")
    for r in recent:
        u = users.get(r["user_id"])
        r["who"] = f"{u['name']} ({u['agency']})" if u else "GulfEye engine"
    return {
        "as_of": _iso(now), "synthetic": True,
        "vessels_monitored": len(ais_data()["vessels"]),
        "suspicious_vessels": sum(1 for r in risk.values() if r["level"] != "low"),
        "risk_levels": dict(Counter(r["level"] for r in risk.values())),
        "active_incidents": sum(1 for i in incs if i["status"] != "closed"),
        "incidents_30d": len(last30), "incidents_prev_30d": len(prev30),
        "incidents_30d_by_type": dict(Counter(i["type"] for i in last30)),
        "open_alerts": len(open_alerts),
        "high_risk_alerts": sum(1 for a in open_alerts if a["level"] in ("high", "critical")),
        "alerts_unacknowledged": sum(1 for a in open_alerts if a["status"] == "new"),
        "recent": recent, "engine": db.get_meta("engine_last_run"),
        "inbox_unread": db.one("SELECT COUNT(*) AS n FROM shares WHERE to_agency = ? AND read_at IS NULL", (user["agency_id"],))["n"],
    }


# ------------------------------------------------------------------ vessels
@app.get("/api/vessels")
def vessels(user=Depends(need("read")), q: str = "", level: str = "", type: str = ""):
    data = ais_data()
    risk = risk_rows()
    reg = {r["vessel_id"]: r for r in db.q("SELECT vessel_id, imo, call_sign, owner FROM registry")}
    open_alerts = Counter(a["vessel_id"] for a in db.q("SELECT vessel_id FROM alerts WHERE status != 'closed'"))
    inc_count = Counter(r["vessel_id"] for r in db.q("SELECT vessel_id FROM incident_vessels"))
    out = []
    for vid, v in data["vessels"].items():
        r = risk.get(vid, {})
        row = {**v, "imo": reg.get(vid, {}).get("imo"), "call_sign": reg.get(vid, {}).get("call_sign"),
               "score": r.get("score", 0), "level": r.get("level", "low"),
               "indicators": [i["code"] for i in r.get("indicators", [])], "open_alerts": open_alerts[vid], "incidents": inc_count[vid]}
        text = f"{v['name']} {vid} {row['imo'] or ''} {row['call_sign'] or ''} {v['flag']}".lower()
        if q and q.lower() not in text:
            continue
        if level and row["level"] != level:
            continue
        if type and v["type"] != type:
            continue
        out.append(row)
    out.sort(key=lambda x: -x["score"])
    return {"vessels": out, "model": MODEL_VERSION, "disclaimer": DISCLAIMER}


@app.get("/api/vessels/{vid}")
def vessel_profile(vid: str, user=Depends(need("read"))):
    data = ais_data()
    v = data["vessels"].get(vid)
    if not v:
        raise HTTPException(404, "unknown vessel")
    rows = sorted((r for r in data["rows"] if r["vessel_id"] == vid), key=lambda r: r["t"])
    risk = risk_rows().get(vid)
    vis = _visible(user)
    idc = correlation.identity_checks(vid, v)
    now = data["meta"]["now_effective"]
    track = [(r["lat"], r["lon"]) for r in rows]
    linked = [incident_out(i) | {"role": i["role"]} for i in correlation.linked_incidents(vid, vis)]
    linked_ids = {i["id"] for i in linked}
    nearby = [incident_out(i) | {"distance_km": i["distance_km"]} for i in correlation.nearby_incidents(track, now, visible=vis)
              if i["id"] not in linked_ids]
    alerts = [alert_out(a) for a in db.q("SELECT * FROM alerts WHERE vessel_id = ? ORDER BY created_at DESC", (vid,))]
    partners = [i for i in (risk or {}).get("indicators", []) if i["code"] in ("rendezvous", "dark_alongside")]
    last = rows[-1] if rows else None
    audit.log(user, "vessel.view", vid, ip=user["ip"])
    return {
        "vessel": {**v, "mmsi": vid, "imo": (idc["registry"] or {}).get("imo"), "call_sign": (idc["registry"] or {}).get("call_sign")},
        "last": {"t": _iso(last["t"]), "lat": last["lat"], "lon": last["lon"], "sog": last["sog"], "cog": last["cog"],
                 "age_min": round((now - last["t"]).total_seconds() / 60)} if last else None,
        "history": [{"t": _iso(r["t"]), "lat": r["lat"], "lon": r["lon"], "sog": r["sog"], "cog": r["cog"], "source": r["source"]} for r in rows],
        "registry": idc["registry"], "identity_issues": idc["issues"], "risk": risk, "alerts": alerts,
        "incidents": linked, "nearby_incidents": nearby, "network": correlation.owner_network(vid), "contacts": partners,
        "comments": _comments("vessel", vid), "disclaimer": DISCLAIMER, "agency": _agency_records(user, vid),
        "consolidated": _consolidated(vid, user),
        "sources": {"ais": "simulated" if AIS.synthetic else "file", "registry": "simulated"},
    }


def _consolidated(vid, user):
    from .merge import consolidated
    return consolidated(vid, user)


def _agency_records(user, vid):
    """Watchlist entries and observations submitted by agencies about this vessel, filtered by visibility."""
    codes = {a["id"]: a["code"] for a in db.q("SELECT id, code FROM agencies")}
    vis = "1=1" if user["role"] == "admin" else "(visibility = 'shared' OR agency_id = ?)"
    args = () if user["role"] == "admin" else (user["agency_id"],)
    wl = db.q(f"SELECT * FROM watchlist WHERE vessel_id = ? AND {vis} ORDER BY listed_at DESC", (vid, *args))
    obs = db.q(f"SELECT * FROM observations WHERE vessel_id = ? AND {vis} ORDER BY observed_at DESC LIMIT 20", (vid, *args))
    for r in wl + obs:
        r["agency"] = codes[r["agency_id"]]
    return {"watchlist": wl, "observations": obs}


# ------------------------------------------------------------------ alerts
@app.get("/api/alerts")
def list_alerts(user=Depends(need("read")), status: str = "", level: str = "", behaviour: str = "", q: str = ""):
    out = []
    for a in db.q("SELECT * FROM alerts ORDER BY score DESC, created_at DESC"):
        a = alert_out(a)
        if status == "open" and a["status"] == "closed" or status and status != "open" and a["status"] != status:
            continue
        if level and a["level"] != level:
            continue
        if behaviour and behaviour not in a["behaviours"]:
            continue
        if q and q.lower() not in f"{a['id']} {a['title']} {a['vessel_id']}".lower():
            continue
        out.append(a)
    return {"alerts": out, "disclaimer": DISCLAIMER}


@app.get("/api/alerts/{aid}")
def get_alert(aid: str, user=Depends(need("read"))):
    a = db.one("SELECT * FROM alerts WHERE id = ?", (aid,))
    if not a:
        raise HTTPException(404, "unknown alert")
    users = _users_by_id()
    hist = db.q("SELECT * FROM alert_history WHERE alert_id = ? ORDER BY id", (aid,))
    for h in hist:
        u = users.get(h["user_id"])
        h["who"] = f"{u['name']} ({u['agency']})" if u else "GulfEye engine"
    out = alert_out(a)
    out["history"] = hist
    out["comments"] = _comments("alert", aid)
    out["shares"] = _shares_of("alert", aid)
    return out


class AlertStatusIn(BaseModel):
    status: Literal["acknowledged", "investigating", "closed"]
    note: str = Field(default="", max_length=1000)
    outcome: Literal["confirmed", "false_alarm", "no_action"] | None = None


@app.post("/api/alerts/{aid}/status")
def alert_status(aid: str, body: AlertStatusIn, user=Depends(need("alerts:update"))):
    a = db.one("SELECT * FROM alerts WHERE id = ?", (aid,))
    if not a:
        raise HTTPException(404, "unknown alert")
    if body.status not in ALERT_FLOW[a["status"]]:
        raise HTTPException(409, f"cannot go from {a['status']} to {body.status}")
    if body.status == "closed" and (not body.outcome or not body.note.strip()):
        raise HTTPException(422, "closing needs an outcome and a note")
    now = audit.now_iso()
    with db.tx() as c:
        c.execute("UPDATE alerts SET status = ?, outcome = ?, assigned_agency = COALESCE(assigned_agency, ?), updated_at = ? WHERE id = ?",
                  (body.status, body.outcome if body.status == "closed" else None, user["agency_id"], now, aid))
        c.execute("INSERT INTO alert_history(alert_id, user_id, from_status, to_status, note, at) VALUES(?,?,?,?,?,?)",
                  (aid, user["id"], a["status"], body.status,
                   (body.note.strip() + (f" Outcome: {body.outcome.replace('_', ' ')}." if body.status == "closed" else "")).strip(), now))
    audit.log(user, "alert.status", aid, {"from": a["status"], "to": body.status, "outcome": body.outcome}, ip=user["ip"])
    broadcast({"type": "alert.updated", "id": aid, "status": body.status, "by": user["agency_code"]})
    return get_alert(aid, user)


@app.post("/api/engine/run")
def engine_run(user=Depends(need("engine:run"))):
    res = run_engine(user)
    for aid in res["created"]:
        broadcast({"type": "alert.created", "id": aid})
    if res["updated"]:
        broadcast({"type": "alert.updated", "ids": res["updated"]})
    return res


# ------------------------------------------------------------------ incidents
def _check_time(value: str) -> str:
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("use an ISO date-time, e.g. 2026-11-04T06:30:00Z")
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    if t > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5) or t.year < 2000:
        raise ValueError("time cannot be in the future or before 2000")
    return _iso(t.astimezone(dt.timezone.utc))


class VesselLink(BaseModel):
    vessel_id: str = Field(pattern=r"^\d{9}$")
    role: Literal["suspect", "victim", "witness", "involved"] = "involved"


class IncidentIn(BaseModel):
    type: str
    title: str = Field(min_length=3, max_length=140)
    description: str = Field(min_length=3, max_length=4000)
    lat: float
    lon: float
    place: str | None = Field(default=None, max_length=120)
    occurred_at: str
    visibility: Literal["shared", "agency"] = "shared"
    vessels: list[VesselLink] = Field(default_factory=list, max_length=20)

    @field_validator("type")
    @classmethod
    def _type(cls, v):
        if v not in INCIDENT_TYPES:
            raise ValueError(f"type must be one of {', '.join(INCIDENT_TYPES)}")
        return v

    @field_validator("occurred_at")
    @classmethod
    def _time(cls, v):
        return _check_time(v)

    @field_validator("lat")
    @classmethod
    def _lat(cls, v):
        if not config.AOI["lat0"] <= v <= config.AOI["lat1"]:
            raise ValueError("latitude is outside the Gulf of Guinea area")
        return v

    @field_validator("lon")
    @classmethod
    def _lon(cls, v):
        if not config.AOI["lon0"] <= v <= config.AOI["lon1"]:
            raise ValueError("longitude is outside the Gulf of Guinea area")
        return v


def _next_incident_id(year):
    row = db.one("SELECT COUNT(*) AS n FROM incidents")
    return f"INC-{year}-{row['n'] + 1:04d}"


@app.get("/api/incidents")
def list_incidents(user=Depends(need("read")), status: str = "", type: str = "", date_from: str = Query("", alias="from"),
                   date_to: str = Query("", alias="to"), q: str = "", vessel: str = "", limit: int = Query(500, le=2000)):
    vis = _visible(user)
    rows = db.q("SELECT * FROM incidents WHERE merged_into IS NULL ORDER BY occurred_at DESC")
    linked = defaultdict(set)
    for r in db.q("SELECT incident_id, vessel_id FROM incident_vessels"):
        linked[r["incident_id"]].add(r["vessel_id"])
    out = []
    for i in rows:
        if not vis(i):
            continue
        if status == "open" and i["status"] == "closed" or status and status != "open" and i["status"] != status:
            continue
        if type and i["type"] != type or date_from and i["occurred_at"] < date_from or date_to and i["occurred_at"][:10] > date_to:
            continue
        if q and q.lower() not in f"{i['id']} {i['title']} {i['place'] or ''} {i['description']}".lower():
            continue
        if vessel and vessel not in linked[i["id"]]:
            continue
        out.append(i)
    return {"incidents": [incident_out(i) for i in out[:limit]], "total": len(out), "types": INCIDENT_TYPES}


@app.get("/api/incidents/{iid}")
def get_incident(iid: str, user=Depends(need("read"))):
    inc = db.one("SELECT * FROM incidents WHERE id = ?", (iid,))
    if not inc or not security.can_see_incident(user, inc):
        raise HTTPException(404, "unknown incident")
    users = _users_by_id()
    out = incident_out(inc)
    rep = users.get(inc["reporter_id"])
    out["reporter"] = (f"{rep['name']} ({rep['agency']})" if rep else
                       f"{out['agency']} system via the agency intake" if inc["source"].startswith("agency_intake") else "Imported record")
    hist = db.q("SELECT * FROM incident_history WHERE incident_id = ? ORDER BY id", (iid,))
    for h in hist:
        u = users.get(h["user_id"])
        h["who"] = f"{u['name']} ({u['agency']})" if u else "Import"
    out["history"] = hist
    out["comments"] = _comments("incident", iid)
    out["shares"] = _shares_of("incident", iid)
    from .merge import duplicate_candidates, reports_of
    out["reports"] = reports_of(inc["merged_into"] or iid)
    out["merge"] = db.one("SELECT * FROM incident_merges WHERE status = 'merged' AND duplicate_id = ?", (iid,))
    out["merges_in"] = db.q("SELECT * FROM incident_merges WHERE status = 'merged' AND primary_id = ?", (iid,))
    out["duplicate_candidates"] = [] if inc["merged_into"] else duplicate_candidates(user, only=iid)
    # correlation: open alerts and other incidents within 25 km in the 30 days around it
    t = dt.datetime.fromisoformat(inc["occurred_at"].replace("Z", "+00:00"))
    out["related"] = [{"id": o["id"], "title": o["title"], "occurred_at": o["occurred_at"], "type": o["type"],
                       "distance_km": round(haversine_km(inc["lat"], inc["lon"], o["lat"], o["lon"]), 1)}
                      for o in db.q("SELECT * FROM incidents WHERE id != ? AND occurred_at BETWEEN ? AND ?",
                                    (iid, _iso(t - dt.timedelta(days=30)), _iso(t + dt.timedelta(days=30))))
                      if not o["merged_into"]
                      if security.can_see_incident(user, o) and haversine_km(inc["lat"], inc["lon"], o["lat"], o["lon"]) <= 25][:8]
    out["related_alerts"] = [{"id": a["id"], "title": a["title"], "level": a["level"],
                              "distance_km": round(haversine_km(inc["lat"], inc["lon"], a["lat"], a["lon"]), 1)}
                             for a in db.q("SELECT * FROM alerts") if a["lat"] is not None
                             and haversine_km(inc["lat"], inc["lon"], a["lat"], a["lon"]) <= 25]
    audit.log(user, "incident.view", iid, ip=user["ip"])
    return out


@app.post("/api/incidents", status_code=201)
def create_incident(body: IncidentIn, user=Depends(need("incidents:create"))):
    known = set(ais_data()["vessels"]) | {r["vessel_id"] for r in db.q("SELECT vessel_id FROM registry")}
    unknown = [v.vessel_id for v in body.vessels if v.vessel_id not in known]
    if unknown:
        raise HTTPException(422, f"unknown vessel(s): {', '.join(unknown)}")
    now = audit.now_iso()
    iid = _next_incident_id(body.occurred_at[:4])
    with db.tx() as c:
        c.execute("""INSERT INTO incidents(id, type, title, description, lat, lon, place, occurred_at, status, visibility, agency_id,
                     reporter_id, created_at, updated_at, source, synthetic) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                  (iid, body.type, body.title.strip(), body.description.strip(), body.lat, body.lon, (body.place or "").strip() or None,
                   body.occurred_at, "reported", body.visibility, user["agency_id"], user["id"], now, now, "user_report"))
        c.execute("INSERT INTO incident_history(incident_id, user_id, from_status, to_status, note, at) VALUES(?,?,?,?,?,?)",
                  (iid, user["id"], None, "reported", "Reported.", now))
        for v in {v.vessel_id: v for v in body.vessels}.values():
            c.execute("INSERT INTO incident_vessels(incident_id, vessel_id, role) VALUES(?,?,?)", (iid, v.vessel_id, v.role))
    audit.log(user, "incident.create", iid, {"type": body.type, "visibility": body.visibility}, ip=user["ip"])
    broadcast({"type": "incident.created", "id": iid, "by": user["agency_code"]},
              agency_only=user["agency_id"] if body.visibility == "agency" else None)
    if body.vessels:   # a named vessel changes its record indicators: re-score
        run_engine(user)
    return get_incident(iid, user)


class IncidentStatusIn(BaseModel):
    status: Literal["verified", "under_investigation", "closed"]
    note: str = Field(default="", max_length=1000)


@app.post("/api/incidents/{iid}/status")
def incident_status(iid: str, body: IncidentStatusIn, user=Depends(need("incidents:update"))):
    inc = db.one("SELECT * FROM incidents WHERE id = ?", (iid,))
    if not inc or not security.can_see_incident(user, inc):
        raise HTTPException(404, "unknown incident")
    if body.status not in INCIDENT_FLOW[inc["status"]]:
        raise HTTPException(409, f"cannot go from {inc['status']} to {body.status}")
    now = audit.now_iso()
    with db.tx() as c:
        c.execute("UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?", (body.status, now, iid))
        c.execute("INSERT INTO incident_history(incident_id, user_id, from_status, to_status, note, at) VALUES(?,?,?,?,?,?)",
                  (iid, user["id"], inc["status"], body.status, body.note.strip(), now))
    audit.log(user, "incident.status", iid, {"from": inc["status"], "to": body.status}, ip=user["ip"])
    broadcast({"type": "incident.updated", "id": iid, "status": body.status},
              agency_only=inc["agency_id"] if inc["visibility"] == "agency" else None)
    return get_incident(iid, user)


@app.post("/api/incidents/{iid}/attachments", status_code=201)
async def upload(iid: str, file: UploadFile = File(...), user=Depends(need("incidents:create"))):
    inc = db.one("SELECT * FROM incidents WHERE id = ?", (iid,))
    if not inc or not security.can_see_incident(user, inc):
        raise HTTPException(404, "unknown incident")
    if inc["agency_id"] != user["agency_id"] and user["role"] != "admin":
        raise HTTPException(403, "only the reporting agency can add evidence")
    name = re.sub(r"[^\w.\- ]", "_", (file.filename or "file"))[:120]
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in config.ALLOWED_UPLOADS:
        raise HTTPException(415, "allowed: PDF, PNG, JPEG, TXT, CSV")
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file larger than 5 MB")
    mime, check = config.ALLOWED_UPLOADS[ext]
    if not data or not check(data):
        raise HTTPException(415, "file content does not match its type")
    fid = secrets.token_hex(10)
    stored = f"{fid}{ext}"
    config.UPLOADS.mkdir(parents=True, exist_ok=True)
    (config.UPLOADS / stored).write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    db.run("INSERT INTO attachments(id, incident_id, filename, stored_name, mime, size, sha256, uploaded_by, at) VALUES(?,?,?,?,?,?,?,?,?)",
           (fid, iid, name, stored, mime, len(data), digest, user["id"], audit.now_iso()))
    audit.log(user, "incident.attach", iid, {"file": name, "size": len(data), "sha256": digest}, ip=user["ip"])
    broadcast({"type": "incident.updated", "id": iid}, agency_only=inc["agency_id"] if inc["visibility"] == "agency" else None)
    return {"id": fid, "filename": name, "size": len(data), "mime": mime, "sha256": digest}


@app.get("/api/attachments/{fid}")
def download(fid: str, user=Depends(need("read"))):
    a = db.one("SELECT * FROM attachments WHERE id = ?", (fid,))
    inc = db.one("SELECT * FROM incidents WHERE id = ?", (a["incident_id"],)) if a else None
    if not a or not inc or not security.can_see_incident(user, inc):
        raise HTTPException(404, "not found")
    audit.log(user, "incident.download", a["incident_id"], {"file": a["filename"]}, ip=user["ip"])
    return FileResponse(config.UPLOADS / a["stored_name"], media_type=a["mime"], filename=a["filename"])


# ------------------------------------------------------------------ comments and sharing
def _comments(kind, tid):
    users = _users_by_id()
    rows = db.q("SELECT * FROM comments WHERE target_type = ? AND target_id = ? ORDER BY id", (kind, tid))
    for r in rows:
        u = users.get(r["user_id"])
        r["who"] = f"{u['name']} ({u['agency']})" if u else "?"
    return rows


def _shares_of(kind, tid):
    ag = {a["id"]: a["code"] for a in db.q("SELECT id, code FROM agencies")}
    rows = db.q("SELECT * FROM shares WHERE target_type = ? AND target_id = ? ORDER BY id", (kind, tid))
    for r in rows:
        r["from"], r["to"] = ag[r["from_agency"]], ag[r["to_agency"]]
    return rows


def _check_target(user, kind, tid):
    if kind == "alert" and db.one("SELECT 1 AS x FROM alerts WHERE id = ?", (tid,)):
        return
    if kind == "vessel" and tid in ais_data()["vessels"]:
        return
    if kind == "incident":
        inc = db.one("SELECT * FROM incidents WHERE id = ?", (tid,))
        if inc and security.can_see_incident(user, inc):
            return
    raise HTTPException(404, "unknown item")


class CommentIn(BaseModel):
    target_type: Literal["alert", "incident", "vessel"]
    target_id: str = Field(max_length=40)
    body: str = Field(min_length=1, max_length=2000)


@app.post("/api/comments", status_code=201)
def add_comment(body: CommentIn, user=Depends(need("comment"))):
    _check_target(user, body.target_type, body.target_id)
    db.run("INSERT INTO comments(target_type, target_id, user_id, body, at) VALUES(?,?,?,?,?)",
           (body.target_type, body.target_id, user["id"], body.body.strip(), audit.now_iso()))
    audit.log(user, "comment.create", f"{body.target_type}:{body.target_id}", ip=user["ip"])
    broadcast({"type": "comment.created", "target": f"{body.target_type}:{body.target_id}"})
    return {"comments": _comments(body.target_type, body.target_id)}


class ShareIn(BaseModel):
    target_type: Literal["alert", "incident", "vessel"]
    target_id: str = Field(max_length=40)
    to_agencies: list[str] = Field(min_length=1, max_length=10)
    message: str = Field(default="", max_length=1000)


@app.post("/api/shares", status_code=201)
def share(body: ShareIn, user=Depends(need("share"))):
    _check_target(user, body.target_type, body.target_id)
    ags = {a["code"]: a["id"] for a in db.q("SELECT id, code FROM agencies")}
    bad = [c for c in body.to_agencies if c not in ags]
    if bad:
        raise HTTPException(422, f"unknown agency: {', '.join(bad)}")
    now = audit.now_iso()
    for code in set(body.to_agencies) - {user["agency_code"]}:
        db.run("INSERT INTO shares(target_type, target_id, from_agency, to_agency, user_id, message, at) VALUES(?,?,?,?,?,?,?)",
               (body.target_type, body.target_id, user["agency_id"], ags[code], user["id"], body.message.strip(), now))
        broadcast({"type": "share.received", "target": f"{body.target_type}:{body.target_id}", "from": user["agency_code"]},
                  agency_only=ags[code])
    audit.log(user, "share.create", f"{body.target_type}:{body.target_id}", {"to": body.to_agencies}, ip=user["ip"])
    return {"shares": _shares_of(body.target_type, body.target_id)}


@app.get("/api/shares/inbox")
def inbox(user=Depends(need("read"))):
    ag = {a["id"]: a["code"] for a in db.q("SELECT id, code FROM agencies")}
    users = _users_by_id()
    rows = db.q("SELECT * FROM shares WHERE to_agency = ? OR from_agency = ? ORDER BY id DESC LIMIT 100", (user["agency_id"], user["agency_id"]))
    for r in rows:
        r["from"], r["to"] = ag[r["from_agency"]], ag[r["to_agency"]]
        r["direction"] = "in" if r["to_agency"] == user["agency_id"] else "out"
        u = users.get(r["user_id"])
        r["who"] = u["name"] if u else "?"
    return {"shares": rows}


@app.post("/api/shares/{sid}/read")
def mark_read(sid: int, user=Depends(need("read"))):
    db.run("UPDATE shares SET read_at = ? WHERE id = ? AND to_agency = ? AND read_at IS NULL", (audit.now_iso(), sid, user["agency_id"]))
    return {"ok": True}


# ------------------------------------------------------------------ intelligence correlation
@app.get("/api/intel")
def intel(user=Depends(need("read"))):
    """Consolidated view: every vessel above Low with its links to incidents, owners, other vessels and radar contacts."""
    data = ais_data()
    risk = risk_rows()
    vis = _visible(user)
    nodes, edges, cards = {}, [], []
    for vid, r in sorted(risk.items(), key=lambda x: -x[1]["score"]):
        if r["level"] == "low":
            continue
        v = data["vessels"][vid]
        nodes[vid] = {"id": vid, "kind": "vessel", "label": v["name"], "level": r["level"]}
        links = []
        for inc in correlation.linked_incidents(vid, vis):
            nodes[inc["id"]] = {"id": inc["id"], "kind": "incident", "label": inc["title"], "level": None}
            edges.append({"a": vid, "b": inc["id"], "kind": inc["role"]})
            links.append({"kind": "incident", "id": inc["id"], "text": f"{inc['role']} in {inc['id']} · {inc['occurred_at'][:10]} · {inc['title']}"})
        for p in correlation.owner_network(vid):
            if not p["adverse"]:
                continue
            nodes.setdefault(p["vessel_id"], {"id": p["vessel_id"], "kind": "vessel", "label": p["name"],
                                              "level": risk.get(p["vessel_id"], {}).get("level")})
            owner_id = f"owner:{p['owner']}"
            nodes[owner_id] = {"id": owner_id, "kind": "owner", "label": p["owner"], "level": None}
            edges += [{"a": vid, "b": owner_id, "kind": "owned by"}, {"a": p["vessel_id"], "b": owner_id, "kind": "owned by"}]
            links.append({"kind": "vessel", "id": p["vessel_id"], "text": f"{p['link']} as {p['name']}"})
        for i in r["indicators"]:
            if i["code"] == "rendezvous":
                edges.append({"a": vid, "b": i["partner"], "kind": "rendezvous"})
                links.append({"kind": "vessel", "id": i["partner"], "text": i["evidence"][0]})
            if i["code"] == "agency_watchlist":
                links.append({"kind": "agency", "id": vid, "text": i["evidence"][0]})
            if i["code"] == "agency_report":
                links += [{"kind": "agency", "id": f"OBS-{o}", "text": t} for o, t in zip(i["observations"], i["evidence"])]
            if i["code"] == "agency_contact":
                nodes[f"OBS-{i['observation']}"] = {"id": f"OBS-{i['observation']}", "kind": "radar", "label": f"Agency radar OBS-{i['observation']}", "level": None}
                edges.append({"a": vid, "b": f"OBS-{i['observation']}", "kind": "alongside"})
                links.append({"kind": "agency", "id": f"OBS-{i['observation']}", "text": i["evidence"][0]})
            if i["code"] == "dark_alongside":
                nodes[i["detection"]] = {"id": i["detection"], "kind": "radar", "label": f"Radar contact {i['detection']}", "level": None}
                edges.append({"a": vid, "b": i["detection"], "kind": "alongside"})
                links.append({"kind": "radar", "id": i["detection"], "text": i["evidence"][0]})
        track = [(x["lat"], x["lon"]) for x in data["rows"] if x["vessel_id"] == vid]
        near = correlation.nearby_incidents(track, data["meta"]["now_effective"], visible=vis, limit=4)
        for n in near:
            links.append({"kind": "nearby", "id": n["id"], "text": f"{n['distance_km']} km from its track: {n['id']} · {n['occurred_at'][:10]} · {n['title']}"})
        cards.append({"vessel_id": vid, "name": v["name"], "flag": v["flag"], "type": v["type"], "score": r["score"], "level": r["level"],
                      "reasons": r["reasons"], "links": links})
    # hotspots: 0.5° cells with the most incidents in the last 90 days
    now = demo_now()
    recent = [i for i in db.q("SELECT * FROM incidents WHERE occurred_at >= ?", (_iso(now - dt.timedelta(days=90)),)) if vis(i)]
    cells = defaultdict(list)
    for i in recent:
        cells[(round(i["lat"] * 2) / 2, round(i["lon"] * 2) / 2)].append(i)
    hot = sorted(cells.items(), key=lambda kv: -len(kv[1]))[:6]
    hotspots = [{"lat": k[0], "lon": k[1], "count": len(v), "types": dict(Counter(i["type"] for i in v)),
                 "zone": (zone_at(_geo(), k[1], k[0]) or {}).get("name"), "ids": [i["id"] for i in v][:10]} for k, v in hot]
    codes = {a["id"]: a["code"] for a in db.q("SELECT id, code FROM agencies")}
    vis_sql = "1=1" if user["role"] == "admin" else "(visibility = 'shared' OR agency_id = ?)"
    vis_args = () if user["role"] == "admin" else (user["agency_id"],)
    unmatched = []
    since = _iso(now - dt.timedelta(days=7))
    for o in db.q(f"SELECT * FROM observations WHERE vessel_id IS NULL AND observed_at >= ? AND {vis_sql} ORDER BY observed_at DESC",
                  (since, *vis_args)):
        z = zone_at(_geo(), o["lon"], o["lat"])
        unmatched.append({"id": f"OBS-{o['id']}", "kind": o["kind"], "agency": codes[o["agency_id"]], "at": o["observed_at"],
                          "zone": z["name"] if z else None, "text": o["description"] or f"{o['length_m'] or '?'} m contact"})
    for w in db.q(f"SELECT * FROM watchlist WHERE vessel_id IS NULL AND active = 1 AND {vis_sql}", vis_args):
        unmatched.append({"id": f"WL-{w['id']}", "kind": "watchlist", "agency": codes[w["agency_id"]], "at": w["listed_at"], "zone": None,
                          "text": f"{w['name'] or w['mmsi'] or w['imo']} ({w['level']}): {w['reason']}"})
    return {"cards": cards, "graph": {"nodes": list(nodes.values()), "edges": edges}, "hotspots": hotspots,
            "agency_unmatched": unmatched, "disclaimer": DISCLAIMER}


@app.get("/api/sources")
def sources(user=Depends(need("read"))):
    return {"sources": statuses(), "engine": db.get_meta("engine_last_run")}


class PositionIn(BaseModel):
    mmsi: str = Field(pattern=r"^\d{9}$")
    t: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    sog: float = Field(default=0, ge=0, le=60)
    cog: float = Field(default=0, ge=0, lt=360)

    @field_validator("t")
    @classmethod
    def _t(cls, v):
        return _check_time(v)


class IngestIn(BaseModel):
    positions: list[PositionIn] = Field(min_length=1, max_length=5000)
    source: str = Field(default="push", max_length=40)


@app.post("/api/ingest/ais")
def ingest(body: IngestIn, user=Depends(need("ingest"))):
    rows = [p for p in body.positions if config.AOI["lat0"] <= p.lat <= config.AOI["lat1"] and config.AOI["lon0"] <= p.lon <= config.AOI["lon1"]]
    now = audit.now_iso()
    with db.tx() as c:
        for p in rows:
            c.execute("INSERT INTO positions_ingested(vessel_id, t, lat, lon, sog, cog, source, received_at) VALUES(?,?,?,?,?,?,?,?)",
                      (p.mmsi, p.t, p.lat, p.lon, p.sog, p.cog, body.source, now))
    audit.log(user, "ingest.ais", body.source, {"accepted": len(rows), "rejected": len(body.positions) - len(rows)}, ip=user["ip"])
    res = run_engine(user)
    for aid in res["created"]:
        broadcast({"type": "alert.created", "id": aid})
    broadcast({"type": "data.updated", "source": "ais"})
    return {"accepted": len(rows), "rejected": len(body.positions) - len(rows), "engine": res}


# ------------------------------------------------------------------ analytics and exports
def _range(date_from, date_to):
    now = demo_now()
    start = date_from or _iso(now - dt.timedelta(days=365))[:10]
    end = date_to or _iso(now)[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) or start > end:
        raise HTTPException(422, "dates must be YYYY-MM-DD with from ≤ to")
    return start, end


def _incidents_in(user, start, end):
    vis = _visible(user)
    return [i for i in db.q("SELECT * FROM incidents WHERE merged_into IS NULL AND substr(occurred_at,1,10) BETWEEN ? AND ? ORDER BY occurred_at",
                            (start, end)) if vis(i)]


@app.get("/api/analytics")
def analytics(user=Depends(need("read")), date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to")):
    start, end = _range(date_from, date_to)
    incs = _incidents_in(user, start, end)
    months = []
    y, m = int(start[:4]), int(start[5:7])
    while f"{y:04d}-{m:02d}" <= end[:7]:
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    by_month = Counter(i["occurred_at"][:7] for i in incs)
    by_zone = Counter((zone_at(_geo(), i["lon"], i["lat"]) or {}).get("name", "Outside watch zones") for i in incs)
    alerts = [a for a in db.q("SELECT * FROM alerts") if start <= a["created_at"][:10] <= end or a["status"] != "closed"]
    risk = risk_rows()
    data = ais_data()
    types = defaultdict(lambda: {"count": 0, "sog": [], "flagged": 0})
    for vid, v in data["vessels"].items():
        t = types[v["type"]]
        t["count"] += 1
        f = risk.get(vid, {}).get("features", {})
        if f:
            t["sog"].append(f["sog_mean"])
        if risk.get(vid, {}).get("level", "low") != "low":
            t["flagged"] += 1
    behaviours = Counter(b for a in alerts for b in json.loads(a["behaviours"]))
    return {
        "from": start, "to": end, "synthetic": True,
        "incidents_total": len(incs),
        "incidents_by_month": [{"month": mo, "count": by_month.get(mo, 0)} for mo in months],
        "incidents_by_type": [{"type": k, "label": INCIDENT_TYPES.get(k, k), "count": v} for k, v in Counter(i["type"] for i in incs).most_common()],
        "incidents_by_status": dict(Counter(i["status"] for i in incs)),
        "incidents_by_zone": [{"zone": k, "count": v} for k, v in by_zone.most_common()],
        "incidents_by_hour": [sum(1 for i in incs if int(i["occurred_at"][11:13]) // 3 == h) for h in range(8)],
        "alerts_by_level": dict(Counter(a["level"] for a in alerts)),
        "alerts_by_status": dict(Counter(a["status"] for a in alerts)),
        "alerts_by_behaviour": [{"code": k, "count": v} for k, v in behaviours.most_common()],
        "risk_distribution": dict(Counter(r["level"] for r in risk.values())),
        "vessel_types": [{"type": k, "count": v["count"], "mean_sog": round(sum(v["sog"]) / len(v["sog"]), 1) if v["sog"] else None,
                          "flagged": v["flagged"]} for k, v in sorted(types.items(), key=lambda kv: -kv[1]["count"])],
        "points": [{"id": i["id"], "lat": i["lat"], "lon": i["lon"], "type": i["type"]} for i in incs],
    }


def _csv(rows, cols, name):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# GulfEye export. Contains SIMULATED demo data. Indicators support investigation; they are not proof."])
    w.writerow(cols)
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in cols])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _cell(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s    # stop spreadsheet formula injection


@app.get("/api/export/{kind}")
def export(kind: str, user=Depends(need("export")), date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to")):
    start, end = _range(date_from, date_to)
    audit.log(user, "export", kind, {"from": start, "to": end}, ip=user["ip"])
    if kind == "incidents.csv":
        rows = []
        for i in _incidents_in(user, start, end):
            o = incident_out(i)
            o["vessels"] = "; ".join(f"{v['name'] or v['vessel_id']} ({v['role']})" for v in o["vessels"])
            o["simulated"] = "yes" if i["synthetic"] else "no"
            o["reports"] = 1 + len(o["merged_reports"])
            o["agencies"] = "; ".join(o["agencies"])
            o["merged_reports"] = "; ".join(o["merged_reports"])
            rows.append(o)
        return _csv(rows, ["id", "occurred_at", "type_label", "title", "status", "lat", "lon", "place", "zone", "agency", "agencies", "reports",
                           "merged_reports", "visibility", "vessels", "simulated", "description"], f"gulfeye_incidents_{start}_{end}.csv")
    if kind == "alerts.csv":
        rows = []
        for a in db.q("SELECT * FROM alerts ORDER BY created_at"):
            o = alert_out(a)
            o["vessel_name"] = (o["vessel"] or {}).get("name")
            o["behaviours"] = "; ".join(o["behaviours"])
            o["simulated"] = "yes" if a["synthetic"] else "no"
            rows.append(o)
        return _csv(rows, ["id", "created_at", "observed_at", "vessel_id", "vessel_name", "level", "score", "status", "outcome", "lat", "lon",
                           "zone", "behaviours", "title", "simulated"], "gulfeye_alerts.csv")
    if kind == "vessels.csv":
        rows = []
        for v in vessels(user)["vessels"]:
            v["indicators"] = "; ".join(v["indicators"])
            rows.append(v)
        return _csv(rows, ["id", "imo", "name", "flag", "type", "length_m", "destination", "score", "level", "indicators", "open_alerts",
                           "incidents"], "gulfeye_vessel_risk.csv")
    if kind == "report.json":
        body = {"title": "GulfEye maritime security report", "generated_at": audit.now_iso(), "by": user["email"],
                "notice": "SIMULATED demo data. " + DISCLAIMER, "summary": summary(user), "analytics": analytics(user, start, end)}
        body["analytics"].pop("points", None)
        return Response(json.dumps(body, indent=1), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="gulfeye_report_{start}_{end}.json"'})
    raise HTTPException(404, "unknown export")


# ------------------------------------------------------------------ agencies, users, roles, audit
@app.get("/api/agencies")
def agencies(user=Depends(need("read"))):
    rows = db.q("SELECT a.*, (SELECT COUNT(*) FROM users u WHERE u.agency_id = a.id AND u.active = 1) AS users FROM agencies a ORDER BY id")
    return {"agencies": rows}


class AgencyIn(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,11}$")
    name: str = Field(min_length=3, max_length=120)
    kind: str = Field(min_length=2, max_length=40)


@app.post("/api/agencies", status_code=201)
def create_agency(body: AgencyIn, user=Depends(need("users:manage"))):
    """Onboard a new partner agency. Its users and API keys are then created as for any other agency."""
    if db.one("SELECT 1 AS x FROM agencies WHERE code = ?", (body.code,)):
        raise HTTPException(409, "an agency with this code exists")
    db.run("INSERT INTO agencies(code, name, kind) VALUES(?,?,?)", (body.code, body.name.strip(), body.kind.strip()))
    audit.log(user, "agency.create", body.code, {"name": body.name, "kind": body.kind}, ip=user["ip"])
    return {"ok": True}


@app.get("/api/roles")
def roles(user=Depends(need("read"))):
    return {"roles": [{"role": k, "label": v["label"], "permissions": sorted(v["perms"])} for k, v in security.ROLES.items()],
            "permissions": security.PERMISSION_LABELS}


@app.get("/api/users")
def users(user=Depends(need("read"))):
    sql = """SELECT u.id, u.email, u.name, u.role, u.active, u.created_at, a.code AS agency FROM users u JOIN agencies a ON a.id = u.agency_id"""
    if user["role"] == "admin":
        return {"users": db.q(sql + " ORDER BY u.id")}
    return {"users": db.q(sql + " WHERE u.agency_id = ? ORDER BY u.id", (user["agency_id"],))}


class UserIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", max_length=200)
    name: str = Field(min_length=2, max_length=100)
    role: Literal["admin", "analyst", "operator", "viewer"]
    agency: str
    password: str = Field(min_length=10, max_length=200)


@app.post("/api/users", status_code=201)
def create_user(body: UserIn, user=Depends(need("users:manage"))):
    ag = db.one("SELECT id FROM agencies WHERE code = ?", (body.agency,))
    if not ag:
        raise HTTPException(422, "unknown agency")
    if db.one("SELECT 1 AS x FROM users WHERE lower(email) = ?", (body.email.lower(),)):
        raise HTTPException(409, "email already registered")
    db.run("INSERT INTO users(email, name, role, agency_id, pw_hash, created_at) VALUES(?,?,?,?,?,?)",
           (body.email.lower(), body.name, body.role, ag["id"], security.hash_password(body.password), audit.now_iso()))
    audit.log(user, "user.create", body.email.lower(), {"role": body.role, "agency": body.agency}, ip=user["ip"])
    return {"ok": True}


class UserPatch(BaseModel):
    role: Literal["admin", "analyst", "operator", "viewer"] | None = None
    active: bool | None = None


@app.patch("/api/users/{uid}")
def patch_user(uid: int, body: UserPatch, user=Depends(need("users:manage"))):
    target = db.one("SELECT * FROM users WHERE id = ?", (uid,))
    if not target:
        raise HTTPException(404, "unknown user")
    if uid == user["id"] and (body.active is False or (body.role and body.role != "admin")):
        raise HTTPException(409, "you cannot demote or disable your own account")
    if body.role:
        db.run("UPDATE users SET role = ? WHERE id = ?", (body.role, uid))
    if body.active is not None:
        db.run("UPDATE users SET active = ? WHERE id = ?", (int(body.active), uid))
    audit.log(user, "user.update", target["email"], body.model_dump(exclude_none=True), ip=user["ip"])
    return {"ok": True}


@app.get("/api/audit")
def audit_log(user=Depends(need("read")), action: str = "", limit: int = Query(200, le=1000)):
    sql, args = "SELECT * FROM audit_log", []
    where = []
    if "audit:read" not in security.perms(user["role"]):
        where.append("user_id = ?")
        args.append(user["id"])
    if action:
        where.append("action LIKE ?")
        args.append(action.replace("%", "") + "%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return {"entries": db.q(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)), "full": "audit:read" in security.perms(user["role"])}


# ------------------------------------------------------------------ agency data intake (backend/intake.py)
from .intake import router as intake_router  # noqa: E402  (imports helpers defined above)

app.include_router(intake_router)


# ------------------------------------------------------------------ static front end (last, so /api routes win)
app.mount("/", StaticFiles(directory=config.WEB, html=True), name="web")
