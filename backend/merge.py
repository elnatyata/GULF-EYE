"""Merging data from different agencies and sensors into one picture.

1. Fused contacts (automatic, non-destructive). Agency sightings, patrol reports and radar contacts, Sentinel-1 dark
   contacts and recent incidents that are close in place and time are grouped into one fused contact. It lists every
   source, counts independent sources and combines their confidence. The original records are never changed.
2. Duplicate incidents (suggested, merged only when a person confirms). The same event reported by two agencies
   becomes one primary incident with both reports attached. Merges are audited and can be undone; "not a duplicate"
   decisions are remembered so the pair is not suggested again.
3. Consolidated vessel record. What AIS, the registry and each agency say about a vessel, side by side, with
   disagreements flagged instead of silently overwritten.

Only shared records are fused, so restricted agency data never surfaces in another agency's view.
"""
import datetime as dt
import hashlib
import os
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, db, security
from .adapters import SAR
from .ml.geo import haversine_km, zone_at

router = APIRouter(prefix="/api")

FUSE_KM, FUSE_MIN, FUSE_DAYS = 5.0, 90, 7
DUP_KM, DUP_HOURS = 10.0, 6
DEFAULT_CONF = {"radar_contact": 0.6, "sighting": 0.7, "patrol_report": 0.8, "incident": 0.7, "sar": 0.6}
FAMILY = {"piracy_attack": "attack", "boarding": "attack", "armed_robbery": "attack", "kidnapping": "attack"}
STATUS_RANK = {"reported": 0, "verified": 1, "under_investigation": 2, "closed": 3}


