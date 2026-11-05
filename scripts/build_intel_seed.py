#!/usr/bin/env python3
"""Build the SIMULATED intelligence seed for GulfEye: a vessel registry and a 12-month incident history.

    python3 scripts/build_intel_seed.py

Writes backend/seed_data/registry.json and backend/seed_data/incidents_history.json. Everything is invented:
company names, IMO numbers (valid check digit, but not real ships), inspections and incidents. A few links are placed
on purpose so the correlation engine has something to find (shared owner, flag history, prior incident, a vessel
that was a victim earlier). The files are loaded by the backend on first start.
"""
import datetime as dt
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import build_data as bd  # noqa: E402  (reuses its geometry helpers and the geo file)
import extend_data as ext  # noqa: E402  (sea test on the real coastline)

OUT = os.path.join(ROOT, "backend", "seed_data")
SEED = 20261105
NOW = dt.datetime(2026, 11, 4, 8, 0, tzinfo=dt.timezone.utc)

OWNERS = ["Atlantic Meridian Shipping Ltd", "Cobalt Tankers SA", "Delta Coastal Lines",
          "Equatorial Bulk Carriers", "Harbour Gate Maritime", "Kribi Offshore Services",
          "Mono River Trading", "Niger Delta Marine Logistics", "Palmline Feeder Services",
          "Tidewater Supply Nigeria", "Volta Shipping Agency", "West Coast Tankers Ltd"]
CLASS = ["Class society A", "Class society B", "Class society C", None]


def imo_number(rng) -> str:
    d = [rng.randrange(1, 10)] + [rng.randrange(10) for _ in range(5)]
    check = sum(x * w for x, w in zip(d, range(7, 1, -1))) % 10
    return "".join(map(str, d + [check]))


def call_sign(rng, flag):
    prefix = {"Nigeria": "5N", "Ghana": "9G", "Benin": "TY", "Togo": "5V", "Cameroon": "TJ", "Liberia": "A8",
              "Panama": "3E", "Marshall Is.": "V7", "Malta": "9H", "Singapore": "9V", "Sierra Leone": "9L"}.get(flag, "XX")
    return prefix + "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ0123456789") for _ in range(3))


def build_registry(rng, vessels):
    recs = []
    for v in vessels:
        small_fishing = v["type"] == "fishing" and v["length_m"] < 24
        r = {
            "vessel_id": v["id"], "mmsi": v["id"], "imo": None if small_fishing else imo_number(rng), "name": v["name"],
            "flag": v["flag"], "call_sign": call_sign(rng, v["flag"]),
            "owner": rng.choice(OWNERS) if v["type"] != "fishing" else f"{v['name'][3:]} Fishing Co.",
            "manager": None, "built": rng.randrange(1996, 2022),
            "gross_tonnage": int(v["length_m"] ** 2 * (1.1 if v["type"] in ("cargo", "tanker") else 0.6)),
            "length_m": v["length_m"], "class_society": rng.choice(CLASS),
            "flag_history": [{"flag": v["flag"], "from": rng.randrange(2012, 2024)}],
            "last_psc": {"date": f"2026-{rng.randrange(1, 10):02d}-{rng.randrange(1, 28):02d}",
                         "port": rng.choice(["Lagos", "Tema", "Douala", "Lomé", "Onne"]),
                         "deficiencies": rng.choice([0, 0, 0, 1, 2, 3, 5])} if not small_fishing else None,
            "watchlist": None, "notes": None,
        }
        r["manager"] = r["owner"]
        recs.append(r)
    by_name = {r["name"]: r for r in recs}

    # --- deliberate links for the correlation demo (all invented) ---
    gv = by_name["MT Gulf Voyager"]
    gv.update(owner="Bight Star Shipping Ltd", manager="Bight Star Shipping Ltd", class_society=None,
              flag_history=[{"flag": "Togo", "from": 2021}, {"flag": "Comoros", "from": 2024}, {"flag": "Panama", "from": 2025}],
              former_names=["Sea Pearl II", "Bight Star 3"],
              watchlist="Named in a simulated 2026 regional bulletin on illegal bunkering (demo data).",
              last_psc={"date": "2026-02-11", "port": "Lomé", "deficiencies": 9})
    sh = by_name["MV Sea Harmony"]
    sh.update(owner="Bight Star Shipping Ltd", manager="Bight Star Shipping Ltd",
              flag_history=[{"flag": "Comoros", "from": 2022}, {"flag": "Sierra Leone", "from": 2025}])
    rl = by_name["FV Red Lantern"]
    rl.update(flag="Togo", notes="Registered flag (Togo) differs from the flag broadcast on AIS (Benin).")
    oh = by_name["FV Ocean Hope"]
    oh.update(notes="Fishing licence for the Ghana EEZ only (simulated).")
    for r in recs:
        r.setdefault("former_names", [])
    return recs


