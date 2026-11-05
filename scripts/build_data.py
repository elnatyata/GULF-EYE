#!/usr/bin/env python3
"""Build the demo data files for the Gulf of Guinea dark-vessel dashboard.

Usage (from the gulf-watch folder):
    python3 scripts/build_data.py
    python3 scripts/build_data.py --refresh-forecast     # fetch a new Open-Meteo snapshot
    python3 scripts/build_data.py --ais web/data/ais_live.json --sar web/data/sar_live.json
                                                         # use real files from fetch_ais.py / detect_sar.py

Writes (all in web/data/):
    ais.json       AIS baseline: vessel tracks of the last 12 hours (Step 1 of the pipeline)
    sar.json       SAR scene footprint + vessel detections (Step 2 and the detector output of Step 3)
    weather.json   10-day wind and wave forecast, sea-state history

Hand-written files read by this script: geo.json (coast, zones, ports, platforms), assets.json, assumptions.json.

By default the AIS tracks and SAR detections are SYNTHETIC but built to be realistic: matched vessels, a few
platforms (bright static returns), vessels that switched AIS off, and dark vessels in risk zones. The matching
itself ("fusion") is NOT done here: it runs in the browser (web/js/model.js) so the rules stay visible.
"""
import argparse
import datetime as dt
import json
import math
import os
import random
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "web", "data")

NOW = dt.datetime(2026, 11, 4, 8, 0, tzinfo=dt.timezone.utc)      # demo "now"
SAR_TIME = dt.datetime(2026, 11, 4, 5, 40, tzinfo=dt.timezone.utc)  # Sentinel-1 pass
STEP_MIN = 20
HOURS = 12
SEED = 20261104
KM_PER_DEG = 111.32


# ---------------------------------------------------------------- geometry helpers
def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def move(lat, lon, brg, km):
    b = math.radians(brg)
    return (lat + km * math.cos(b) / KM_PER_DEG,
            lon + km * math.sin(b) / (KM_PER_DEG * math.cos(math.radians(lat))))