def _t(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _codes():
    return {a["id"]: a["code"] for a in db.q("SELECT id, code FROM agencies")}


# ------------------------------------------------------------------ 1. fused contacts
_cache = {"key": None, "value": None}


def _items():
    """Everything that can be fused: shared agency observations, Sentinel-1 dark contacts, recent shared incidents."""
    from .app import ais_data, demo_now, _geo
    from .ml import fusion
    from .ml.engine import load_state
    now = ais_data()["meta"]["now_effective"]
    since = _iso(now - dt.timedelta(days=FUSE_DAYS))
    codes = _codes()
    names = {vid: v["name"] for vid, v in ais_data()["vessels"].items()}
    items = []
    for o in db.q("SELECT * FROM observations WHERE visibility = 'shared' AND observed_at >= ?", (since,)):
        items.append({"id": f"OBS-{o['id']}", "type": o["kind"], "source": codes[o["agency_id"]], "t": _t(o["observed_at"]),
                      "lat": o["lat"], "lon": o["lon"], "conf": o["confidence"] or DEFAULT_CONF[o["kind"]],
                      "vessel_id": o["vessel_id"], "vessel_name": names.get(o["vessel_id"]),
                      "text": o["description"] or (f"{o['length_m']:.0f} m contact" if o["length_m"] else "")})
    state = load_state()
    fu = fusion.run(state["ais"], state["sar"], state["geo"], state["assumptions"]["fusion"])
    for d in fu["dark"]:
        det = d["det"]
        items.append({"id": det["id"], "type": "sar", "source": "Sentinel-1", "t": fu["time"], "lat": det["lat"], "lon": det["lon"],
                      "conf": det["confidence"], "vessel_id": None, "vessel_name": None,
                      "text": f"{det['length_m']} m radar contact without AIS ({det['confidence'] * 100:.0f}% detector confidence)"})
    for i in db.q("SELECT * FROM incidents WHERE visibility = 'shared' AND merged_into IS NULL AND occurred_at >= ?", (since,)):
        items.append({"id": i["id"], "type": "incident", "source": codes[i["agency_id"]], "t": _t(i["occurred_at"]),
                      "lat": i["lat"], "lon": i["lon"], "conf": DEFAULT_CONF["incident"], "vessel_id": None, "vessel_name": None,
                      "text": i["title"]})
    return items, demo_now


def fused_contacts():
    key = (db.one("SELECT COUNT(*) AS n, MAX(id) AS m FROM observations")["m"], db.one("SELECT COUNT(*) AS n FROM incidents")["n"],
           db.one("SELECT COUNT(*) AS n FROM incident_merges")["n"], db.one("SELECT COUNT(*) AS n FROM observations")["n"],
           os.path.getmtime(SAR.path) if os.path.exists(SAR.path) else 0)
    if _cache["key"] == key:
        return _cache["value"]
    from .app import _geo
    items, _ = _items()
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if abs((a["t"] - b["t"]).total_seconds()) <= FUSE_MIN * 60 and haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) <= FUSE_KM:
                parent[find(i)] = find(j)
    groups = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    out = []
    for members in groups.values():
        # merged contacts are about craft without an identity; reports on a known vessel are already merged in its profile
        if len(members) < 2 or all(m["vessel_id"] for m in members):
            continue
        members.sort(key=lambda m: m["t"])
        best = {}
        for m in members:                              # one vote per independent source: its most confident record
            best[m["source"]] = max(best.get(m["source"], 0), m["conf"])
        miss = 1.0
        for c in best.values():
            miss *= 1 - min(c, 0.95)
        w = sum(m["conf"] for m in members)
        lat = sum(m["lat"] * m["conf"] for m in members) / w
        lon = sum(m["lon"] * m["conf"] for m in members) / w
        spread = max(haversine_km(lat, lon, m["lat"], m["lon"]) for m in members)
        z = zone_at(_geo(), lon, lat)
        vessels = sorted({(m["vessel_id"], m["vessel_name"]) for m in members if m["vessel_id"]})
        fid = "FC-" + hashlib.sha1("|".join(sorted(m["id"] for m in members)).encode()).hexdigest()[:6]
        out.append({
            "id": fid, "lat": round(lat, 4), "lon": round(lon, 4), "spread_km": round(spread, 1), "zone": z["name"] if z else None,
            "first": _iso(members[0]["t"]), "last": _iso(members[-1]["t"]), "sources": sorted(best), "independent_sources": len(best),
            "confidence": round(min(0.99, 1 - miss), 3), "single_best": round(max(best.values()), 3),
            "vessels": [{"vessel_id": v, "name": n} for v, n in vessels],
            "members": [{**{k: m[k] for k in ("id", "type", "source", "lat", "lon", "conf", "vessel_id", "vessel_name", "text")},
                         "t": _iso(m["t"])} for m in members],
        })
    out.sort(key=lambda f: (-f["independent_sources"], -f["confidence"]))
    _cache.update(key=key, value=out)
    return out


# ------------------------------------------------------------------ 2. duplicate incidents
def _family(t):
    return FAMILY.get(t, t)


def duplicate_candidates(user, only=None):
    vis = lambda i: security.can_see_incident(user, i)  # noqa: E731
    incs = [i for i in db.q("SELECT * FROM incidents WHERE merged_into IS NULL ORDER BY occurred_at") if vis(i)]
    rejected = {tuple(sorted((r["primary_id"], r["duplicate_id"]))) for r in db.q("SELECT * FROM incident_merges WHERE status = 'rejected'")}
    vessels = {}
    for r in db.q("SELECT incident_id, vessel_id FROM incident_vessels"):
        vessels.setdefault(r["incident_id"], set()).add(r["vessel_id"])
    codes = _codes()
    out = []
    for n, a in enumerate(incs):
        ta = _t(a["occurred_at"])
        for b in incs[n + 1:]:
            tb = _t(b["occurred_at"])
            dh = (tb - ta).total_seconds() / 3600
            if dh > DUP_HOURS:
                break
            if only and only not in (a["id"], b["id"]):
                continue
            if _family(a["type"]) != _family(b["type"]):
                continue
            d = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
            if d > DUP_KM or tuple(sorted((a["id"], b["id"]))) in rejected:
                continue
            shared = vessels.get(a["id"], set()) & vessels.get(b["id"], set())
            score = 0.4 * (1 - d / DUP_KM) + 0.3 * (1 - dh / DUP_HOURS) + (0.2 if a["type"] == b["type"] else 0.05) + (0.1 if shared else 0)
            reasons = [f"{d:.1f} km apart", f"{dh * 60:.0f} min apart",
                       "same type" if a["type"] == b["type"] else f"related types ({a['type'].replace('_', ' ')} / {b['type'].replace('_', ' ')})"]
            if shared:
                reasons.append(f"same vessel named ({', '.join(sorted(shared))})")
            reasons.append("reported by different agencies" if a["agency_id"] != b["agency_id"] else "reported by the same agency")
            primary, dup = (a, b) if (a["created_at"], a["id"]) <= (b["created_at"], b["id"]) else (b, a)
            out.append({"primary": _brief(primary, codes), "duplicate": _brief(dup, codes), "score": round(score, 2), "reasons": reasons})
    out.sort(key=lambda x: -x["score"])
    return out


