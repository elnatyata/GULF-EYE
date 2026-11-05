"""Threat detection and explainable risk scoring.

Pipeline: adapters -> pandas features -> rule indicators + Isolation Forest -> score 0-100 -> level -> alerts.

Principles (enforced in code and tested in tests/test_engine.py):
  * Several indicators are combined; each one carries points, evidence and a plain reason.
  * An AIS gap on its own is never more than Medium: gaps also come from coverage holes and equipment faults.
  * The Isolation Forest is one indicator among others and cannot push a vessel above Medium by itself.
  * Critical needs indicators from at least two independent groups.
  * Scores support human investigation. They never trigger enforcement and never state guilt.
"""
import datetime as dt
import json

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from .. import audit, config, db
from ..adapters import AIS, GEO, SAR
from . import correlation, fusion
from .features import FEATURE_LABELS, FEATURES, rendezvous, vessel_features

MODEL_VERSION = "gulfeye-risk-1.0 (rules + IsolationForest)"
LEVELS = [(75, "critical"), (50, "high"), (25, "medium"), (0, "low")]
GROUP_LABELS = {"ais": "AIS signal", "behaviour": "Movement behaviour", "location": "Location",
                "record": "Records and history", "agency": "Agency reports", "model": "Statistical outlier (ML)"}
AGENCY_WINDOW_H = 72          # agency sightings and reports count for 3 days
CONTACT_KM, CONTACT_MIN = 1.0, 45   # unidentified radar contact within 1 km and 45 min of a vessel
CORROB_STEP, CORROB_CAP = 3, 6      # each extra independent agency adds 3 points, at most 6 in total
DISCLAIMER = ("Indicators for human investigation only. They are not proof of wrongdoing and must not by themselves "
              "trigger enforcement action.")


def _lc(text):
    return text if text[:3].isupper() else text[0].lower() + text[1:]


def level_of(score):
    return next(name for floor, name in LEVELS if score >= floor)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t is not None else None


def _fmt_min(m):
    h, mm = divmod(int(round(m)), 60)
    return f"{h} h {mm:02d} min" if h else f"{mm} min"


def isolation_forest(feats):
    """Fit an Isolation Forest on the behaviour features (+ vessel type). Returns {vid: (score 0-1, outlier, drivers)}."""
    X = feats[FEATURES].astype(float).copy()
    types = feats["type"].fillna("unknown")
    onehot = pd.get_dummies(types).to_numpy(dtype=float)
    Xs = StandardScaler().fit_transform(X.values)
    model = IsolationForest(n_estimators=300, contamination=0.08, random_state=42)
    model.fit(np.hstack([Xs, onehot]))
    raw = -model.score_samples(np.hstack([Xs, onehot]))        # higher = more anomalous
    flags = model.predict(np.hstack([Xs, onehot])) == -1
    lo, hi = raw.min(), raw.max()
    norm = (raw - lo) / (hi - lo or 1)
    # explanation: features furthest from the vessel's own type peers (robust z-score)
    out = {}
    for i, vid in enumerate(feats.index):
        peers = X[types == types.iloc[i]]
        if len(peers) < 4:
            peers = X
        med = peers.median()
        mad = (peers - med).abs().median().replace(0, np.nan)
        z = ((X.iloc[i] - med) / (1.4826 * mad)).replace([np.inf, -np.inf], np.nan).fillna(0)
        drivers = [(f, float(z[f]), float(X.iloc[i][f]), float(med[f])) for f in z.abs().sort_values(ascending=False).index[:3]
                   if abs(z[f]) >= 2.5]
        out[vid] = (float(norm[i]), bool(flags[i]), drivers)
    return out