TYPES = {  # type: (weight, zones / anchor points, label)
    "piracy_attack": (12, "piracy"), "boarding": (10, "piracy"), "kidnapping": (6, "deep"),
    "armed_robbery": (16, "anchorage"), "suspicious_approach": (22, "any"), "illegal_fishing": (14, "fishing"),
    "oil_theft": (10, "delta"), "sts_transfer": (6, "anchorage"), "smuggling": (4, "coast"),
}
TITLES = {
    "piracy_attack": ["Armed skiff fired on {v}", "Attack on {v} repelled by citadel crew", "Attempted hijack of {v}"],
    "boarding": ["Unauthorised boarding of {v}", "Boarding attempt on {v} at drifting position"],
    "kidnapping": ["Crew members abducted from {v}", "Kidnapping of crew from {v}"],
    "armed_robbery": ["Robbery at anchor: stores stolen from {v}", "Theft of ship's property from {v} at anchorage"],
    "suspicious_approach": ["Fast skiff approached {v}", "Suspicious craft shadowing {v}", "Unlit boat approached {v} at night"],
    "illegal_fishing": ["Trawler fishing without licence", "Fishing inside the inshore exclusion zone", "Transshipment of catch at sea"],
    "oil_theft": ["Illegal bunkering barge observed", "Suspected crude theft from pipeline-fed barge", "Tanker loading from unregistered barge"],
    "sts_transfer": ["Unannounced ship-to-ship transfer", "STS transfer without port notification"],
    "smuggling": ["Suspected fuel smuggling by fast boat", "Contraband transfer near the coast"],
}
DESCR = {
    "piracy_attack": "Master reported an armed approach. Crew mustered in the citadel; ship increased speed. No injuries reported.",
    "boarding": "Persons boarded via the stern; alarm raised and the boarders left. Items missing are being checked.",
    "kidnapping": "Vessel boarded far offshore; part of the crew taken. Remaining crew sailed the vessel to a safe port.",
    "armed_robbery": "Robbers boarded at anchor during the night and escaped with ship's stores when spotted by the watch.",
    "suspicious_approach": "A small craft approached at speed and turned away when the ship altered course and sounded the alarm.",
    "illegal_fishing": "Patrol observed fishing activity not matching any licence on record. Catch and nets photographed.",
    "oil_theft": "Barge and small tanker observed alongside with hoses connected; no notified transfer for this position.",
    "sts_transfer": "Ship-to-ship transfer observed with no prior notification to the port authority.",
    "smuggling": "Fast boat with drums on deck observed heading towards the creeks.",
}