def _brief(i, codes):
    return {"id": i["id"], "title": i["title"], "type": i["type"], "occurred_at": i["occurred_at"], "agency": codes[i["agency_id"]],
            "lat": i["lat"], "lon": i["lon"], "status": i["status"], "place": i["place"]}


def reports_of(primary_id):
    """The primary incident plus every incident merged into it, as separate reports."""
    codes = _codes()
    rows = db.q("SELECT * FROM incidents WHERE id = ? OR merged_into = ? ORDER BY created_at", (primary_id, primary_id))
    return [{**_brief(r, codes), "description": r["description"], "is_primary": r["id"] == primary_id} for r in rows]


def resolve(iid):
    """Follow a merge to the incident that now represents the event."""
    row = db.one("SELECT merged_into FROM incidents WHERE id = ?", (iid,))
    return row["merged_into"] if row and row["merged_into"] else iid


# ------------------------------------------------------------------ 3. consolidated vessel record
_PREFIX = re.compile(r"^(mv|mt|fv|osv|tug|m/v|m/t)\s+", re.I)


def _norm(field, v):
    if v is None or v == "":
        return None
    if field == "name":
        return _PREFIX.sub("", str(v).strip()).lower()
    return str(v).strip().lower()


def consolidated(vid, user):
    from .app import ais_data
    v = ais_data()["vessels"].get(vid) or {}
    reg = db.one("SELECT * FROM registry WHERE vessel_id = ?", (vid,)) or {}
    codes = _codes()
    vis = "1=1" if user["role"] == "admin" else "(visibility = 'shared' OR agency_id = ?)"
    args = () if user["role"] == "admin" else (user["agency_id"],)
    claims = {f: [] for f in ("name", "mmsi", "imo", "flag", "type", "length_m", "call_sign", "destination")}

    def add(field, value, source):
        if value not in (None, ""):
            claims[field].append((value, source))

    for f in ("name", "flag", "type", "length_m", "destination"):
        add(f, v.get(f), "AIS")
    add("mmsi", vid, "AIS")
    for f in ("name", "imo", "flag", "length_m", "call_sign"):
        add(f, reg.get(f), "Registry (simulated)")
    for o in db.q(f"SELECT * FROM observations WHERE vessel_id = ? AND {vis}", (vid, *args)):
        src = f"{codes[o['agency_id']]} {o['kind'].replace('_', ' ')} OBS-{o['id']}"
        add("name", o["reported_name"], src)
        add("mmsi", o["reported_mmsi"], src)
        add("length_m", o["length_m"], src)
    for w in db.q(f"SELECT * FROM watchlist WHERE vessel_id = ? AND {vis}", (vid, *args)):
        src = f"{codes[w['agency_id']]} watchlist WL-{w['id']}"
        add("name", w["name"], src)
        add("mmsi", w["mmsi"], src)
        add("imo", w["imo"], src)
    rows = []
    for field, vals in claims.items():
        if not vals:
            continue
        groups = []
        for value, src in vals:
            if field == "length_m":
                g = next((g for g in groups if abs(float(value) - float(g["value"])) / float(g["value"]) <= 0.25), None)
            else:
                g = next((g for g in groups if _norm(field, g["value"]) == _norm(field, value)), None)
            if g:
                g["sources"].append(src)
            else:
                groups.append({"value": round(float(value)) if field == "length_m" else value, "sources": [src]})
        rows.append({"field": field, "values": groups, "conflict": len(groups) > 1})
    return {"fields": rows, "conflicts": sum(r["conflict"] for r in rows)}


