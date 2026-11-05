#!/usr/bin/env python3
"""Build web/data/geo.json for the whole Gulf of Guinea from Natural Earth (public domain, naturalearthdata.com).

    pip install shapely
    curl -LO https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson
    curl -LO https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson
    python3 scripts/build_geo.py --land ne_10m_land.geojson --countries ne_50m_admin_0_countries.geojson

Keeps the hand-made layers (ports, platforms, risk zones, shipping lanes, radar footprint) and replaces the
schematic coastline with the real one, simplified for drawing (about 1 km). Not for navigation.
"""
import argparse
import json
import os

from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "data", "geo.json")
BBOX = {"lon0": -8.5, "lon1": 12.0, "lat0": -4.5, "lat1": 7.8}

PORTS = [  # id, name, lon, lat  (approximate harbour entrances)
    ("abidjan", "Abidjan", -4.01, 5.25), ("sanpedro", "San-Pédro", -6.62, 4.73), ("takoradi", "Takoradi", -1.74, 4.88),
    ("tema", "Tema", 0.02, 5.62), ("lome", "Lomé", 1.28, 6.13), ("cotonou", "Cotonou", 2.43, 6.34), ("lagos", "Lagos", 3.40, 6.41),
    ("forcados", "Forcados", 5.35, 5.36), ("bonny", "Bonny", 7.08, 4.33), ("calabar", "Calabar", 8.36, 4.52),
    ("malabo", "Malabo", 8.78, 3.76), ("douala", "Douala", 9.55, 3.95), ("kribi", "Kribi", 9.90, 2.94),
    ("bata", "Bata", 9.76, 1.86), ("libreville", "Libreville", 9.42, 0.40), ("portgentil", "Port-Gentil", 8.70, -0.62),
    ("saotome", "São Tomé", 6.74, 0.34),
]
NEW_PLATFORMS = [
    {"id": "P6", "name": "Offshore field P6 (Ghana)", "lon": -2.90, "lat": 4.55},
    {"id": "P7", "name": "Offshore field P7 (Côte d'Ivoire)", "lon": -4.60, "lat": 4.95},
    {"id": "P8", "name": "Offshore field P8 (Equatorial Guinea)", "lon": 8.05, "lat": 3.68},
    {"id": "P9", "name": "Offshore field P9 (Gabon)", "lon": 8.55, "lat": -1.05},
    {"id": "P10", "name": "Offshore field P10 (Nigeria deep water)", "lon": 4.45, "lat": 3.55},
]
NEW_ZONES = [
    {"id": "Z5", "name": "Deep offshore (kidnap risk)", "kind": "piracy", "weight": 20, "color": "#E5484D",
     "poly": [[3.0, 3.0], [7.8, 3.0], [7.8, 1.2], [3.0, 1.2]]},
    {"id": "Z6", "name": "São Tomé approaches", "kind": "piracy", "weight": 18, "color": "#E5484D",
     "poly": [[5.8, 2.2], [8.0, 2.2], [8.0, -0.6], [5.8, -0.6]]},
    {"id": "Z7", "name": "Ghana–Côte d'Ivoire fishing grounds", "kind": "fishing", "weight": 12, "color": "#F5A524",
     "poly": [[-5.5, 5.0], [0.6, 5.0], [0.6, 4.0], [-5.5, 4.0]]},
    {"id": "Z8", "name": "Lomé transfer area", "kind": "transfer", "weight": 15, "color": "#A78BFA",
     "poly": [[0.9, 6.05], [1.9, 6.05], [1.9, 5.65], [0.9, 5.65]]},
]
LANES = [
    {"id": "coast_west", "name": "Lagos – Tema coastal lane", "pts": [[3.3, 6.0], [2.4, 5.85], [1.4, 5.65], [0.8, 5.35], [0.05, 5.45]]},
    {"id": "abidjan_tema", "name": "Abidjan – Tema coastal lane", "pts": [[-4.0, 5.05], [-2.6, 4.6], [-1.6, 4.65], [-0.6, 5.2], [0.05, 5.45]]},
    {"id": "sanpedro_abidjan", "name": "San-Pédro – Abidjan lane", "pts": [[-6.6, 4.55], [-5.4, 4.85], [-4.0, 5.05]]},
    {"id": "lagos_bonny", "name": "Lagos – Bonny lane", "pts": [[3.5, 5.95], [4.5, 5.55], [5.05, 5.0], [5.45, 4.45], [6.2, 4.0], [7.0, 3.85]]},
    {"id": "bonny_douala", "name": "Bonny – Douala lane", "pts": [[7.1, 3.9], [8.0, 3.95], [8.8, 3.98], [9.4, 3.55]]},
    {"id": "douala_gabon", "name": "Douala – Libreville – Port-Gentil lane", "pts": [[9.4, 3.55], [9.55, 2.4], [9.3, 1.2], [9.1, 0.4], [8.6, -0.6]]},
    {"id": "offshore_ew", "name": "Offshore east–west transit", "pts": [[-7.5, 3.4], [-3.0, 3.0], [1.0, 2.6], [3.5, 2.55], [6.0, 2.5], [9.3, 2.4]]},
    {"id": "southbound", "name": "Southbound transit (to Angola)", "pts": [[5.2, 3.6], [5.9, 1.2], [6.05, -0.6], [6.6, -2.5], [8.0, -4.3]]},
]


