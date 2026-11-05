#!/usr/bin/env python3
"""Step 1 of the pipeline: pull an AIS baseline for the Gulf of Guinea and write it in the dashboard's format.

    pip install neptune-ais polars
    python3 scripts/fetch_ais.py --start 2026-11-03 --end 2026-11-04 --out web/data/ais_live.json

Uses Neptune-AIS, which merges several AIS sources (Global Fishing Watch needs an API key; see the Neptune-AIS
docs). The column names below are looked up leniently because they differ between sources; check the first
printed row and adjust COLUMN_ALIASES if your source uses other names.

NOT TESTED against live data in this repository (no network or API key was available when it was written).
After running it, combine with a real SAR file:  python3 scripts/build_data.py --ais web/data/ais_live.json --sar web/data/sar_live.json
"""
import argparse
import datetime as dt
import json
import math

BBOX = {"lon0": 0.5, "lon1": 10.5, "lat0": 1.0, "lat1": 7.0}
STEP_MIN = 20
COLUMN_ALIASES = {
    "mmsi": ["mmsi", "ssvid", "vessel_id"],
    "time": ["timestamp", "time", "datetime", "ts"],
    "lat": ["lat", "latitude"],
    "lon": ["lon", "longitude"],
    "sog": ["speed", "sog", "speed_knots"],
    "cog": ["course", "cog", "heading"],
    "name": ["shipname", "name", "vessel_name"],
    "flag": ["flag", "flag_state"],
    "type": ["vessel_type", "ship_type", "type"],
    "length": ["length", "length_m"],
}


def pick(columns, key):
    for name in COLUMN_ALIASES[key]:
        if name in columns:
            return name
    return None


def vessel_type(raw):
    t = str(raw or "").lower()
    if "tank" in t:
        return "tanker"
    if "fish" in t:
        return "fishing"
    if "tug" in t:
        return "tug"
    if "supply" in t or "offshore" in t:
        return "supply"
    return "cargo"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="web/data/ais_live.json")
    ap.add_argument("--hours", type=int, default=12, help="length of the track window ending at --end")
    args = ap.parse_args()

    from neptune_ais import Neptune  # imported here so the other scripts work without it

    n = Neptune((args.start, args.end), sources=["gfw"], merge="best")
    n.download()
    df = n.positions().collect().to_pandas()
    print("columns:", list(df.columns))
    c = {k: pick(df.columns, k) for k in COLUMN_ALIASES}
    for need in ("mmsi", "time", "lat", "lon"):
        if not c[need]:
            raise SystemExit(f"column for '{need}' not found; edit COLUMN_ALIASES")

    df = df[(df[c["lon"]].between(BBOX["lon0"], BBOX["lon1"])) & (df[c["lat"]].between(BBOX["lat0"], BBOX["lat1"]))]
    df[c["time"]] = df[c["time"]].astype("datetime64[ns, UTC]") if df[c["time"]].dt.tz is None else df[c["time"]]
    end = df[c["time"]].max()
    t0 = end - dt.timedelta(hours=args.hours)
    n_steps = args.hours * 60 // STEP_MIN + 1

    vessels = []
    for mmsi, g in df[df[c["time"]] >= t0].groupby(c["mmsi"]):
        pts = [None] * n_steps
        for _, r in g.iterrows():
            i = round((r[c["time"]] - t0).total_seconds() / 60 / STEP_MIN)
            if 0 <= i < n_steps:
                sog = float(r[c["sog"]]) if c["sog"] and not math.isnan(r[c["sog"]]) else 0.0
                cog = int(r[c["cog"]]) % 360 if c["cog"] and not math.isnan(r[c["cog"]]) else 0
                pts[i] = [round(float(r[c["lat"]]), 4), round(float(r[c["lon"]]), 4), round(sog, 1), cog]
        if not any(pts):
            continue
        first = g.iloc[0]
        vessels.append({
            "id": str(mmsi), "name": str(first[c["name"]]) if c["name"] else f"MMSI {mmsi}",
            "flag": str(first[c["flag"]]) if c["flag"] else "–", "type": vessel_type(first[c["type"]] if c["type"] else None),
            "length_m": int(first[c["length"]]) if c["length"] and not math.isnan(first[c["length"]]) else 80,
            "destination": None, "pts": pts,
        })

    out = {"meta": {"area": "Gulf of Guinea", "now": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "track_start": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "step_min": STEP_MIN,
                    "source": "Neptune-AIS (Global Fishing Watch)", "synthetic": False, "bbox": BBOX}, "vessels": vessels}
    with open(args.out, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {len(vessels)} vessels to {args.out}")


if __name__ == "__main__":
    main()