def in_poly(lon, lat, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8") as f:
        return json.load(f)


GEO = load("geo.json")                       # current geography (real coastline, whole Gulf of Guinea)
with open(os.path.join(HERE, "legacy_geo.json"), encoding="utf-8") as _f:
    LEGACY = json.load(_f)                   # the original schematic geography: the original 47 vessels are generated
                                             # with it so their ids, names and stories stay exactly the same


def at_sea(lon, lat):
    """Sea test on the ORIGINAL schematic coastline (original generator only; see extend_data.py for the real one)."""
    if in_poly(lon, lat, LEGACY["land"]):
        return False
    return not any(in_poly(lon, lat, i["poly"]) for i in LEGACY["islands"])


def in_footprint(lon, lat):
    return in_poly(lon, lat, GEO["footprint"]["poly"])


def zone_of(lon, lat):
    for z in GEO["zones"]:
        if in_poly(lon, lat, z["poly"]):
            return z["id"]
    return None


# ---------------------------------------------------------------- vessel tracks
ROUTES = {
    "coast_west": [(3.3, 6.0), (2.4, 5.85), (1.4, 5.65), (0.8, 5.35)],
    "lagos_bonny": [(3.5, 5.95), (4.5, 5.6), (5.3, 4.9), (6.2, 4.15), (7.0, 3.85)],
    "bonny_douala": [(7.1, 3.9), (8.0, 3.95), (8.8, 3.98), (9.4, 3.55)],
    "offshore_ew": [(1.0, 2.6), (3.5, 2.55), (6.0, 2.5), (9.3, 2.4)],
}
NAMES = ["Coral Star", "Atlas Bay", "Ocean Pride", "Delta Spirit", "Gulf Trader", "Niger Pearl", "Lagos Express",
         "Cape Meridian", "Blue Horizon", "Sahel Wind", "Harmattan", "Ivory Coast", "Palm Bay", "Mariner One",
         "Eko Carrier", "Bight Runner", "Calabar Dawn", "Equator Light", "Orion Pass", "Volta Queen", "Savanna",
         "Kribi Star", "Benin Bright", "Amber Tide", "Neptune Gate", "Coastal Hope", "Bonga Supply", "Ocean Swift",
         "Tide Runner", "Trident", "Silver Wave", "Port Royal", "Zephyr", "Anchor Point", "Sea Falcon", "Mono Bay",
         "Dawn Trader", "Cobalt", "Red Lantern", "Westerly", "Horizon Gold", "Sapele", "Akwa Star", "Ibom Pride",
         "Warri Belle", "Lekki Tide", "Badagry Sun", "Eastern Light", "Obalende", "Ada Foah"]
FLAGS = [("Nigeria", 657), ("Ghana", 627), ("Benin", 610), ("Togo", 671), ("Cameroon", 613), ("Liberia", 636),
         ("Panama", 351), ("Marshall Is.", 538), ("Malta", 256), ("Singapore", 563)]
TYPES = {  # type: (prefix, length range)
    "cargo": ("MV", (90, 190)), "tanker": ("MT", (150, 250)), "fishing": ("FV", (18, 40)),
    "supply": ("OSV", (55, 80)), "tug": ("Tug", (24, 35)),
}


def route_geometry(pts):
    segs, total = [], 0.0
    for (lo1, la1), (lo2, la2) in zip(pts, pts[1:]):
        d = haversine_km(la1, lo1, la2, lo2)
        segs.append((total, d, (lo1, la1), (lo2, la2)))
        total += d
    return segs, total


def along(pts, dist_km):
    segs, total = route_geometry(pts)
    period = 2 * total
    d = dist_km % period
    reverse = d > total
    if reverse:
        d = period - d
    for start, length, (lo1, la1), (lo2, la2) in segs:
        if d <= start + length or (start, length) == (segs[-1][0], segs[-1][1]):
            f = min(1.0, max(0.0, (d - start) / length))
            lon, lat = lo1 + (lo2 - lo1) * f, la1 + (la2 - la1) * f
            brg = bearing(la1, lo1, la2, lo2)
            return lat, lon, (brg + 180) % 360 if reverse else brg
    return pts[-1][1], pts[-1][0], 0.0


def make_track(rng, kind, spec):
    n = HOURS * 60 // STEP_MIN + 1
    pts = []
    if kind == "route":
        route, p0, sog, direction = spec["route"], spec["p0"], spec["sog"], spec["dir"]
        total = route_geometry(route)[1]
        for i in range(n):
            d = p0 * total + direction * sog * 1.852 * (i * STEP_MIN / 60)
            lat, lon, cog = along(route, d)
            pts.append([round(lat, 4), round(lon, 4), round(max(0, sog + rng.gauss(0, 0.4)), 1), round(cog) % 360])
    elif kind == "anchored":
        lat0, lon0 = spec["pos"]
        cog = rng.randrange(360)
        for _ in range(n):
            pts.append([round(lat0 + rng.gauss(0, 0.0004), 4), round(lon0 + rng.gauss(0, 0.0004), 4),
                        round(abs(rng.gauss(0.1, 0.1)), 1), cog])
    elif kind == "walk":
        lat, lon = spec["pos"]
        brg = rng.uniform(0, 360)
        box = spec["box"]
        for _ in range(n):
            sog = max(0.2, rng.gauss(spec["sog"], 0.8))
            brg = (brg + rng.gauss(0, 25)) % 360
            nlat, nlon = move(lat, lon, brg, sog * 1.852 * STEP_MIN / 60)
            if not (box[0] <= nlon <= box[1] and box[2] <= nlat <= box[3]) or not at_sea(nlon, nlat):
                brg = (brg + 180) % 360
                nlat, nlon = move(lat, lon, brg, sog * 1.852 * STEP_MIN / 60)
            lat, lon = nlat, nlon
            pts.append([round(lat, 4), round(lon, 4), round(sog, 1), round(brg) % 360])
    return pts


def build_vessels(rng):
    vessels, used_names = [], set()

    def new_id(flag_mid):
        return f"{flag_mid}{rng.randrange(100000, 999999)}"

    def add(vtype, kind, spec, name=None, flag=None, length=None, dest=None):
        prefix, (lmin, lmax) = TYPES[vtype]
        nm = name
        while nm is None or nm in used_names:
            nm = f"{prefix} {rng.choice(NAMES)}"
        used_names.add(nm)
        fl = flag or rng.choice(FLAGS)
        track = make_track(rng, kind, spec)
        v = {"id": new_id(fl[1]), "name": nm, "flag": fl[0], "type": vtype,
             "length_m": length or rng.randrange(lmin, lmax), "destination": dest, "pts": track}
        vessels.append(v)
        return v

    # anchored in the Lagos anchorage and at Cotonou / Lomé roads
    for _ in range(6):
        add("cargo" if rng.random() < .5 else "tanker", "anchored",
            {"pos": (rng.uniform(5.75, 6.2), rng.uniform(3.15, 4.4))}, dest="LAGOS")
    for _ in range(2):
        add("cargo", "anchored", {"pos": (rng.uniform(5.85, 5.98), rng.uniform(1.2, 2.5))}, dest="COTONOU")
    sts = add("tanker", "anchored", {"pos": (5.9012, 3.8995)}, name="MT Atlantic Pearl", flag=("Liberia", 636),
              length=245, dest="LAGOS")
    sts["scenario"] = "sts-tanker"
    # lanes
    for key, count, dests in (("coast_west", 6, ("TEMA", "LAGOS")), ("lagos_bonny", 7, ("BONNY", "LAGOS")),
                              ("bonny_douala", 5, ("DOUALA", "BONNY")), ("offshore_ew", 6, ("LUANDA", "ABIDJAN"))):
        for _ in range(count):
            add(rng.choice(["cargo", "cargo", "tanker"]), "route",
                {"route": ROUTES[key], "p0": rng.random(), "sog": rng.uniform(8, 13), "dir": rng.choice([1, -1])},
                dest=rng.choice(dests))
    # supply vessels around the platforms, fishing boats on the grounds
    for p in LEGACY["platforms"][:4]:
        add("supply", "walk", {"pos": (p["lat"] + 0.02, p["lon"] + 0.02), "box": (p["lon"] - .15, p["lon"] + .15, p["lat"] - .15, p["lat"] + .15), "sog": 2.0},
            dest=p["name"].upper())
    for box in ((1.2, 2.9, 4.4, 5.5), (1.2, 2.9, 4.4, 5.5), (1.2, 2.9, 4.4, 5.5), (4.0, 5.4, 3.5, 4.4),
                (6.0, 7.6, 3.3, 4.2), (8.1, 9.4, 3.1, 3.8)):
        add("fishing", "walk", {"pos": ((box[2] + box[3]) / 2, (box[0] + box[1]) / 2), "box": box, "sog": 4.0}, dest="FISHING")
    add("tug", "walk", {"pos": (6.25, 3.5), "box": (3.2, 3.8, 6.1, 6.25), "sog": 3.0}, dest="LAGOS")

    # vessels that switched AIS off (pts are None after the gap)
    g1 = add("cargo", "route", {"route": [(5.0, 4.4), (7.0, 3.85)], "p0": 0.0, "sog": 9.0, "dir": 1},
             name="MV Sea Harmony", flag=("Sierra Leone", 667), length=62, dest="BONNY")
    g2 = add("tanker", "anchored", {"pos": (5.2, 4.6)}, name="MT Gulf Voyager", flag=("Panama", 351), length=190, dest="UNKNOWN")
    g3 = add("fishing", "walk", {"pos": (4.9, 1.6), "box": (1.3, 1.9, 4.6, 5.2), "sog": 3.0},
             name="FV Ocean Hope", flag=("Ghana", 627), length=32, dest="FISHING")
    for v, gap_idx, tag in ((g1, 10, "gap"), (g2, 5, "gap"), (g3, 14, "gap-outside")):  # last report index
        for i in range(gap_idx + 1, len(v["pts"])):
            v["pts"][i] = None
        v["scenario"] = tag
    return vessels


def pos_at(vessel, idx):
    return vessel["pts"][idx]


def build_detections(rng, vessels):
    sar_idx = int((SAR_TIME - (NOW - dt.timedelta(hours=HOURS))).total_seconds() // 60 // STEP_MIN)
    dets = []

    def det(lat, lon, length, conf, source):
        dets.append({"lat": round(lat, 4), "lon": round(lon, 4), "length_m": int(length),
                     "confidence": round(conf, 2), "_src": source})

    # 1. vessels that report AIS at the time of the pass
    for v in vessels:
        p = pos_at(v, sar_idx)
        if p is None:
            continue
        lat, lon = p[0], p[1]
        if not (in_footprint(lon, lat) and at_sea(lon, lat)):
            continue
        if v["length_m"] < 25 and rng.random() < 0.35:
            continue  # small boats are often missed
        r = min(0.35, abs(rng.gauss(0, 0.11)))
        nlat, nlon = move(lat, lon, rng.uniform(0, 360), r)
        conf = min(0.98, 0.58 + v["length_m"] / 450 + rng.uniform(-.04, .06))
        det(nlat, nlon, v["length_m"] * rng.uniform(0.9, 1.12), conf, v["id"])

    # 2. AIS-gap vessels: dead reckoning from the last report, plus a small drift
    for v in vessels:
        sc = v.get("scenario")
        if sc != "gap":
            continue
        last_i = max(i for i, p in enumerate(v["pts"]) if p)
        lat, lon, sog, cog = v["pts"][last_i]
        hours = (sar_idx - last_i) * STEP_MIN / 60
        plat, plon = move(lat, lon, cog, sog * 1.852 * hours)
        if v["name"] == "MV Sea Harmony":
            plat, plon = move(plat, plon, 200, 1.6)
        else:
            plat, plon = move(plat, plon, 70, 0.9)
        det(plat, plon, v["length_m"] * rng.uniform(.95, 1.08), 0.9, "gap-" + v["id"])

    # 3. known platforms: bright, static returns
    for p in LEGACY["platforms"]:
        nlat, nlon = move(p["lat"], p["lon"], rng.uniform(0, 360), rng.uniform(0.05, 0.2))
        det(nlat, nlon, rng.randrange(70, 110), 0.95, "platform-" + p["id"])

    # 4. dark vessels: no AIS, hand-placed so the story is the same in every run
    dark = [  # lat, lon, length, conf, label
        (3.84, 6.88, 14, 0.92, "Bonny skiff"), (3.74, 6.74, 11, 0.87, "Bonny skiff 2"), (3.62, 6.60, 40, 0.74, "mothership"),
        (3.52, 9.28, 18, 0.83, "Bioko channel"), (5.9031, 3.9021, 60, 0.96, "STS barge"),
        (5.15, 2.55, 22, 0.88, "fishing 1"), (4.95, 2.85, 28, 0.81, "fishing 2"),
        (4.70, 5.30, 9, 0.41, "low confidence"), (6.05, 4.05, 7, 0.38, "low confidence 2")]
    for lat, lon, length, conf, label in dark:
        assert at_sea(lon, lat) and in_footprint(lon, lat), label
        det(lat, lon, length, conf, "dark-" + label)

    dets.sort(key=lambda d: (d["lon"], d["lat"]))
    for i, d in enumerate(dets, 1):
        d["id"] = f"D-{i:03d}"
    return dets


def build_history(rng, zone_counts):
    ref = {"Z1": 2.2, "Z2": 1.2, "Z3": 0.8, "Z4": 1.5}
    hist = {}
    for z, mean in ref.items():
        weekly = [max(0, round(rng.gauss(mean, 0.9))) for _ in range(12)]
        hist[z] = {"passes": weekly, "reference": mean}
    return hist


# ---------------------------------------------------------------- weather
def synthetic_forecast(rng):
    codes = [2, 3, 80, 3, 61, 63, 80, 3, 2, 2]
    days = []
    for i in range(10):
        rough = i in (4, 5)
        wave = rng.uniform(2.5, 3.2) if rough else rng.uniform(0.9, 1.9)
        wind = rng.uniform(36, 46) if rough else rng.uniform(10, 26)
        rain = rng.uniform(6, 22) if rough else rng.choice([0, 0, 0.4, 2.5, 6.5])
        days.append({
            "date": (NOW + dt.timedelta(days=i)).strftime("%Y-%m-%d"), "weather_code": codes[i],
            "tmax": round(rng.uniform(29, 32), 1), "tmin": round(rng.uniform(24, 26), 1),
            "precip_mm": round(rain, 1), "precip_prob": int(min(95, 20 + rain * 4)) if rain else int(rng.uniform(5, 25)),
            "wind_max_kmh": round(wind, 1), "wave_height_max_m": round(wave, 1)})
    return {"source": "Synthetic sea-state forecast (offline demo). Run with --refresh-forecast for Open-Meteo.",
            "fetched_at": NOW.strftime("%Y-%m-%dT%H:%M"), "location": {"lat": 4.0, "lon": 6.0, "note": "Gulf of Guinea centre"},
            "days": days}


def open_meteo_forecast():
    lat, lon = 4.0, 6.0
    base = f"latitude={lat}&longitude={lon}&timezone=UTC&forecast_days=10"
    with urllib.request.urlopen("https://api.open-meteo.com/v1/forecast?" + base +
                                "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                                "precipitation_probability_max,wind_speed_10m_max", timeout=30) as r:
        wx = json.load(r)["daily"]
    with urllib.request.urlopen("https://marine-api.open-meteo.com/v1/marine?" + base + "&daily=wave_height_max", timeout=30) as r:
        mw = json.load(r)["daily"]
    days = [{"date": wx["time"][i], "weather_code": wx["weather_code"][i], "tmax": wx["temperature_2m_max"][i],
             "tmin": wx["temperature_2m_min"][i], "precip_mm": wx["precipitation_sum"][i],
             "precip_prob": wx["precipitation_probability_max"][i], "wind_max_kmh": wx["wind_speed_10m_max"][i],
             "wave_height_max_m": mw["wave_height_max"][i]} for i in range(len(wx["time"]))]
    return {"source": "Open-Meteo forecast + marine APIs (open-meteo.com), CC BY 4.0",
            "fetched_at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "location": {"lat": lat, "lon": lon, "note": "Gulf of Guinea centre"}, "days": days}


def build_weather(rng, refresh):
    path = os.path.join(OUT, "forecast_snapshot.json")
    if refresh or not os.path.exists(path):
        try:
            snap = open_meteo_forecast() if refresh else synthetic_forecast(rng)
        except Exception as exc:  # network down: keep the demo working
            print(f"  forecast fetch failed ({exc}); using the synthetic forecast")
            snap = synthetic_forecast(rng)
        with open(path, "w") as f:
            json.dump(snap, f, indent=1)
    with open(path) as f:
        forecast = json.load(f)
    years = []
    for y in range(2016, 2026):
        calm = rng.randrange(9, 24)
        years.append({"year": y, "days": 30, "calm_days": calm, "rough_days": rng.randrange(1, 7),
                      "mean_wave_m": round(rng.uniform(1.3, 1.9), 2)})
    months = [{"month": m, "wave_mean_m": w, "wind_mean_kmh": k} for m, w, k in
              [(1, 1.3, 17), (2, 1.4, 18), (3, 1.6, 19), (4, 1.7, 18), (5, 1.8, 17), (6, 2.0, 19), (7, 2.2, 22), (8, 2.1, 21),
               (9, 1.9, 18), (10, 1.6, 15)]]
    return {"forecast": forecast,
            "history": {"window": "4 Nov to 3 Dec", "source": "Synthetic sea-state history (no marine reanalysis in the demo)", "years": years},
            "season_monthly": {"year": 2026, "source": "Synthetic monthly series", "months": months}}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-forecast", action="store_true")
    ap.add_argument("--ais", help="real AIS json from fetch_ais.py (same schema as ais.json)")
    ap.add_argument("--sar", help="real SAR json from detect_sar.py (same schema as sar.json)")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    rng = random.Random(SEED)

    t0 = NOW - dt.timedelta(hours=HOURS)
    if args.ais and args.sar:
        with open(args.ais) as f:
            ais = json.load(f)
        with open(args.sar) as f:
            sar = json.load(f)
        print(f"using real files: {len(ais['vessels'])} vessels, {len(sar['detections'])} detections")
    else:
        import extend_data as ext
        vessels = build_vessels(rng)
        dets = build_detections(rng, vessels)
        ext.nudge_to_sea(vessels, dets, sar_index=int((SAR_TIME - t0).total_seconds() // 60 // STEP_MIN))
        rng2 = random.Random(SEED + 1)       # separate stream: the original data above is not affected
        extra = ext.extend_vessels(rng2, vessels)
        dets += ext.extend_detections(rng2, extra, first_id=len(dets) + 1,
                                      sar_index=int((SAR_TIME - t0).total_seconds() // 60 // STEP_MIN))
        vessels += extra
        for d in dets:
            d.pop("_src")
        ais = {
            "meta": {"area": "Gulf of Guinea", "now": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "track_start": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "step_min": STEP_MIN,
                     "source": "Synthetic AIS baseline modelled on Neptune-AIS / Global Fishing Watch output",
                     "synthetic": True, "bbox": GEO["bbox"]},
            "vessels": [{k: v for k, v in x.items() if k != "scenario"} for x in vessels],
        }
        sar = {
            "scene": {"id": "S1A_IW_GRDH_20261104T054000 (simulated)", "time": SAR_TIME.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "polarisation": "VV", "preprocessing": "CLAHE contrast enhancement",
                      "detector": "YOLOv8m vessel detector (simulated output)", "footprint": GEO["footprint"]["poly"], "synthetic": True},
            "detections": dets,
        }
    # zone history needs counts per zone only for the baseline; fusion happens in the browser
    sar["history"] = build_history(rng, None)
    if not (args.ais and args.sar):
        import extend_data as ext
        sar["history"].update(ext.extra_history(random.Random(SEED + 2)))

    with open(os.path.join(OUT, "ais.json"), "w") as f:
        json.dump(ais, f, separators=(",", ":"))
    with open(os.path.join(OUT, "sar.json"), "w") as f:
        json.dump(sar, f, indent=1)
    weather = build_weather(rng, args.refresh_forecast)
    with open(os.path.join(OUT, "weather.json"), "w") as f:
        json.dump(weather, f, indent=1)
    print(f"ais.json: {len(ais['vessels'])} vessels · sar.json: {len(sar['detections'])} detections · "
          f"weather.json: {len(weather['forecast']['days'])} forecast days")
    for d in sar["detections"]:
        z = zone_of(d["lon"], d["lat"])
        print(f"  {d['id']}  {d['lat']:.3f} {d['lon']:.3f}  L={d['length_m']:>3} conf={d['confidence']:.2f}  {z or ''}")


if __name__ == "__main__":
    main()