# ------------------------------------------------------------------ routes
def _user(request: Request, perm="read"):
    from .app import current_user
    user = current_user(request)
    if perm not in security.perms(user["role"]):
        audit.log(user, "denied", perm, ip=user["ip"], ok=False)
        raise HTTPException(403, f"your role ({user['role']}) cannot do this ({perm})")
    return user


@router.get("/fused")
def fused(request: Request):
    _user(request)
    items = fused_contacts()
    return {"fused": items, "rules": {"radius_km": FUSE_KM, "window_min": FUSE_MIN, "days": FUSE_DAYS},
            "note": "Fused contacts group reports that are probably the same thing. Originals are kept; this is an analyst aid."}


@router.get("/fused/{fid}")
def fused_one(fid: str, request: Request):
    user = _user(request)
    f = next((x for x in fused_contacts() if x["id"] == fid), None)
    if not f:
        raise HTTPException(404, "this fused contact no longer exists (new data may have regrouped the reports)")
    audit.log(user, "fused.view", fid, ip=user["ip"])
    return f


@router.get("/incidents/duplicates")
def duplicates(request: Request, incident: str = ""):
    user = _user(request)
    return {"candidates": duplicate_candidates(user, only=incident or None), "rules": {"radius_km": DUP_KM, "window_h": DUP_HOURS}}


class MergeIn(BaseModel):
    primary: str = Field(max_length=40)
    duplicate: str = Field(max_length=40)
    note: str = Field(default="", max_length=1000)


def _visible_incident(user, iid):
    inc = db.one("SELECT * FROM incidents WHERE id = ?", (iid,))
    if not inc or not security.can_see_incident(user, inc):
        raise HTTPException(404, f"unknown incident {iid}")
    return inc


@router.post("/incidents/merge", status_code=201)
def merge(body: MergeIn, request: Request):
    from .app import broadcast
    user = _user(request, "incidents:update")
    if body.primary == body.duplicate:
        raise HTTPException(422, "choose two different incidents")
    p = _visible_incident(user, body.primary)
    d = _visible_incident(user, body.duplicate)
    if p["merged_into"] or d["merged_into"]:
        raise HTTPException(409, "one of these incidents is already merged into another; undo that first or use its primary")
    if db.one("SELECT 1 AS x FROM incidents WHERE merged_into = ?", (d["id"],)):
        raise HTTPException(409, f"{d['id']} already has reports merged into it; keep it as the primary instead")
    now = audit.now_iso()
    note = body.note.strip()
    with db.tx() as c:
        c.execute("UPDATE incidents SET merged_into = ?, updated_at = ? WHERE id = ?", (p["id"], now, d["id"]))
        # the event keeps the most advanced status of its reports
        if STATUS_RANK[d["status"]] > STATUS_RANK[p["status"]] and d["status"] != "closed":
            c.execute("UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?", (d["status"], now, p["id"]))
        cur = c.execute("INSERT INTO incident_merges(primary_id, duplicate_id, status, note, decided_by, decided_at) VALUES(?,?,?,?,?,?)",
                        (p["id"], d["id"], "merged", note, user["id"], now))
        for iid, other, verb in ((p["id"], d["id"], "absorbed"), (d["id"], p["id"], "merged into")):
            st = c.execute("SELECT status FROM incidents WHERE id = ?", (iid,)).fetchone()[0]
            c.execute("INSERT INTO incident_history(incident_id, user_id, from_status, to_status, note, at) VALUES(?,?,?,?,?,?)",
                      (iid, user["id"], None, st, f"Report {verb} {other}." + (f" {note}" if note else ""), now))
    audit.log(user, "incident.merge", p["id"], {"duplicate": d["id"], "merge_id": cur.lastrowid}, ip=user["ip"])
    broadcast({"type": "incident.updated", "id": p["id"]})
    from .ml.engine import run_engine
    run_engine(user)      # prior-incident indicators now count the event once
    return {"merge_id": cur.lastrowid, "primary": p["id"], "duplicate": d["id"], "reports": reports_of(p["id"])}


