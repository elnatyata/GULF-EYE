"""Intelligence correlation: link vessel identities, tracks, registry records, incidents, alerts and radar contacts."""
import datetime as dt

from .. import db
from .geo import haversine_km

NEARBY_KM = 15


def _parse(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def registry(vid):
    r = db.one("SELECT * FROM registry WHERE vessel_id = ?", (vid,))
    return db.loads(r, "flag_history", "last_psc", "former_names")


def linked_incidents(vid, visible=None):
    """Incidents naming this vessel. Reports merged into one event are returned once, as the primary incident."""
    rows = db.q("""SELECT COALESCE(p.id, i.id) AS event_id, iv.role FROM incident_vessels iv JOIN incidents i ON i.id = iv.incident_id
                   LEFT JOIN incidents p ON p.id = i.merged_into WHERE iv.vessel_id = ?""", (vid,))
    rank = {"suspect": 0, "victim": 1, "witness": 2, "involved": 3}
    roles = {}
    for r in rows:
        if r["event_id"] not in roles or rank[r["role"]] < rank[roles[r["event_id"]]]:
            roles[r["event_id"]] = r["role"]
    out = []
    for eid, role in roles.items():
        inc = db.one("SELECT * FROM incidents WHERE id = ?", (eid,))
        if inc and (visible is None or visible(inc)):
            out.append({**inc, "role": role})
    return sorted(out, key=lambda x: x["occurred_at"], reverse=True)


def owner_network(vid):
    """Other vessels with the same registered owner or manager, and whether they carry adverse records."""
    me = registry(vid)
    if not me or not me.get("owner"):
        return []
    peers = db.q("SELECT vessel_id, name, owner, manager, watchlist FROM registry WHERE vessel_id != ? AND (owner = ? OR manager = ?)",
                 (vid, me["owner"], me["owner"]))
    out = []
    for p in peers:
        suspect = [i for i in linked_incidents(p["vessel_id"]) if i["role"] == "suspect"]
        out.append({**p, "link": "same owner" if p["owner"] == me["owner"] else "same manager",
                    "adverse": bool(p["watchlist"] or suspect), "suspect_incidents": suspect})
    return out


def identity_checks(vid, ais_info):
    """Compare what the vessel broadcasts on AIS with its registration record."""
    r = registry(vid)
    if not r:
        return {"registry": None, "issues": ["No registration record found for this MMSI."]}
    issues = []
    if ais_info.get("flag") and r["flag"] and ais_info["flag"] != r["flag"]:
        issues.append(f"AIS flag {ais_info['flag']} differs from registered flag {r['flag']}.")
    names = {r["name"].lower(), *[n.lower() for n in (r.get("former_names") or [])]}
    if ais_info.get("name") and ais_info["name"].lower() not in names:
        issues.append(f"AIS name '{ais_info['name']}' differs from registered name '{r['name']}'.")
    if ais_info.get("length_m") and r.get("length_m") and abs(ais_info["length_m"] - r["length_m"]) / r["length_m"] > 0.25:
        issues.append(f"AIS length {ais_info['length_m']} m differs from registered {r['length_m']} m.")
    recent = [h for h in (r.get("flag_history") or []) if h["from"] >= 2021]
    return {"registry": r, "issues": issues, "flag_changes_5y": max(0, len(recent) - 1) if len(recent) > 1 else 0}


def nearby_incidents(track_pts, now, days=365, km=NEARBY_KM, visible=None, limit=8):
    """Incidents within km of any point of the track in the last `days` (spatial cross-reference)."""
    since = (now - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    incs = db.q("SELECT * FROM incidents WHERE merged_into IS NULL AND occurred_at >= ? ORDER BY occurred_at DESC", (since,))
    pts = track_pts[:: max(1, len(track_pts) // 12)] or track_pts
    out = []
    for inc in incs:
        if visible and not visible(inc):
            continue
        d = min((haversine_km(inc["lat"], inc["lon"], p[0], p[1]) for p in pts), default=1e9)
        if d <= km:
            out.append({**inc, "distance_km": round(d, 1)})
    return out[:limit]