def pick_position(rng, mode, geo):
    zones = {z["id"]: z for z in geo["zones"]}
    anchors = {"anchorage": [(6.2, 3.4), (6.25, 2.45), (6.05, 1.3), (3.85, 9.3), (4.3, 7.05), (5.15, -4.0), (5.55, 0.05),
                             (0.42, 9.2), (-0.55, 8.62), (4.8, -1.7)],
               "delta": [(4.2, 5.4), (4.25, 6.0), (4.15, 6.8), (5.25, 5.25)], "coast": [(6.25, 3.0), (4.4, 7.6), (6.1, 1.8), (5.0, -3.5)]}
    for _ in range(200):
        if mode in anchors:
            lat, lon = rng.choice(anchors[mode])
            lat, lon = lat + rng.gauss(0, 0.12), lon + rng.gauss(0, 0.15)
        elif mode == "deep":
            z = zones[rng.choice(["Z5", "Z5", "Z6"])]
            xs = [p[0] for p in z["poly"]]
            ys = [p[1] for p in z["poly"]]
            lon, lat = rng.uniform(min(xs), max(xs)), rng.uniform(min(ys), max(ys))
        else:
            if mode == "piracy":
                z = zones[rng.choice(["Z1", "Z1", "Z2", "Z5", "Z6"])]
            elif mode == "fishing":
                z = zones[rng.choice(["Z4", "Z7", "Z7"])]
            else:
                z = zones[rng.choice(["Z1", "Z2", "Z3", "Z4", "Z1", "Z5", "Z7", "Z8"])]
            xs = [p[0] for p in z["poly"]]
            ys = [p[1] for p in z["poly"]]
            lon, lat = rng.uniform(min(xs), max(xs)), rng.uniform(min(ys), max(ys))
        if ext.sea(lon, lat):
            return round(lat, 4), round(lon, 4)
    raise RuntimeError(mode)


def nearest_place(geo, lat, lon):
    best = min(geo["ports"], key=lambda p: bd.haversine_km(lat, lon, p["lat"], p["lon"]))
    km = bd.haversine_km(lat, lon, best["lat"], best["lon"])
    return f"{round(km / 1.852)} nm off {best['name']}" if km > 9 else f"{best['name']} anchorage"


