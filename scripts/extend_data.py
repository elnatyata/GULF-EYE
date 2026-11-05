"""Extension of the simulated data to the whole Gulf of Guinea (used by build_data.py).

The original 47 vessels and 50 detections are generated exactly as before. This module:
  * nudges the few original points that fall on the real coastline out to sea (and moves their radar detection by
    the same offset, so AIS/radar matching is unchanged);
  * adds about 230 vessels on the real coastline (anchorages from Abidjan to Port-Gentil, all shipping lanes,
    ferries, fishing fleets, offshore supply, tugs) with a few new behaviour scenarios;
  * adds their Sentinel-1 detections and some new dark and low-confidence contacts, numbered from D-051.
Everything is SIMULATED and deterministic (fixed seeds).
"""
import json
import math
import os

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import nearest_points
from shapely.prepared import prep

import build_data as bd

GEO = bd.GEO
_LAND = MultiPolygon([Polygon(GEO["land"])] + [Polygon(i["poly"]) for i in GEO["islands"]])
_PLAND = prep(_LAND)
N = bd.HOURS * 60 // bd.STEP_MIN + 1

FIRST = ["Atlantic", "Bight", "Coral", "Delta", "Eko", "Gulf", "Harbour", "Ivory", "Kribi", "Lagoon", "Meridian", "Niger",
         "Ocean", "Palm", "Royal", "Sahel", "Tropic", "Volta", "West", "Zenith", "Cape", "Benue", "Akwa", "Ogun", "Comoe",
         "Ogooue", "Sanaga", "Wouri", "Bonga", "Mono", "Ada", "Keta", "Elmina", "Sassandra", "Bandama", "Corisco"]
SECOND = ["Star", "Pride", "Spirit", "Trader", "Express", "Pearl", "Runner", "Dawn", "Light", "Queen", "Wind", "Bay", "Hope",
          "Swift", "Falcon", "Glory", "Venture", "Horizon", "Carrier", "Breeze", "Crest", "Harvest", "Unity", "Voyager",
          "Navigator", "Endeavour", "Promise", "Legacy", "Mariner", "Sun"]
FLAGS = bd.FLAGS + [("Côte d'Ivoire", 619), ("Gabon", 626), ("Equatorial Guinea", 631), ("São Tomé and Príncipe", 668),
                    ("Greece", 240), ("Cyprus", 209), ("Norway", 257), ("Hong Kong", 477), ("Netherlands", 244)]
TYPES = {**bd.TYPES, "passenger": ("MV", (40, 95))}


def sea(lon, lat):
    a = GEO["bbox"]
    return a["lon0"] < lon < a["lon1"] and a["lat0"] < lat < a["lat1"] and not _PLAND.contains(Point(lon, lat))


def _push(lon, lat, km=2.5):
    """Nearest point at sea, about `km` beyond the coastline."""
    p = Point(lon, lat)
    q = nearest_points(_LAND.boundary, p)[0]
    dx, dy = q.x - lon, q.y - lat
    d = math.hypot(dx, dy) or 1e-6
    step = km / bd.KM_PER_DEG
    for k in range(1, 30):
        nx, ny = q.x + dx / d * step * k / 2, q.y + dy / d * step * k / 2
        if sea(nx, ny):
            return nx, ny
    return q.x, q.y


def nudge_to_sea(vessels, dets, sar_index):
    """Move original points that the real coastline puts on land; keep each radar detection with its vessel."""
    delta = {}
    for v in vessels:
        for i, p in enumerate(v["pts"]):
            if p and not sea(p[1], p[0]):
                lon, lat = _push(p[1], p[0])
                if i == sar_index:
                    delta[v["id"]] = (lat - p[0], lon - p[1])
                p[0], p[1] = round(lat, 4), round(lon, 4)
    for d in dets:
        if d["_src"] in delta:
            dla, dlo = delta[d["_src"]]
            d["lat"], d["lon"] = round(d["lat"] + dla, 4), round(d["lon"] + dlo, 4)
        elif not sea(d["lon"], d["lat"]):
            lon, lat = _push(d["lon"], d["lat"])
            d["lat"], d["lon"] = round(lat, 4), round(lon, 4)


# ------------------------------------------------------------------ tracks on the real coastline
def _start(rng, lat, lon, spread):
    for _ in range(400):
        la, lo = lat + rng.gauss(0, spread), lon + rng.gauss(0, spread)
        if sea(lo, la):
            return la, lo
    return _push(lon, lat)[::-1]


