"""NMEA GGA+RMC encoding.

Merges accessory-location profile.location_sentences (stationary, fixed
quality/satellites) with iap2loc Protocol/Nmea.cs (altitude, speed,
bearing, accuracy -> HDOP, quality, satellites). Only GGA+RMC are ever
sent: iOS 0xFFFA only subscribes to those two.
"""
import math
from datetime import UTC, datetime

KNOTS_PER_MPS = 1.94384449


def coordinate(value, latitude):
    """accessory-location coordinate(): integer-minute units, no 60.0000 rollover."""
    limit = 90 if latitude else 180
    if not math.isfinite(value) or not -limit <= value <= limit:
        raise ValueError(f"Coordinate out of range: {value}")
    units = round(abs(value) * 60 * 10000)
    degrees, minute_units = divmod(units, 600000)
    minutes = minute_units / 10000
    hemi = ("S" if value < 0 else "N") if latitude else ("W" if value < 0 else "E")
    return f"{degrees:0{2 if latitude else 3}d}{minutes:07.4f}", hemi


def _sentence(body):
    checksum = 0
    for b in body.encode("ascii"):
        checksum ^= b
    return f"${body}*{checksum:02X}\r\n"


def location_sentences(latitude, longitude, when=None, *, altitude=10.0,
                       speed_mps=None, bearing_deg=None, accuracy_m=5.0,
                       quality=1, satellites=8):
    """Return {1: GGA, 2: RMC} for one fix.

    speed_mps/bearing_deg are None for a stationary fix (iap2loc: an
    unknown course stays empty in RMC, it must not claim north).
    accuracy_m maps to HDOP as accuracy/5, clamped 0.5..50 (iap2loc).
    """
    when = (when or datetime.now(UTC)).astimezone(UTC)
    lat, ns = coordinate(latitude, True)
    lon, ew = coordinate(longitude, False)
    clock = when.strftime("%H%M%S") + ".00"
    date = when.strftime("%d%m%y")
    hdop = 1.0
    if accuracy_m is not None and math.isfinite(accuracy_m) and accuracy_m > 0:
        hdop = min(50.0, max(0.5, accuracy_m / 5.0))
    alt = f"{altitude:.1f}" if altitude is not None and math.isfinite(altitude) else "0.0"
    gga = (
        f"GPGGA,{clock},{lat},{ns},{lon},{ew},"
        f"{max(0, min(8, quality))},{max(0, min(99, satellites)):02d},"
        f"{hdop:.1f},{alt},M,0.0,M,,"
    )
    speed = (
        f"{speed_mps * KNOTS_PER_MPS:.2f}"
        if speed_mps is not None and math.isfinite(speed_mps) and speed_mps >= 0
        else "0.00"
    )
    course = (
        f"{bearing_deg:.2f}"
        if bearing_deg is not None and math.isfinite(bearing_deg)
        else ""
    )
    rmc = f"GPRMC,{clock},A,{lat},{ns},{lon},{ew},{speed},{course},{date},,,A"
    return {1: _sentence(gga), 2: _sentence(rmc)}