def assess(state):
    """Score every vessel. Returns {vid: assessment dict}."""
    ais, geo, f = state["ais"], state["geo"], state["assumptions"]["fusion"]
    now = ais["meta"]["now_effective"]
    feats, det = vessel_features(ais, geo)
    feats["type"] = [ais["vessels"][v]["type"] for v in feats.index]
    fu = fusion.run(ais, state["sar"], geo, f)
    rdv = rendezvous(ais, geo)
    iforest = isolation_forest(feats)
    zones = {z["id"]: z for z in geo["zones"]}
    gap_seen = {g["vessel_id"]: g for g in fu["gaps"]}
    alongside = {}
    for d in fu["dark"]:
        if d["alongside"] and not d["low_conf"]:
            alongside.setdefault(d["alongside"], []).append(d)

    agency = _agency_inputs(ais)
    tracks = {}
    for r in ais["rows"]:
        tracks.setdefault(r["vessel_id"], []).append(r)
    for rows_ in tracks.values():
        rows_.sort(key=lambda r: r["t"])
    results = {}
    for vid, row in feats.iterrows():
        info, d = ais["vessels"][vid], det[vid]
        vtype = info["type"]
        ind = []

        def add(code, group, points, title, evidence, reason, steps, at=None, extra=None):
            ind.append({"code": code, "group": group, "points": round(points, 1), "title": title, "evidence": evidence,
                        "reason": reason, "steps": steps, "at": _iso(at), **(extra or {})})

        # 1. AIS signal disruption
        if row["gap_max_min"] >= f["gap_minutes"]:
            trailing = d["trailing_gap_min"] >= f["gap_minutes"]
            pts = 15 + (10 if trailing else 0)
            ev = [f"Longest silence {_fmt_min(row['gap_max_min'])} (threshold {f['gap_minutes']} min).",
                  f"{d['n']} of about {round(d['n'] / max(row['report_ratio'], 1e-6))} expected reports received."]
            if trailing:
                ev.append(f"Still silent: last report {_iso(d['last_t'])} at {d['last']['lat']:.3f}°N {d['last']['lon']:.3f}°E, "
                          f"{d['last']['sog']:.1f} kn.")
            g = gap_seen.get(vid)
            if g and g["status"] == "matched":
                pts += 10
                ev.append(f"Sentinel-1 radar detection {g['det']['id']} at {fu['time']:%H:%M} UTC lies {g['offset_km']:.1f} km from "
                          f"its dead-reckoned position: the vessel was still at sea while not transmitting.")
            add("ais_gap", "ais", pts, "AIS signal disruption", ev,
                "Transmissions stopped for longer than normal reporting allows. Gaps also come from coverage holes, "
                "equipment faults or receiver saturation, so this alone is not evidence of intent.",
                ["Check terrestrial and satellite AIS coverage for the area and time of the gap.",
                 "Compare with the next radar pass or an air patrol sighting.",
                 "Contact the vessel on VHF 16 or through its agent and ask why AIS is off."], at=d["last_t"])

        # 2. Presence in a high-risk zone
        if row["hr_frac"] >= 0.25 and d["hr_zones"]:
            zn = ", ".join(zones[z]["name"] for z in d["hr_zones"])
            add("high_risk_area", "location", 10, "Activity in a high-risk zone",
                [f"{row['hr_frac'] * 100:.0f}% of reports inside {zn}."],
                "These zones have the highest concentration of reported attacks and boardings.",
                ["Check whether the vessel has a declared reason to be in the zone (fishing licence, offshore contract, port call).",
                 "Share its position with patrols operating in the zone."])

        # 3. Loitering
        slow_small = row["rg_km"] < 4 and 0.3 <= row["sog_mean"] <= 4 and d["n"] >= 9 and d["anch_frac"] < 0.5
        if slow_small and not (vtype == "supply" and d["near_platform"]):
            if vtype == "fishing":
                if row["hr_frac"] >= 0.25:
                    add("loitering", "behaviour", 8, "Loitering in a high-risk zone",
                        [f"Stayed within {row['rg_km']:.1f} km at {row['sog_mean']:.1f} kn on average for {d['n']} reports."],
                        "Slow, confined movement is normal for fishing but also how attack groups wait for targets.",
                        ["Check the vessel's fishing licence and declared fishing area.", "Ask a patrol to observe gear on deck."])
            else:
                add("loitering", "behaviour", 15, "Loitering outside an anchorage",
                    [f"Stayed within {row['rg_km']:.1f} km at {row['sog_mean']:.1f} kn on average for {d['n']} reports, away from ports and anchorages."],
                    "A merchant or supply vessel with no reason to wait at sea may be waiting for a rendezvous.",
                    ["Ask the agent or charterer for the voyage plan.", "Watch for other contacts approaching on the next pass."])

        # 4. Unexpected stop
        if vtype in ("cargo", "tanker") and d["underway_first_half"] > 5 and d["stopped_tail"] and not d["tail_anch"]:
            add("unexpected_stop", "behaviour", 15, "Unexpected stop at sea",
                [f"Averaged {d['underway_first_half']:.1f} kn, then below 1 kn for the last three reports away from any anchorage."],
                "A sudden stop in open water can mean engine trouble, a transfer or a vessel under attack.",
                ["Call the vessel to check on crew safety first.", "Alert nearby patrols and the coordination centre."], at=d["last_t"])

        # 5. Abnormal speed change
        lim = 6 if vtype == "fishing" else 5
        if row["dsog_max"] >= lim:
            add("speed_change", "behaviour", 10, "Abnormal speed change",
                [f"Speed changed by {row['dsog_max']:.1f} kn between two consecutive reports."],
                "Sharp accelerations are typical of evasive manoeuvres or a chase.",
                ["Look for small contacts near the vessel at that time.", "Ask the master whether there was an approach."], at=d["dsog_at"])

        # 6. Erratic course
        if row["turn_mean"] > 60 and row["sog_mean"] > 1:
            add("erratic_course", "behaviour", 8, "Erratic course changes",
                [f"Course changed by {row['turn_mean']:.0f}° on average between reports while underway."],
                "Repeated reversals suggest searching, shadowing or deliberate confusion of the track.",
                ["Overlay the track on the risk zones and on nearby vessels.", "Check if the pattern matches its fishing gear."])

        # 7. Route deviation
        if vtype in ("cargo", "tanker") and row["sog_mean"] > 3 and row["lane_dist_km"] > 20:
            add("route_deviation", "behaviour", 10, "Unusual route",
                [f"Underway on average {row['lane_dist_km']:.0f} km from the usual shipping lanes."],
                "Merchant ships normally follow the lanes between ports; large detours need a reason.",
                ["Compare the route with its declared destination.", "Check weather routing or port congestion."])

        # 8. Rendezvous with another AIS vessel
        for r in rdv.get(vid, []):
            partner = ais["vessels"][r["partner"]]
            add("rendezvous", "behaviour", 18, "Rendezvous with another vessel",
                [f"Within {r['min_km'] * 1000:.0f} m of {partner['name']} ({r['partner']}) at under 2 kn for {r['count']} reports, "
                 f"{_iso(r['first'])} to {_iso(r['last'])}, outside any anchorage."],
                "Two slow vessels side by side in open water is the signature of a ship-to-ship transfer.",
                ["Check whether a transfer was notified to the port authority.", "Request an overflight or next radar pass."],
                at=r["first"], extra={"partner": r["partner"]})

        # 9. Close contact with a non-reporting (dark) vessel, from radar
        for dk in alongside.get(vid, []):
            z = zones.get(dk["zone"])
            add("dark_alongside", "behaviour", 25, "Close contact with a non-reporting vessel",
                [f"Radar detection {dk['det']['id']} ({dk['det']['length_m']} m, {dk['det']['confidence'] * 100:.0f}% confidence) "
                 f"with no AIS lies within {f['sts_radius_km']} km of this vessel at {fu['time']:%H:%M} UTC"
                 f"{(' in ' + z['name']) if z else ''}."],
                "A vessel without AIS alongside a tanker or cargo ship can be an undeclared transfer (fuel, cargo or people).",
                ["Task an overflight or patrol boat to identify the second vessel.",
                 "Ask the port authority whether a transfer or bunkering was authorised."],
                at=fu["time"], extra={"detection": dk["det"]["id"]})

        # 10-13. Records: incidents, watchlist, identity, owner network (correlation)
        state_now = ais["meta"]["now_effective"]
        since = _iso(state_now - dt.timedelta(days=365))
        incs = [i for i in correlation.linked_incidents(vid) if i["occurred_at"] >= since]
        suspect = [i for i in incs if i["role"] == "suspect"]
        if suspect:
            add("prior_incident", "record", 15, "Named in a recent incident",
                [f"{i['id']} · {i['occurred_at'][:10]} · {i['title']} (status: {i['status'].replace('_', ' ')})." for i in suspect],
                "The vessel was named as a suspect in an incident report in the last 12 months.",
                ["Read the incident file and its evidence.", "Contact the agency that reported it before acting."],
                extra={"incidents": [i["id"] for i in suspect]})
        idc = correlation.identity_checks(vid, info)
        reg = idc["registry"]
        if reg and reg.get("watchlist"):
            add("watchlist", "record", 12, "On a watchlist", [reg["watchlist"]],
                "The vessel appears in a bulletin or list shared by partner agencies.",
                ["Confirm the listing is current with the issuing agency.", "Check for name or flag changes since listing."])
        if idc["issues"]:
            add("identity_mismatch", "record", 10, "Identity does not match the registry", idc["issues"],
                "Broadcast details that differ from the registration record can indicate spoofing or a recent sale.",
                ["Verify IMO number and call sign by radio.", "Ask the flag state to confirm the registration."])
        if idc.get("flag_changes_5y", 0) >= 2:
            hist = " → ".join(f"{h['flag']} ({h['from']})" for h in reg["flag_history"])
            add("flag_hopping", "record", 6, "Frequent flag changes", [f"Flag history: {hist}."],
                "Several flag changes in a few years can be used to avoid inspections or sanctions.",
                ["Check port-state control records under previous flags and names."])
        adverse = [p for p in correlation.owner_network(vid) if p["adverse"]]
        if adverse:
            add("owner_network", "record", 6, "Owner linked to other flagged vessels",
                [f"{p['name']} ({p['link']}): " + ("on a watchlist" if p["watchlist"] else f"suspect in {', '.join(i['id'] for i in p['suspect_incidents'])}")
                 for p in adverse],
                "Vessels under the same owner or manager as flagged vessels deserve a closer look, not a presumption.",
                ["Review the owner's fleet and recent incidents together."], extra={"vessels": [p["vessel_id"] for p in adverse]})

        # 15-17. Data submitted by agencies (shared records only, so restricted content never reaches alert evidence).
        # Several agencies saying the same thing raise confidence through a capped corroboration bonus; they do not stack.
        wl = agency["watch"].get(vid, [])
        if wl:
            top_level = "suspect" if any(w["level"] == "suspect" for w in wl) else "monitor"
            ags = sorted({w["agency"] for w in wl})
            bonus = min(CORROB_CAP, CORROB_STEP * (len(ags) - 1))
            add("agency_watchlist", "agency", (15 if top_level == "suspect" else 6) + bonus,
                f"On {'/'.join(ags)} watchlist ({top_level})",
                [f"{w['agency']} ({w['level']}): {w['reason']}" + (f" Valid until {w['valid_until']}." if w["valid_until"] else "")
                 + f" Listed {w['listed_at'][:10]}, matched by {w['match']}." for w in wl]
                + ([f"Listed independently by {len(ags)} agencies: corroboration bonus +{bonus} (capped at {CORROB_CAP})."] if bonus else []),
                "A partner agency has asked for this vessel to be monitored." if top_level == "monitor"
                else "A partner agency names this vessel as a suspect in its own work.",
                [f"Contact {', '.join(ags)} for the background before acting.", "Check the listings are still current."],
                extra={"watchlist": [w["id"] for w in wl], "agencies": ags})
        reps = agency["reports"].get(vid, [])
        if reps:
            ags = sorted({o["agency"] for o in reps})
            bonus = min(CORROB_CAP, CORROB_STEP * (len(ags) - 1))
            add("agency_report", "agency", 8 + bonus, "Reported by an agency patrol or sighting",
                [f"{o['agency']} {o['kind'].replace('_', ' ')} {o['observed_at'][:16].replace('T', ' ')} UTC at {o['lat']:.3f}°N "
                 f"{o['lon']:.3f}°E: {o['description'] or 'no description'}" for o in reps[:4]]
                + ([f"Reported independently by {len(ags)} agencies: corroboration bonus +{bonus}."] if bonus else []),
                "A patrol, aircraft or observer from a partner agency reported this vessel in the last 3 days.",
                ["Read the full report and contact the reporting unit.", "Compare with the vessel's AIS track at that time."],
                at=_parse_obs(reps[0]["observed_at"]), extra={"observations": [o["id"] for o in reps], "agencies": ags})
        near = _contacts_near(agency["contacts"], tracks.get(vid, []), ais["meta"]["step_min"], info.get("length_m"))
        if near:
            srcs = sorted({o["agency"] for o in near})
            bonus = min(CORROB_CAP, CORROB_STEP * (len(srcs) - 1))
            add("agency_contact", "behaviour", 15 + bonus, "Unidentified radar contact alongside",
                [f"{o['agency']} radar contact OBS-{o['id']} ({o.get('length_m') or '?'} m) at {o['observed_at'][11:16]} UTC, "
                 f"{o['dist_km'] * 1000:.0f} m from this vessel's position at that time; the contact sent no AIS." for o in near]
                + ([f"Seen by {len(srcs)} independent radars: corroboration bonus +{bonus}."] if bonus else []),
                "A second, non-reporting craft next to a vessel can mean an undeclared transfer or an approach.",
                ["Ask the radar operator for the contact's track before and after.", "Task a patrol or overflight to identify it."],
                at=_parse_obs(near[0]["observed_at"]), extra={"observation": near[0]["id"], "observations": [o["id"] for o in near]})

        # 14. Isolation Forest
        ml_score, outlier, drivers = iforest[vid]
        if outlier and drivers:
            add("ml_outlier", "model", 6 + 6 * ml_score, "Unusual behaviour pattern (Isolation Forest)",
                [f"{FEATURE_LABELS[fn]}: {val:.1f} (typical for a {vtype}: {med:.1f})" for fn, _, val, med in drivers],
                "The model flags this track as unlike the others of its type. It points where to look; it explains nothing by itself.",
                ["Review the track for the features listed above."])

        # ---- combine, with the safeguards ----
        score = min(100.0, sum(i["points"] for i in ind))
        groups = {i["group"] for i in ind if i["group"] != "model"}
        caps = []
        if not groups and ind:
            score, _ = min(score, 40), caps.append("Only the statistical model flagged this vessel: capped at Medium.")
        if groups == {"ais"}:
            score = min(score, 45)
            caps.append("AIS disruption is the only indicator group: capped at Medium. Signal loss is not proof of wrongdoing.")
        if score >= 75 and len(groups) < 2:
            score = 74
            caps.append("Critical needs indicators from at least two independent groups: capped at High.")
        level = level_of(score)
        steps = []
        for i in sorted(ind, key=lambda x: -x["points"]):
            for st in i["steps"]:
                if st not in steps:
                    steps.append(st)
        reasons = [f"{GROUP_LABELS[g]}: " + "; ".join(i["title"] for i in ind if i["group"] == g)
                   for g in GROUP_LABELS if any(i["group"] == g for i in ind)]
        results[vid] = {
            "vessel_id": vid, "score": round(score, 1), "level": level, "indicators": ind, "groups": sorted(groups),
            "reasons": reasons + caps, "steps": steps[:6], "ml_score": round(ml_score, 3), "ml_outlier": outlier,
            "features": {k: round(float(row[k]), 3) for k in FEATURES},
            "last": {**d["last"], "t": _iso(d["last_t"])}, "trailing_gap_min": round(d["trailing_gap_min"]),
        }
    return results, fu