def track(rng, kind, spec):
    pts = []
    if kind == "route":
        route, p0, sog, direction = spec["route"], spec["p0"], spec["sog"], spec["dir"]
        total = bd.route_geometry(route)[1]
        prof = spec.get("profile")              # optional speed profile: list of speeds per step (sprint scenario)
        d = p0 * total
        for i in range(N):
            v = prof[i] if prof else sog
            lat, lon, cog = bd.along(route, d)
            pts.append([round(lat, 4), round(lon, 4), round(max(0, v + rng.gauss(0, 0.4)), 1), round(cog) % 360])
            d += direction * v * 1.852 * bd.STEP_MIN / 60
    elif kind == "anchored":
        lat0, lon0 = spec["pos"]
        cog = rng.randrange(360)
        for _ in range(N):
            pts.append([round(lat0 + rng.gauss(0, 0.0004), 4), round(lon0 + rng.gauss(0, 0.0004), 4),
                        round(abs(rng.gauss(0.1, 0.1)), 1), cog])
    elif kind == "walk":
        lat, lon = spec["pos"]
        brg = rng.uniform(0, 360)
        box = spec["box"]
        for _ in range(N):
            sog = max(0.2, rng.gauss(spec["sog"], spec.get("sd", 0.8)))
            brg = (brg + rng.gauss(0, spec.get("turn", 25))) % 360
            nlat, nlon = bd.move(lat, lon, brg, sog * 1.852 * bd.STEP_MIN / 60)
            if not (box[0] <= nlon <= box[1] and box[2] <= nlat <= box[3]) or not sea(nlon, nlat):
                brg = (brg + 180) % 360
                nlat, nlon = bd.move(lat, lon, brg, sog * 1.852 * bd.STEP_MIN / 60)
                if not sea(nlon, nlat):
                    nlat, nlon = lat, lon
            lat, lon = nlat, nlon
            pts.append([round(lat, 4), round(lon, 4), round(sog, 1), round(brg) % 360])
    return pts


