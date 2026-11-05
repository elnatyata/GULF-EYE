"""Per-vessel behaviour features from AIS tracks, with pandas.

Each feature is something an analyst can read back: speeds in knots, distances in km, durations in minutes.
"""
import math

import numpy as np
import pandas as pd

from .geo import KM_PER_DEG, dist_to_lanes_km, haversine_km, zone_at

FEATURES = ["sog_mean", "sog_std", "dsog_max", "turn_mean", "stop_frac", "gap_max_min", "rg_km", "hr_frac",
            "lane_dist_km", "report_ratio"]
FEATURE_LABELS = {
    "sog_mean": "mean speed (kn)", "sog_std": "speed variability (kn)", "dsog_max": "largest speed change between reports (kn)",
    "turn_mean": "mean course change between reports (°)", "stop_frac": "share of reports stopped",
    "gap_max_min": "longest AIS silence (min)", "rg_km": "area covered, radius of gyration (km)",
    "hr_frac": "share of reports in piracy zones", "lane_dist_km": "mean distance from shipping lanes when underway (km)",
    "report_ratio": "share of expected AIS reports received",
}


def _anchorage(geo, lat, lon):
    z = zone_at(geo, lon, lat)
    if z and z["kind"] == "transfer":
        return True
    return any(haversine_km(lat, lon, p["lat"], p["lon"]) <= 30 for p in geo["ports"])  # port + outer anchorage


def _near_platform(geo, lat, lon, km=20):
    return any(haversine_km(lat, lon, p["lat"], p["lon"]) <= km for p in geo["platforms"])


def frame(ais: dict) -> pd.DataFrame:
    df = pd.DataFrame(ais["rows"])
    if df.empty:
        return df
    df["t"] = pd.to_datetime(df["t"], utc=True)
    return df.sort_values(["vessel_id", "t"]).reset_index(drop=True)


def vessel_features(ais: dict, geo: dict) -> tuple[pd.DataFrame, dict]:
    """Return (features table indexed by vessel_id, extra details per vessel used as evidence)."""
    df = frame(ais)
    now = pd.Timestamp(ais["meta"]["now_effective"])
    start = pd.Timestamp(ais["meta"]["track_start"])
    step = ais["meta"]["step_min"]
    expected = (now - start).total_seconds() / 60 / step + 1
    piracy = [z for z in geo["zones"] if z["kind"] == "piracy"]

    df["zone"] = [(zone_at(geo, lo, la) or {}).get("id") for la, lo in zip(df["lat"], df["lon"])]
    df["hr"] = df["zone"].isin([z["id"] for z in piracy])
    df["anch"] = [_anchorage(geo, la, lo) for la, lo in zip(df["lat"], df["lon"])]

    rows, details = [], {}
    for vid, g in df.groupby("vessel_id", sort=False):
        g = g.reset_index(drop=True)
        dt_min = g["t"].diff().dt.total_seconds().div(60)
        dsog = g["sog"].diff().abs().where(dt_min <= step * 1.5)
        turn = g["cog"].diff().abs()
        turn = np.minimum(turn, 360 - turn).where((g["sog"] > 1) & (dt_min <= step * 1.5))
        gaps = list(dt_min.dropna()) + [(now - g["t"].iloc[-1]).total_seconds() / 60]
        lat0, lon0 = g["lat"].mean(), g["lon"].mean()
        dx = (g["lon"] - lon0) * KM_PER_DEG * math.cos(math.radians(lat0))
        dy = (g["lat"] - lat0) * KM_PER_DEG
        under = g[g["sog"] > 3]
        lane = float(np.mean([dist_to_lanes_km(geo, la, lo) for la, lo in zip(under["lat"], under["lon"])])) if len(under) else 0.0
        half = len(g) // 2
        rows.append({
            "vessel_id": vid,
            "sog_mean": float(g["sog"].mean()), "sog_std": float(g["sog"].std(ddof=0)),
            "dsog_max": float(dsog.max()) if dsog.notna().any() else 0.0,
            "turn_mean": float(turn.mean()) if turn.notna().any() else 0.0,
            "stop_frac": float((g["sog"] < 0.5).mean()),
            "gap_max_min": float(max(gaps)), "rg_km": float(np.sqrt((dx ** 2 + dy ** 2).mean())),
            "hr_frac": float(g["hr"].mean()), "lane_dist_km": lane, "report_ratio": float(min(1.0, len(g) / expected)),
        })
        last = g.iloc[-1]
        details[vid] = {
            "n": int(len(g)), "first_t": g["t"].iloc[0], "last_t": last["t"],
            "last": {"lat": float(last["lat"]), "lon": float(last["lon"]), "sog": float(last["sog"]), "cog": float(last["cog"])},
            "trailing_gap_min": (now - last["t"]).total_seconds() / 60,
            "gap_at": g["t"].iloc[int(np.argmax(list(dt_min.fillna(0)) + [0]))] if len(g) > 1 else last["t"],
            "anch_frac": float(g["anch"].mean()),
            "near_platform": _near_platform(geo, lat0, lon0),
            "hr_zones": sorted(set(g.loc[g["hr"], "zone"])),
            "zones": sorted(set(z for z in g["zone"] if isinstance(z, str))),
            "underway_first_half": float(g["sog"].iloc[:half].mean()) if half else 0.0,
            "stopped_tail": bool(len(g) >= 4 and (g["sog"].iloc[-3:] < 1).all()),
            "tail_anch": bool(g["anch"].iloc[-3:].any()),
            "dsog_at": g["t"].iloc[int(dsog.fillna(0).values.argmax())] if dsog.notna().any() else None,
        }
    return pd.DataFrame(rows).set_index("vessel_id"), details


def rendezvous(ais: dict, geo: dict, radius_km=0.5, max_sog=2.0, min_steps=2) -> dict:
    """Pairs of AIS vessels close together and slow, outside anchorages and platforms, for several reports."""
    df = frame(ais)
    if df.empty:
        return {}
    slow = df[df["sog"] <= max_sog]
    hits = {}
    for t, g in slow.groupby("t"):
        pts = list(g[["vessel_id", "lat", "lon"]].itertuples(index=False))
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                a, b = pts[i], pts[j]
                if a.vessel_id == b.vessel_id:
                    continue
                d = haversine_km(a.lat, a.lon, b.lat, b.lon)
                if d <= radius_km and not _anchorage(geo, a.lat, a.lon) and not _near_platform(geo, a.lat, a.lon, 3):
                    key = tuple(sorted((a.vessel_id, b.vessel_id)))
                    hits.setdefault(key, []).append({"t": t, "km": d, "lat": a.lat, "lon": a.lon})
    out = {}
    for (a, b), evs in hits.items():
        if len(evs) >= min_steps:
            for x, y in ((a, b), (b, a)):
                out.setdefault(x, []).append({"partner": y, "count": len(evs), "first": evs[0]["t"], "last": evs[-1]["t"],
                                              "min_km": min(e["km"] for e in evs), "lat": evs[0]["lat"], "lon": evs[0]["lon"]})
    return out