@router.post("/incidents/merge/{mid}/undo")
def undo(mid: int, request: Request):
    from .app import broadcast
    user = _user(request, "incidents:update")
    m = db.one("SELECT * FROM incident_merges WHERE id = ? AND status = 'merged'", (mid,))
    if not m:
        raise HTTPException(404, "no active merge with this id")
    _visible_incident(user, m["primary_id"])
    now = audit.now_iso()
    with db.tx() as c:
        c.execute("UPDATE incidents SET merged_into = NULL, updated_at = ? WHERE id = ?", (now, m["duplicate_id"]))
        c.execute("UPDATE incident_merges SET status = 'undone', undone_by = ?, undone_at = ? WHERE id = ?", (user["id"], now, mid))
        for iid in (m["primary_id"], m["duplicate_id"]):
            st = c.execute("SELECT status FROM incidents WHERE id = ?", (iid,)).fetchone()[0]
            c.execute("INSERT INTO incident_history(incident_id, user_id, from_status, to_status, note, at) VALUES(?,?,?,?,?,?)",
                      (iid, user["id"], None, st, f"Merge {m['duplicate_id']} → {m['primary_id']} undone.", now))
    audit.log(user, "incident.unmerge", m["primary_id"], {"duplicate": m["duplicate_id"], "merge_id": mid}, ip=user["ip"])
    broadcast({"type": "incident.updated", "id": m["primary_id"]})
    from .ml.engine import run_engine
    run_engine(user)
    return {"ok": True}


class NotDuplicateIn(BaseModel):
    a: str = Field(max_length=40)
    b: str = Field(max_length=40)
    note: str = Field(default="", max_length=1000)


@router.post("/incidents/not-duplicate", status_code=201)
def not_duplicate(body: NotDuplicateIn, request: Request):
    user = _user(request, "incidents:update")
    _visible_incident(user, body.a)
    _visible_incident(user, body.b)
    db.run("INSERT INTO incident_merges(primary_id, duplicate_id, status, note, decided_by, decided_at) VALUES(?,?,?,?,?,?)",
           (body.a, body.b, "rejected", body.note.strip(), user["id"], audit.now_iso()))
    audit.log(user, "incident.not_duplicate", body.a, {"other": body.b}, ip=user["ip"])
    return {"ok": True}


@router.get("/incidents/merges")
def merges(request: Request):
    user = _user(request)
    users = {u["id"]: u["name"] for u in db.q("SELECT id, name FROM users")}
    rows = []
    for m in db.q("SELECT * FROM incident_merges ORDER BY id DESC LIMIT 100"):
        p = db.one("SELECT * FROM incidents WHERE id = ?", (m["primary_id"],))
        d = db.one("SELECT * FROM incidents WHERE id = ?", (m["duplicate_id"],))
        if not (security.can_see_incident(user, p) and security.can_see_incident(user, d)):
            continue
        rows.append({**m, "by": users.get(m["decided_by"]), "undone_by_name": users.get(m["undone_by"]),
                     "primary_title": p["title"], "duplicate_title": d["title"]})
    return {"merges": rows}
