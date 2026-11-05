"""Geometry helpers (same formulas as web/js/util.js)."""
import math

KM_PER_DEG = 111.32
KM_PER_NM = 1.852


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(min(1.0, a)))


def move(lat, lon, brg, km):
    b = math.radians(brg)
    return lat + km * math.cos(b) / KM_PER_DEG, lon + km * math.sin(b) / (KM_PER_DEG * math.cos(math.radians(lat)))


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


def zone_at(geo, lon, lat):
    for z in geo["zones"]:
        if in_poly(lon, lat, z["poly"]):
            return z
    return None


def dist_to_segment_km(lat, lon, a, b):
    """Distance from a point to a lane segment (a, b are [lon, lat]); local flat projection is fine at this scale."""
    kx = KM_PER_DEG * math.cos(math.radians(lat))
    ax, ay = (a[0] - lon) * kx, (a[1] - lat) * KM_PER_DEG
    bx, by = (b[0] - lon) * kx, (b[1] - lat) * KM_PER_DEG
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


def dist_to_lanes_km(geo, lat, lon):
    best = float("inf")
    for lane in geo.get("lanes", []):
        for a, b in zip(lane["pts"], lane["pts"][1:]):
            best = min(best, dist_to_segment_km(lat, lon, a, b))
    return best