def extend_vessels(rng, original):
    used_names = {v["name"] for v in original}
    used_ids = {v["id"] for v in original}
    out = []

    def add(vtype, kind, spec, name=None, flag=None, length=None, dest=None, scenario=None):
        prefix, (lmin, lmax) = TYPES[vtype]
        nm = name
        while nm is None or nm in used_names:
            nm = f"{prefix} {rng.choice(FIRST)} {rng.choice(SECOND)}"
        used_names.add(nm)
        fl = flag or rng.choice(FLAGS)
        vid = None
        while vid is None or vid in used_ids:
            vid = f"{fl[1]}{rng.randrange(100000, 999999)}"
        used_ids.add(vid)
        v = {"id": vid, "name": nm, "flag": fl[0], "type": vtype, "length_m": length or rng.randrange(lmin, lmax),
             "destination": dest, "pts": track(rng, kind, spec)}
        if scenario:
            v["scenario"] = scenario
        out.append(v)
        return v

    lanes = {lane["id"]: [tuple(p) for p in lane["pts"]] for lane in GEO["lanes"]}
    # anchorages and roads (lat, lon, spread, count, destination)
    for lat, lon, spread, count, dest in [(5.12, -4.02, 0.05, 10, "ABIDJAN"), (4.7, -6.7, 0.04, 3, "SAN-PEDRO"),
                                          (4.82, -1.7, 0.04, 4, "TAKORADI"), (5.55, 0.05, 0.05, 8, "TEMA"),
                                          (5.85, 1.35, 0.08, 10, "LOME"), (6.25, 2.45, 0.04, 6, "COTONOU"),
                                          (6.12, 3.65, 0.08, 12, "LAGOS"), (3.88, 9.38, 0.04, 6, "DOUALA"),
                                          (0.42, 9.2, 0.04, 5, "LIBREVILLE"), (-0.55, 8.62, 0.04, 4, "PORT-GENTIL")]:
        for _ in range(count):
            add(rng.choice(["cargo", "cargo", "tanker"]), "anchored", {"pos": _start(rng, lat, lon, spread)}, dest=dest)
    # shipping lanes
    for key, count, dests in [("coast_west", 8, ("TEMA", "LAGOS")), ("abidjan_tema", 12, ("ABIDJAN", "TEMA", "LOME")),
                              ("sanpedro_abidjan", 6, ("SAN-PEDRO", "ABIDJAN")), ("lagos_bonny", 10, ("BONNY", "LAGOS")),
                              ("bonny_douala", 8, ("DOUALA", "BONNY")), ("douala_gabon", 10, ("LIBREVILLE", "DOUALA", "PORT-GENTIL")),
                              ("offshore_ew", 16, ("LUANDA", "ABIDJAN", "LAS PALMAS")), ("southbound", 10, ("LUANDA", "POINTE-NOIRE"))]:
        for _ in range(count):
            add(rng.choice(["cargo", "cargo", "tanker"]), "route",
                {"route": lanes[key], "p0": rng.random(), "sog": rng.uniform(8, 14), "dir": rng.choice([1, -1])}, dest=rng.choice(dests))
    # passenger ferries
    for route, count, dest in [([(8.35, 4.45), (8.6, 4.0), (8.76, 3.83)], 2, "MALABO"), ([(9.35, 3.85), (9.0, 3.85), (8.84, 3.8)], 2, "MALABO"),
                               ([(6.8, 0.42), (7.1, 1.0), (7.38, 1.55)], 2, "PRINCIPE"), ([(9.25, 0.35), (9.0, -0.1), (8.72, -0.55)], 2, "PORT-GENTIL"),
                               ([(3.35, 6.32), (2.6, 6.15), (2.45, 6.28)], 2, "COTONOU")]:
        for _ in range(count):
            add("passenger", "route", {"route": route, "p0": rng.random(), "sog": rng.uniform(12, 18), "dir": rng.choice([1, -1])}, dest=dest)
    # fishing fleets (lon0, lon1, lat0, lat1, count)
    for box, count in [((-5.4, 0.5, 4.1, 4.95), 22), ((1.0, 3.0, 4.4, 5.5), 6), ((9.1, 9.7, 2.3, 3.4), 5),
                       ((8.4, 9.2, -0.4, 0.3), 4), ((6.3, 7.2, -0.2, 0.9), 4), ((-7.6, -6.0, 4.0, 4.5), 4)]:
        for _ in range(count):
            c = _start(rng, (box[2] + box[3]) / 2, (box[0] + box[1]) / 2, (box[1] - box[0]) / 4)
            add("fishing", "walk", {"pos": c, "box": box, "sog": rng.uniform(2.5, 4.5)}, dest="FISHING")
    # offshore supply around all platforms
    for p in GEO["platforms"]:
        for _ in range(3 if p["id"] in ("P6", "P7", "P8", "P9", "P10") else 1):
            add("supply", "walk", {"pos": _start(rng, p["lat"] + 0.02, p["lon"] + 0.02, 0.01),
                                   "box": (p["lon"] - .15, p["lon"] + .15, p["lat"] - .15, p["lat"] + .15), "sog": 2.0}, dest=p["name"].upper())
    # harbour tugs
    for lat, lon, dest in [(5.2, -4.0, "ABIDJAN"), (5.58, 0.05, "TEMA"), (6.08, 1.3, "LOME"), (6.3, 3.45, "LAGOS"),
                           (4.3, 7.05, "BONNY"), (3.95, 9.4, "DOUALA"), (0.45, 9.3, "LIBREVILLE"), (6.3, 2.45, "COTONOU")]:
        c = _start(rng, lat, lon, 0.03)
        add("tug", "walk", {"pos": c, "box": (c[1] - .12, c[1] + .12, c[0] - .1, c[0] + .1), "sog": 3.0}, dest=dest)

    # ---- new behaviour scenarios (all invented) ----
    # rendezvous: a tanker drifts in the deep offshore zone, a second tanker joins it and stays alongside
    a = add("tanker", "walk", {"pos": (2.45, 5.6), "box": (5.55, 5.65, 2.4, 2.5), "sog": 0.4, "sd": 0.15, "turn": 60},
            name="MT Corisco Venture", flag=("Togo", 671), length=182, dest="UNKNOWN", scenario="rendezvous-a")
    b = add("tanker", "route", {"route": [(5.3, 2.32), (a["pts"][-1][1] + 0.002, a["pts"][-1][0] + 0.002)], "p0": 0.0, "sog": 7,
                                "dir": 1, "profile": [7.0] * 12 + [0.3] * (N - 12)},
            name="MT Keta Harvest", flag=("Comoros", 616), length=120, dest="LOME", scenario="rendezvous-b")
    for i in range(14, N):                 # stay beside the first tanker
        la, lo = a["pts"][i][0], a["pts"][i][1]
        b["pts"][i] = [round(la + 0.002, 4), round(lo + 0.0018, 4), round(abs(rng.gauss(0.3, 0.1)), 1), b["pts"][i][3]]
    # loitering cargo ship near São Tomé
    add("cargo", "walk", {"pos": (1.05, 6.9), "box": (6.85, 6.95, 1.0, 1.1), "sog": 1.2, "sd": 0.3, "turn": 70},
        name="MV Ogooue Navigator", flag=("Gabon", 626), length=96, dest="SAO TOME", scenario="loiter")
    # sprint: a supply boat that suddenly accelerates in the Bonny approaches
    add("supply", "route", {"route": [(6.4, 3.6), (7.4, 3.75)], "p0": 0.05, "sog": 6, "dir": 1, "profile": [6.0] * 24 + [21.0] * (N - 24)},
        name="OSV Wouri Crest", flag=("Nigeria", 657), length=58, dest="BONNY", scenario="sprint")
    # off-lane transit far from the usual routes
    add("cargo", "route", {"route": [(-3.5, 1.2), (1.5, 0.3)], "p0": 0.1, "sog": 11, "dir": 1},
        name="MV Elmina Promise", flag=("Panama", 351), length=140, dest="LUANDA", scenario="off-lane")
    # AIS gaps outside the radar scene
    gaps = [add("tanker", "route", {"route": [(1.3, 5.85), (0.6, 4.6)], "p0": 0.0, "sog": 8, "dir": 1},
                name="MT Cape Sable", flag=("Cook Islands", 518), length=150, dest="UNKNOWN", scenario="gap-outside"),
            add("fishing", "walk", {"pos": (4.4, -2.2), "box": (-2.6, -1.8, 4.2, 4.6), "sog": 3.0},
                name="FV Golden Net", flag=("Ghana", 627), length=30, dest="FISHING", scenario="gap-outside")]
    for v, last in zip(gaps, (12, 20)):
        for i in range(last + 1, N):
            v["pts"][i] = None
    return out