def _parse_obs(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _agency_inputs(ais):
    """Shared, active agency records relevant to the current picture."""
    now = ais["meta"]["now_effective"]
    codes = {a["id"]: a["code"] for a in db.q("SELECT id, code FROM agencies")}
    watch = {}
    for w in db.q("SELECT * FROM watchlist WHERE active = 1 AND visibility = 'shared' AND vessel_id IS NOT NULL"):
        if w["valid_until"] and w["valid_until"] < now.strftime("%Y-%m-%d"):
            continue
        watch.setdefault(w["vessel_id"], []).append({**w, "agency": codes[w["agency_id"]]})
    since = (now - dt.timedelta(hours=AGENCY_WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reports, contacts = {}, []
    for o in db.q("SELECT * FROM observations WHERE visibility = 'shared' AND observed_at >= ? ORDER BY observed_at DESC", (since,)):
        o["agency"] = codes[o["agency_id"]]
        if o["vessel_id"]:
            reports.setdefault(o["vessel_id"], []).append(o)
        elif o["kind"] == "radar_contact":
            contacts.append(o)
    return {"watch": watch, "reports": reports, "contacts": contacts}


def _contacts_near(contacts, rows, step_min, length_m=None):
    out = []
    for o in contacts:
        if length_m and o.get("length_m") and abs(o["length_m"] - length_m) / length_m <= 0.6:
            continue        # same size as the vessel: probably the vessel itself, not a second craft
        t = _parse_obs(o["observed_at"])
        p = fusion.position_at(rows, t, step_min, CONTACT_MIN)
        if not p:
            continue
        d = fusion.haversine_km(o["lat"], o["lon"], p["lat"], p["lon"])
        if d <= CONTACT_KM:
            out.append({**o, "dist_km": d})
    return out


def load_state():
    with open(config.DATA / "assumptions.json", encoding="utf-8") as fh:
        assumptions = json.load(fh)
    return {"ais": AIS.load(), "sar": SAR.load(), "geo": GEO.load(), "assumptions": assumptions}


def _next_alert_id():
    row = db.one("SELECT id FROM alerts ORDER BY id DESC LIMIT 1")
    n = int(row["id"].split("-")[1]) + 1 if row else 1
    return f"A-{n:04d}"


def run_engine(user=None):
    """Score all vessels, store the assessments, create or update alerts. Returns a summary with changed alert ids."""
    state = load_state()
    results, _ = assess(state)
    now = audit.now_iso()
    synthetic = int(AIS.synthetic or SAR.synthetic)
    created, updated = [], []
    with db.tx() as c:
        for vid, r in results.items():
            c.execute("""INSERT INTO vessel_risk(vessel_id, score, level, indicators, reasons, steps, features, ml_score, model_version, computed_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(vessel_id) DO UPDATE SET score=excluded.score, level=excluded.level,
                         indicators=excluded.indicators, reasons=excluded.reasons, steps=excluded.steps, features=excluded.features,
                         ml_score=excluded.ml_score, model_version=excluded.model_version, computed_at=excluded.computed_at""",
                      (vid, r["score"], r["level"], json.dumps(r["indicators"]), json.dumps(r["reasons"]), json.dumps(r["steps"]),
                       json.dumps(r["features"]), r["ml_score"], MODEL_VERSION, now))
            if r["level"] == "low":
                continue
            v = state["ais"]["vessels"][vid]
            top = sorted(r["indicators"], key=lambda i: -i["points"])
            behaviours = [i["code"] for i in top]
            named = [i for i in top if i["group"] != "model"] or top
            title = f"{v['name']}: " + ", ".join(_lc(i["title"]) for i in named[:2])
            pos = r["last"]
            radar = next((i for i in top if i["code"] in ("dark_alongside",)), None)
            observed = max((i["at"] for i in top if i["at"]), default=r["last"]["t"])
            evidence = [{"indicator": i["title"], "points": i["points"], "items": i["evidence"], "reason": i["reason"]} for i in top]
            existing = c.execute("SELECT * FROM alerts WHERE vessel_id = ? AND kind = 'behaviour' AND status != 'closed'", (vid,)).fetchone()
            if existing:
                if existing["level"] != r["level"] or abs(existing["score"] - r["score"]) >= 1:
                    c.execute("""UPDATE alerts SET level=?, score=?, title=?, behaviours=?, evidence=?, actions=?, lat=?, lon=?,
                                 observed_at=?, updated_at=? WHERE id=?""",
                              (r["level"], r["score"], title, json.dumps(behaviours), json.dumps(evidence), json.dumps(r["steps"]),
                               pos["lat"], pos["lon"], observed, now, existing["id"]))
                    c.execute("INSERT INTO alert_history(alert_id, user_id, from_status, to_status, note, at) VALUES(?,?,?,?,?,?)",
                              (existing["id"], None, existing["status"], existing["status"],
                               f"Re-scored by the engine: {existing['level']} {existing['score']:.0f} → {r['level']} {r['score']:.0f}", now))
                    updated.append(existing["id"])
                continue
            aid = _next_alert_id()
            c.execute("""INSERT INTO alerts(id, vessel_id, kind, level, score, lat, lon, observed_at, title, behaviours, evidence, actions,
                         status, created_at, updated_at, source, synthetic) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (aid, vid, "behaviour", r["level"], r["score"], pos["lat"], pos["lon"], observed, title, json.dumps(behaviours),
                       json.dumps(evidence), json.dumps(r["steps"]), "new", now, now, MODEL_VERSION, synthetic))
            c.execute("INSERT INTO alert_history(alert_id, user_id, from_status, to_status, note, at) VALUES(?,?,?,?,?,?)",
                      (aid, None, None, "new", f"Raised by the engine ({r['level']}, score {r['score']:.0f})."
                       + (f" Radar evidence {radar['detection']}." if radar else ""), now))
            created.append(aid)
    db.set_meta("engine_last_run", {"at": now, "model": MODEL_VERSION, "vessels": len(results),
                                    "levels": {lv: sum(1 for r in results.values() if r["level"] == lv) for _, lv in LEVELS}})
    audit.log(user, "engine.run", None, {"created": created, "updated": updated})
    return {"created": created, "updated": updated, "vessels": len(results)}
