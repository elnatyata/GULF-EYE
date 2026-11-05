"""File-based adapters for the existing pipeline output (scripts/build_data.py, fetch_ais.py, detect_sar.py)."""
import datetime as dt
import json
import os

from .. import config, db
from .base import Adapter


def _parse(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


class AISFileAdapter(Adapter):
    key = "ais_file"
    name = "AIS baseline (file)"
    kind = "ais"
    description = ("Vessel tracks in the dashboard format. Default: simulated web/data/ais.json. Point GULFEYE_AIS_FILE at "
                   "the output of scripts/fetch_ais.py (Neptune-AIS / Global Fishing Watch) for real data.")

    def __init__(self):
        self.path = os.environ.get("GULFEYE_AIS_FILE", str(config.DATA / "ais.json"))

    def raw(self) -> dict:
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    @property
    def synthetic(self):
        try:
            return bool(self.raw()["meta"].get("synthetic", True))
        except OSError:
            return True

    def configured(self):
        return os.path.exists(self.path)

    def extra_status(self):
        if not self.configured():
            return {}
        meta = self.raw()["meta"]
        return {"records": len(self.raw()["vessels"]), "as_of": meta["now"], "source_note": meta.get("source")}

    def load(self) -> dict:
        """Return {"meta", "vessels": {id: info}, "rows": [ {vessel_id, t, lat, lon, sog, cog, source} ]}."""
        raw = self.raw()
        meta = raw["meta"]
        t0 = _parse(meta["track_start"])
        step = dt.timedelta(minutes=meta["step_min"])
        vessels, rows = {}, []
        for v in raw["vessels"]:
            vessels[v["id"]] = {k: v.get(k) for k in ("id", "name", "flag", "type", "length_m", "destination")}
            for i, p in enumerate(v["pts"]):
                if p:
                    rows.append({"vessel_id": v["id"], "t": t0 + i * step, "lat": p[0], "lon": p[1], "sog": p[2],
                                 "cog": p[3], "source": "ais_file"})
        # positions pushed through POST /api/ingest/ais are appended to the same tracks
        for r in db.q("SELECT vessel_id, t, lat, lon, sog, cog FROM positions_ingested ORDER BY t"):
            if r["vessel_id"] not in vessels:
                vessels[r["vessel_id"]] = {"id": r["vessel_id"], "name": f"MMSI {r['vessel_id']}", "flag": "–",
                                           "type": "cargo", "length_m": None, "destination": None}
            rows.append({**r, "t": _parse(r["t"]), "source": "ingest"})
        now = max([_parse(meta["now"])] + [r["t"] for r in rows])
        return {"meta": {**meta, "now_effective": now}, "vessels": vessels, "rows": rows}


class SARFileAdapter(Adapter):
    key = "sar_file"
    name = "Sentinel-1 detections (file)"
    kind = "sar"
    description = ("Radar vessel detections. Default: simulated web/data/sar.json. Point GULFEYE_SAR_FILE at the output of "
                   "scripts/detect_sar.py (YOLOv8 on Sentinel-1) for real scenes.")

    def __init__(self):
        self.path = os.environ.get("GULFEYE_SAR_FILE", str(config.DATA / "sar.json"))

    @property
    def synthetic(self):
        try:
            return bool(self.load()["scene"].get("synthetic", True))
        except OSError:
            return True

    def configured(self):
        return os.path.exists(self.path)

    def extra_status(self):
        if not self.configured():
            return {}
        d = self.load()
        return {"records": len(d["detections"]), "as_of": d["scene"]["time"], "source_note": d["scene"]["id"]}

    def load(self) -> dict:
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)


class GeoAdapter(Adapter):
    key = "geo"
    name = "Reference geography"
    kind = "reference"
    synthetic = True
    description = "Hand-simplified coastline, ports, platforms, risk zones and shipping lanes (web/data/geo.json). Not for navigation."

    def load(self) -> dict:
        with open(config.DATA / "geo.json", encoding="utf-8") as f:
            return json.load(f)