def extend_detections(rng, extra, first_id, sar_index):
    dets = []
    for v in extra:
        p = v["pts"][sar_index]
        if p is None or not (bd.in_footprint(p[1], p[0]) and sea(p[1], p[0])):
            continue
        if v["length_m"] < 25 and rng.random() < 0.35:
            continue
        r = min(0.35, abs(rng.gauss(0, 0.11)))
        nlat, nlon = bd.move(p[0], p[1], rng.uniform(0, 360), r)
        conf = min(0.98, 0.58 + v["length_m"] / 450 + rng.uniform(-.04, .06))
        dets.append({"lat": round(nlat, 4), "lon": round(nlon, 4), "length_m": int(v["length_m"] * rng.uniform(0.9, 1.12)),
                     "confidence": round(conf, 2), "_src": v["id"]})
    for p in GEO["platforms"][5:]:
        if bd.in_footprint(p["lon"], p["lat"]):
            nlat, nlon = bd.move(p["lat"], p["lon"], rng.uniform(0, 360), rng.uniform(0.05, 0.2))
            dets.append({"lat": round(nlat, 4), "lon": round(nlon, 4), "length_m": rng.randrange(70, 110), "confidence": 0.95,
                         "_src": "platform-" + p["id"]})
    dark = [  # deep-offshore group, an unlit craft near Lagos anchorage, a skiff in the Calabar estuary approaches
        (2.62, 4.88, 26, 0.84), (2.55, 5.02, 12, 0.79), (2.58, 4.96, 10, 0.72),
        (6.02, 3.25, 15, 0.77), (4.25, 8.28, 13, 0.81)]
    for lat, lon, length, conf in dark:
        assert sea(lon, lat) and bd.in_footprint(lon, lat), (lat, lon)
        dets.append({"lat": lat, "lon": lon, "length_m": length, "confidence": conf, "_src": "dark-new"})
    for _ in range(6):                     # sea clutter: low-confidence returns
        for _try in range(100):
            lat, lon = rng.uniform(2.3, 6.5), rng.uniform(2.3, 9.4)
            if sea(lon, lat):
                break
        dets.append({"lat": round(lat, 4), "lon": round(lon, 4), "length_m": rng.randrange(5, 12),
                     "confidence": round(rng.uniform(0.3, 0.46), 2), "_src": "clutter"})
    dets.sort(key=lambda d: (d["lon"], d["lat"]))
    for i, d in enumerate(dets, first_id):
        d["id"] = f"D-{i:03d}"
    return dets


def extra_history(rng):
    ref = {"Z5": 1.4, "Z6": 0.9, "Z7": 2.4, "Z8": 1.1}
    return {z: {"passes": [max(0, round(rng.gauss(m, 0.9))) for _ in range(12)], "reference": m} for z, m in ref.items()}
