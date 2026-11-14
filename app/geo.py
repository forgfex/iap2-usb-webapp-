"""Geography helpers. Port of iap2loc Protocol/Geo.cs (GPL-3.0)."""
import math

EARTH_RADIUS_M = 6_371_000.0
METRES_PER_DEGREE = 111_320.0


def distance_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing in degrees, 0 = north, clockwise."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def offset_m(lat, lon, distance_m_, angle_rad):
    """Move a point by distance/angle. Good enough for tens-of-metres jitter."""
    dlat = distance_m_ * math.cos(angle_rad) / METRES_PER_DEGREE
    dlon = distance_m_ * math.sin(angle_rad) / (
        METRES_PER_DEGREE * max(0.01, math.cos(math.radians(lat)))
    )
    return lat + dlat, lon + dlon


# --- WGS-84 <-> GCJ-02, same formulae as iap2loc Web/index.html ---
# NMEA only understands WGS-84. Gaode tiles are GCJ-02, so the Web UI
# converts on click; the Python side only needs it for favourites/CLI
# that were recorded against a Gaode map.
_PI = math.pi
_AXIS = 6378245.0
_EE = 0.00669342162296594323


def _out_of_china(lat, lon):
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x, y):
    r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    r += (20 * math.sin(6 * x * _PI) + 20 * math.sin(2 * x * _PI)) * 2 / 3
    r += (20 * math.sin(y * _PI) + 40 * math.sin(y / 3 * _PI)) * 2 / 3
    r += (160 * math.sin(y / 12 * _PI) + 320 * math.sin(y * _PI / 30)) * 2 / 3
    return r


def _transform_lon(x, y):
    r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    r += (20 * math.sin(6 * x * _PI) + 20 * math.sin(2 * x * _PI)) * 2 / 3
    r += (20 * math.sin(x * _PI) + 40 * math.sin(x / 3 * _PI)) * 2 / 3
    r += (150 * math.sin(x / 12 * _PI) + 300 * math.sin(x / 30 * _PI)) * 2 / 3
    return r


def wgs2gcj(lat, lon):
    if _out_of_china(lat, lon):
        return lat, lon
    dlat = _transform_lat(lon - 105, lat - 35)
    dlon = _transform_lon(lon - 105, lat - 35)
    rad = math.radians(lat)
    magic = math.sin(rad)
    magic = 1 - _EE * magic * magic
    sq = math.sqrt(magic)
    dlat = dlat * 180 / ((_AXIS * (1 - _EE)) / (magic * sq) * _PI)
    dlon = dlon * 180 / (_AXIS / sq * math.cos(rad) * _PI)
    return lat + dlat, lon + dlon


def gcj2wgs(lat, lon):
    wlat, wlon = lat, lon
    for _ in range(8):
        glat, glon = wgs2gcj(wlat, wlon)
        wlat += lat - glat
        wlon += lon - glon
    return wlat, wlon