def build_incidents(rng, vessels, geo):
    names = [v["name"] for v in vessels if v["type"] in ("cargo", "tanker", "supply")]
    kinds = list(TYPES)
    weights = [TYPES[k][0] for k in kinds]
    incs = []
    start = NOW - dt.timedelta(days=729)
    agencies = ["NIMASA", "NN", "MPOL", "NPA", "NAF", "NCS", "MMCC"]
    family = lambda t: "attack" if t in ("piracy_attack", "boarding", "armed_robbery", "kidnapping") else t  # noqa: E731
    while len(incs) < 480:
        # more activity in the calm dry season (Nov–Mar)
        while True:
            t = start + dt.timedelta(minutes=rng.randrange(0, 364 * 24 * 60))
            if rng.random() < (1.0 if t.month in (11, 12, 1, 2, 3) else 0.55):
                break
        kind = rng.choices(kinds, weights)[0]
        lat, lon = pick_position(rng, TYPES[kind][1], geo)
        victim = rng.choice(["a tanker", "a cargo ship", "a supply vessel", "a fishing canoe"]) if kind != "illegal_fishing" else ""
        title = rng.choice(TITLES[kind]).format(v=victim or "vessel").replace("  ", " ")
        age = (NOW - t).days
        status = ("closed" if age > 60 else rng.choice(["verified", "under_investigation", "closed"]) if age > 10
                  else rng.choice(["reported", "verified", "under_investigation"]))
        if any(family(o["type"]) == family(kind) and abs((dt.datetime.fromisoformat(o["occurred_at"][:-1] + "+00:00") - t).total_seconds()) < 8 * 3600
               and bd.haversine_km(lat, lon, o["lat"], o["lon"]) < 15 for o in incs):
            continue        # keep the history free of accidental duplicates (deliberate ones are added below)
        incs.append({"type": kind, "title": title[0].upper() + title[1:], "description": DESCR[kind], "lat": lat, "lon": lon,
                     "place": nearest_place(geo, lat, lon), "occurred_at": t.strftime("%Y-%m-%dT%H:%M:00Z"), "status": status,
                     "visibility": "agency" if rng.random() < 0.15 else "shared", "agency": rng.choice(agencies), "vessels": []})
    by = {v["name"]: v["id"] for v in vessels}
    n_random = len(incs)
    incs += [
        {"type": "oil_theft", "title": "Tanker loading from unregistered barge off Forcados",
         "description": "Air patrol photographed MT Gulf Voyager (then broadcasting as 'Bight Star 3') alongside an unregistered "
                        "barge with hoses connected. Vessel left before a patrol boat arrived.",
         "lat": 5.12, "lon": 4.95, "place": "9 nm off Forcados", "occurred_at": "2026-07-14T03:20:00Z", "status": "under_investigation",
         "visibility": "shared", "agency": "NN", "vessels": [[by["MT Gulf Voyager"], "suspect"]]},
        {"type": "sts_transfer", "title": "STS transfer without port notification",
         "description": "MV Sea Harmony reported alongside a small tanker at night, AIS off for 5 hours. No notification to the port.",
         "lat": 4.25, "lon": 5.95, "place": "Bonny approaches", "occurred_at": "2026-09-02T22:40:00Z", "status": "verified",
         "visibility": "shared", "agency": "NIMASA", "vessels": [[by["MV Sea Harmony"], "suspect"]]},
        {"type": "piracy_attack", "title": "Armed skiff fired on MV Calabar Dawn",
         "description": "Five armed persons in a skiff fired at the bridge. Master increased speed and the skiff broke off.",
         "lat": 3.95, "lon": 7.05, "place": "Bonny fairway", "occurred_at": "2026-03-18T04:05:00Z", "status": "closed",
         "visibility": "shared", "agency": "NN", "vessels": [[by["MV Calabar Dawn"], "victim"]]},
        {"type": "illegal_fishing", "title": "Trawler fishing outside licence area",
         "description": "FV Ocean Hope observed trawling inside the Benin EEZ with a Ghana-only licence. Warned and escorted out.",
         "lat": 5.05, "lon": 1.95, "place": "Benin offshore", "occurred_at": "2026-09-21T10:15:00Z", "status": "closed",
         "visibility": "shared", "agency": "NIMASA", "vessels": [[by["FV Ocean Hope"], "suspect"]]},
        {"type": "suspicious_approach", "title": "Fast skiff without lights near the Bonny fairway buoy",
         "description": "Fishing vessel reported a fast skiff without lights near the Bonny fairway buoy (from the radio log).",
         "lat": 3.9, "lon": 6.9, "place": "Bonny fairway buoy", "occurred_at": "2026-11-03T21:10:00Z", "status": "reported",
         "visibility": "shared", "agency": "NIMASA", "vessels": []},
        {"type": "boarding", "title": "Boarding attempt on FV Red Lantern's sister boat",
         "description": "Fishermen reported armed men in a canoe demanding fuel near Bioko; FV Red Lantern was in company.",
         "lat": 3.47, "lon": 8.8, "place": "Bioko channel", "occurred_at": "2026-10-12T18:30:00Z", "status": "verified",
         "visibility": "agency", "agency": "MPOL", "vessels": [[by["FV Red Lantern"], "witness"]]},
    ]
    fixed = incs[n_random:]

    def clash(a, b):
        ta = dt.datetime.fromisoformat(a["occurred_at"][:-1] + "+00:00")
        tb = dt.datetime.fromisoformat(b["occurred_at"][:-1] + "+00:00")
        return (family(a["type"]) == family(b["type"]) and abs((ta - tb).total_seconds()) < 8 * 3600
                and bd.haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) < 15)
    incs = [i for i in incs[:n_random] if not any(clash(i, f) for f in fixed)] + fixed
    incs.sort(key=lambda x: x["occurred_at"])
    return incs


