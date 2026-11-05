"""Server-side copy of the AIS/radar fusion in web/js/model.js (same rules, same thresholds from assumptions.json).

The browser keeps its own copy so the rules stay visible to operators; this one lets the engine use radar evidence
(dark contacts alongside a vessel, a silent vessel still seen at sea). tests/test_engine.py checks both agree.
"""
import datetime as dt

from .geo import KM_PER_NM, haversine_km, in_poly, move, zone_at


def _parse(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def position_at(rows, t, step_min, max_extrap_min):
    """rows: sorted list of dicts with t, lat, lon, sog, cog. Interpolate between consecutive reports, else dead-reckon."""
    prev = nxt = None
    for r in rows:
        if r["t"] <= t:
            prev = r
        if r["t"] >= t:
            nxt = r
            break
    if prev and nxt and (nxt["t"] - prev["t"]).total_seconds() <= step_min * 60 + 1:
        span = (nxt["t"] - prev["t"]).total_seconds()
        w = 0 if span == 0 else (t - prev["t"]).total_seconds() / span
        return {"lat": prev["lat"] + (nxt["lat"] - prev["lat"]) * w, "lon": prev["lon"] + (nxt["lon"] - prev["lon"]) * w,
                "sog": prev["sog"], "cog": prev["cog"]}
    if not prev:
        return None
    age = (t - prev["t"]).total_seconds() / 60
    if age > max_extrap_min:
        return None
    lat, lon = move(prev["lat"], prev["lon"], prev["cog"], prev["sog"] * KM_PER_NM * age / 60)
    return {"lat": lat, "lon": lon, "sog": prev["sog"], "cog": prev["cog"]}


def run(ais: dict, sar: dict, geo: dict, f: dict) -> dict:
    T = _parse(sar["scene"]["time"])
    step = ais["meta"]["step_min"]
    tracks = {}
    for r in ais["rows"]:
        tracks.setdefault(r["vessel_id"], []).append(r)
    for rows in tracks.values():
        rows.sort(key=lambda r: r["t"])
    vinfo = ais["vessels"]
    dets = [dict(d) for d in sar["detections"]]
    footprint = sar["scene"]["footprint"]

    live, silent = [], []
    for vid, rows in tracks.items():
        p = position_at(rows, T, step, f["max_extrapolate_min"])
        if p:
            live.append((vid, p))
        else:
            last = max((r for r in rows if r["t"] <= T), key=lambda r: r["t"], default=None)
            if last:
                silent.append((vid, last, (T - last["t"]).total_seconds() / 60))

    def length_ok(det, vid):
        L = vinfo[vid].get("length_m")
        return bool(L) and abs(det["length_m"] - L) / L <= f["max_length_mismatch"]

    pairs = sorted(((haversine_km(d["lat"], d["lon"], p["lat"], p["lon"]), d, vid, p) for d in dets for vid, p in live
                    if haversine_km(d["lat"], d["lon"], p["lat"], p["lon"]) <= f["match_radius_km"] and length_ok(d, vid)),
                   key=lambda x: x[0])
    used_d, used_v, matches = set(), set(), []
    for dist, d, vid, p in pairs:
        if d["id"] in used_d or vid in used_v:
            continue
        used_d.add(d["id"])
        used_v.add(vid)
        matches.append({"det": d, "vessel_id": vid, "pos": p, "dist_km": dist})

    infra = []
    for d in dets:
        if d["id"] in used_d:
            continue
        pf = next((x for x in geo["platforms"] if haversine_km(d["lat"], d["lon"], x["lat"], x["lon"]) <= f["platform_radius_km"]), None)
        if pf:
            used_d.add(d["id"])
            infra.append({"det": d, "platform": pf})

    gaps = []
    for vid, last, gap_min in silent:
        if gap_min < f["gap_minutes"]:
            continue
        travel = last["sog"] * KM_PER_NM * gap_min / 60
        plat, plon = move(last["lat"], last["lon"], last["cog"], travel)
        tol = max(f["gap_min_tolerance_km"], f["match_radius_km"] + f["gap_drift_pct"] * travel)
        entry = {"vessel_id": vid, "last": last, "gap_min": gap_min, "predicted": (plat, plon), "status": "outside"}
        if in_poly(plon, plat, footprint):
            cands = sorted(((haversine_km(d["lat"], d["lon"], plat, plon), d) for d in dets
                            if d["id"] not in used_d and length_ok(d, vid)), key=lambda x: x[0])
            cands = [c for c in cands if c[0] <= tol]
            if cands:
                used_d.add(cands[0][1]["id"])
                entry.update(status="matched", det=cands[0][1], offset_km=cands[0][0])
            else:
                entry["status"] = "unseen"
        gaps.append(entry)

    dark = []
    for d in dets:
        if d["id"] in used_d:
            continue
        near = next((m for m in matches if vinfo[m["vessel_id"]]["type"] in ("tanker", "cargo")
                     and haversine_km(d["lat"], d["lon"], m["pos"]["lat"], m["pos"]["lon"]) <= f["sts_radius_km"]), None)
        z = zone_at(geo, d["lon"], d["lat"])
        dark.append({"det": d, "zone": z["id"] if z else None, "low_conf": d["confidence"] < f["low_confidence"],
                     "alongside": near["vessel_id"] if near else None})
    return {"time": T, "matches": matches, "infrastructure": infra, "gaps": gaps, "dark": dark}