def ring(poly, digits=3):
    return [[round(x, digits), round(y, digits)] for x, y in poly.exterior.coords]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--land", required=True)
    ap.add_argument("--countries", required=True)
    ap.add_argument("--tolerance", type=float, default=0.01, help="simplification in degrees (0.01 ≈ 1.1 km)")
    args = ap.parse_args()
    with open(OUT, encoding="utf-8") as f:
        old = json.load(f)
    frame = box(BBOX["lon0"], BBOX["lat0"], BBOX["lon1"], BBOX["lat1"])
    with open(args.land, encoding="utf-8") as f:
        land = unary_union([shape(ft["geometry"]).intersection(frame) for ft in json.load(f)["features"]
                            if shape(ft["geometry"]).intersects(frame)])
    land = land.simplify(args.tolerance, preserve_topology=True)
    polys = sorted([p for p in getattr(land, "geoms", [land]) if p.geom_type == "Polygon" and p.area > 0.0004],
                   key=lambda p: -p.area)
    mainland, islands = polys[0], polys[1:]
    names = {"Bioko": (8.75, 3.5), "São Tomé": (6.6, 0.25), "Príncipe": (7.4, 1.6), "Annobón": (5.63, -1.43)}

    def island_name(p):  # noqa: E306
        c = p.representative_point()
        best = min(names, key=lambda n: (names[n][0] - c.x) ** 2 + (names[n][1] - c.y) ** 2)
        return best if (names[best][0] - c.x) ** 2 + (names[best][1] - c.y) ** 2 < 0.25 else "Islet"

    countries = []
    with open(args.countries, encoding="utf-8") as f:
        for ft in json.load(f)["features"]:
            g = shape(ft["geometry"])
            if not g.intersects(frame):
                continue
            g = g.intersection(frame).simplify(0.02, preserve_topology=True)
            parts = [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and p.area > 0.01]
            if not parts:
                continue
            big = max(parts, key=lambda p: p.area)
            lp = big.representative_point()
            countries.append({"name": ft["properties"]["NAME"], "iso": ft["properties"]["ISO_A3"],
                              "label": [round(lp.x, 2), round(lp.y, 2)], "polys": [ring(p, 2) for p in parts]})
    geo = {
        "note": ("Gulf of Guinea, Natural Earth 1:10m coastline and 1:50m borders (public domain), simplified to about 1 km. "
                 "Ports, platforms, zones and lanes are hand-placed for the demo. Not for navigation. Coordinates are [lon, lat]."),
        "bbox": BBOX, "land": ring(mainland), "islands": [{"name": island_name(p), "poly": ring(p)} for p in islands],
        "countries": countries,
        "ports": [{"id": i, "name": n, "lon": lo, "lat": la} for i, n, lo, la in PORTS],
        "platforms": old["platforms"][:5] + NEW_PLATFORMS,
        "zones": old["zones"][:4] + NEW_ZONES,
        "lanes": LANES,
        "footprint": old["footprint"],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(geo, f, separators=(",", ":"), ensure_ascii=False)
    print(f"geo.json: mainland {len(geo['land'])} pts, {len(islands)} islands, {len(countries)} countries, "
          f"{os.path.getsize(OUT) // 1024} kB")


if __name__ == "__main__":
    main()