def build_agency_bulk(rng, vessels, incs):
    """Bulk SIMULATED submissions from every agency for the last 3 days (sightings, patrol reports, radar contacts,
    watchlists, incident batches). Positions come from the vessels' own AIS tracks, so matching works."""
    t0 = NOW - dt.timedelta(hours=12)
    original = {v["id"] for v in vessels[:47]}
    movers = [v for v in vessels if v["type"] != "fishing" or rng.random() < 0.4]

    def at(v):
        idx = [i for i, p in enumerate(v["pts"]) if p]
        i = rng.choice(idx)
        p = v["pts"][i]
        t = t0 + dt.timedelta(minutes=20 * i + rng.randrange(0, 15))
        return round(p[0] + rng.gauss(0, 0.002), 4), round(p[1] + rng.gauss(0, 0.002), 4), t.strftime("%Y-%m-%dT%H:%M:00Z"), p

    def sighting(v, ref, unit):
        la, lo, t, p = at(v)
        return {"ref": ref, "observed_at": t, "lat": la, "lon": lo, "mmsi": v["id"], "vessel_name": v["name"], "speed_kn": p[2],
                "course": p[3], "description": f"{unit}: {v['type']} vessel identified visually, {rng.choice(['normal transit', 'crew on deck', 'no anomalies', 'answered on VHF 16', 'flag confirmed'])}."}

    batches = []
    picks = rng.sample(movers, 70)
    batches.append({"agency": "NN", "kind": "sightings", "channel": "api", "received_at": "2026-11-04T07:45:00Z",
                    "records": [sighting(v, f"NN-SGT-{1000 + n}", "Patrol craft") for n, v in enumerate(picks[:35])]})
    batches.append({"agency": "NAF", "kind": "sightings", "channel": "api", "received_at": "2026-11-04T07:50:00Z",
                    "records": [sighting(v, f"NAF-MPA-{500 + n}", "Maritime patrol aircraft") for n, v in enumerate(picks[35:])]})
    skiffs = []
    for n in range(10):                                   # unidentified small craft seen from the air in risk zones
        while True:
            la, lo = rng.uniform(2.4, 4.2), rng.uniform(5.9, 9.3)
            if ext.sea(lo, la):
                break
        t = t0 + dt.timedelta(minutes=rng.randrange(0, 720))
        skiffs.append({"ref": f"NAF-MPA-{600 + n}", "observed_at": t.strftime("%Y-%m-%dT%H:%M:00Z"), "lat": round(la, 4), "lon": round(lo, 4),
                       "length_m": rng.randrange(8, 16), "speed_kn": round(rng.uniform(12, 28), 1),
                       "description": rng.choice(["Fast skiff, several persons on board, no AIS", "Two outboard skiffs heading south",
                                                  "Wooden boat with drums on deck", "Speedboat circling, turned away from the aircraft"])})
    batches.append({"agency": "NAF", "kind": "sightings", "channel": "api", "received_at": "2026-11-04T07:55:00Z", "records": skiffs})
    reports = []
    for n, v in enumerate(rng.sample(movers, 12)):
        la, lo, t, _ = at(v)
        reports.append({"ref": f"NN-PR-{200 + n}", "observed_at": t, "lat": la, "lon": lo, "mmsi": v["id"], "unit": f"Patrol craft P-{rng.randrange(10, 40)}",
                        "description": f"Boarded and inspected {v['name']}: papers in order, {rng.randrange(8, 30)} crew."})
    batches.append({"agency": "NN", "kind": "patrol_reports", "channel": "api", "received_at": "2026-11-04T07:58:00Z", "records": reports})
    radar = []
    stations = [("Lagos coastal radar (trial)", 6.4, 3.4), ("Bonny coastal radar (trial)", 4.35, 7.1), ("Calabar coastal radar (trial)", 4.6, 8.3)]
    for v in vessels:
        p = v["pts"][29]
        if not p:
            continue
        for name, sla, slo in stations:
            if bd.haversine_km(p[0], p[1], sla, slo) < 45 and rng.random() < 0.6:
                radar.append({"ref": f"CR-{91000 + len(radar)}", "observed_at": "2026-11-04T05:45:00Z", "lat": round(p[0] + rng.gauss(0, 0.001), 4),
                              "lon": round(p[1] + rng.gauss(0, 0.001), 4), "mmsi": v["id"], "length_m": round(v["length_m"] * rng.uniform(0.9, 1.1)),
                              "speed_kn": p[2], "course": p[3], "confidence": round(rng.uniform(0.75, 0.95), 2), "sensor": name})
                break
    batches.append({"agency": "NIMASA", "kind": "radar_contacts", "channel": "api", "received_at": "2026-11-04T06:10:00Z", "records": radar})
    candidates = [v for v in vessels if v["id"] not in original and v["type"] in ("cargo", "tanker")]
    reasons = {"NCS": ["Under investigation for undeclared fuel cargo", "Customs alert: suspected smuggling of refined products",
                       "Cargo manifest discrepancies at the last port call"],
               "NIMASA": ["Detained in 2026 for safety deficiencies (simulated)", "Outstanding levies and sailing without clearance"],
               "MPOL": ["Crew member wanted for questioning", "Linked to theft of ship's stores in Lagos anchorage"]}
    for ag, n_entries in (("NCS", 8), ("NIMASA", 4), ("MPOL", 4)):
        recs = []
        for n, v in enumerate(rng.sample(candidates, n_entries)):
            recs.append({"ref": f"{ag}-WL-{300 + n}", "mmsi": v["id"], "name": v["name"], "level": rng.choice(["monitor", "monitor", "suspect"]),
                         "reason": rng.choice(reasons[ag]) + " (simulated).", "valid_until": "2027-12-31"})
        batches.append({"agency": ag, "kind": "watchlist", "channel": "csv", "filename": f"{ag.lower()}_watchlist_q4.csv",
                        "received_at": "2026-11-02T10:00:00Z", "records": recs})
    regional = []
    tries = 0
    while len(regional) < 14 and tries < 500:
        tries += 1
        kind = rng.choice(["piracy_attack", "boarding", "suspicious_approach", "armed_robbery", "illegal_fishing", "smuggling"])
        la, lo = pick_position(rng, TYPES[kind][1], bd.GEO)
        t = NOW - dt.timedelta(days=rng.uniform(1, 40))
        cand = {"type": kind, "occurred_at": t.strftime("%Y-%m-%dT%H:%M:00Z"), "lat": la, "lon": lo}
        if any(_fam(c["type"]) == _fam(kind) and abs((dt.datetime.fromisoformat(c["occurred_at"][:-1] + "+00:00") - t).total_seconds()) < 8 * 3600
               and bd.haversine_km(la, lo, c["lat"], c["lon"]) < 15 for c in incs + regional):
            continue
        title = rng.choice(TITLES[kind]).format(v=rng.choice(["a tanker", "a cargo ship", "a fishing vessel"]))
        regional.append({**cand, "ref": f"MMCC-E-{7000 + len(regional)}", "title": title[0].upper() + title[1:], "description": DESCR[kind],
                         "place": nearest_place(bd.GEO, la, lo)})
    batches.append({"agency": "MMCC", "kind": "incidents", "channel": "api", "received_at": "2026-11-04T06:30:00Z",
                    "records": [{"observed_at": r["occurred_at"], **{k: r[k] for k in ("ref", "type", "title", "lat", "lon", "place", "description")}}
                                for r in regional]})
    return batches


def _fam(t):
    return "attack" if t in ("piracy_attack", "boarding", "armed_robbery", "kidnapping") else t


def main():
    rng = random.Random(SEED)
    with open(os.path.join(bd.OUT, "ais.json"), encoding="utf-8") as f:
        vessels = json.load(f)["vessels"]
    geo = bd.GEO
    os.makedirs(OUT, exist_ok=True)
    reg = build_registry(rng, vessels)
    incs = build_incidents(rng, vessels, geo)
    bulk = build_agency_bulk(random.Random(SEED + 7), vessels, incs)
    note = "SIMULATED for the GulfEye demo. Invented companies, numbers and events. Not intelligence."
    with open(os.path.join(OUT, "registry.json"), "w", encoding="utf-8") as f:
        json.dump({"note": note, "records": reg}, f, indent=1, ensure_ascii=False)
    with open(os.path.join(OUT, "incidents_history.json"), "w", encoding="utf-8") as f:
        json.dump({"note": note, "incidents": incs}, f, indent=1, ensure_ascii=False)
    with open(os.path.join(OUT, "agency_submissions_bulk.json"), "w", encoding="utf-8") as f:
        json.dump({"note": note, "batches": bulk}, f, indent=1, ensure_ascii=False)
    print(f"registry: {len(reg)} records · incidents: {len(incs)} · agency bulk: "
          f"{sum(len(b['records']) for b in bulk)} records in {len(bulk)} batches")


if __name__ == "__main__":
    main()
