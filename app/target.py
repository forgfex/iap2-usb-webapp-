"""Shared live location target. Port of iap2loc Protocol/LocationTarget.cs.

All sessions share one target, so a map click moves every connected
iPhone together, on the next tick, without reconnecting.

Two iap2loc behaviours are kept deliberately:
- jitter is rolled ONCE per target change, then reused for every fix.
  A point that re-rolls every second looks like a broken GPS.
- a jump > 100 m gets one frame at 250 m/s (~900 km/h) on the bearing
  to the new target, then stationary again, so iOS does not drop a
  "stationary + teleport" fix.
"""
import math
import random
import threading
from dataclasses import dataclass

from . import geo

JUMP_THRESHOLD_M = 100.0
JUMP_SPEED_MPS = 250.0


@dataclass(frozen=True)
class Fix:
    latitude: float
    longitude: float
    altitude: float | None = 10.0
    speed_mps: float | None = None
    bearing_deg: float | None = None
    accuracy_m: float | None = 5.0
    quality: int = 1
    satellites: int = 8


class LocationTarget:
    def __init__(self, latitude=31.2304, longitude=121.4737, rng=None, **opts):
        self._lock = threading.Lock()
        self._rng = rng or random.Random()
        self.latitude = latitude
        self.longitude = longitude
        self.altitude = 10.0
        self.accuracy = 5.0
        self.speed_mps = None
        self.bearing_deg = None
        self.jitter_position = False
        self.jitter_meters = 10.0
        self.jitter_accuracy = False
        self.accuracy_min = 3.0
        self.accuracy_max = 12.0
        self.quality = 1
        self.satellites = 8
        self._resolved_lat = latitude
        self._resolved_lon = longitude
        self._resolved_accuracy = 5.0
        # Last fix actually sent, for jump detection.
        self._last_lat = None
        self._last_lon = None
        self._resolve_locked()
        if opts:
            self.apply(**opts)

    def apply(self, **update):
        """Partial update; every field optional. Returns True if resolved fix changed."""
        with self._lock:
            old_lat, old_lon = self.latitude, self.longitude
            old_jitter = (self.jitter_position, self.jitter_meters,
                          self.jitter_accuracy, self.accuracy_min, self.accuracy_max)
            for key, value in update.items():
                if value is None:
                    continue
                if key in ("latitude", "longitude", "altitude", "accuracy",
                           "speed_mps", "bearing_deg", "jitter_position",
                           "jitter_meters", "jitter_accuracy", "accuracy_min",
                           "accuracy_max", "quality", "satellites"):
                    setattr(self, key, value)
            self.quality = max(0, min(8, int(self.quality)))
            self.satellites = max(0, min(99, int(self.satellites)))
            self.accuracy_max = max(self.accuracy_min, self.accuracy_max)
            changed = (
                self.latitude != old_lat or self.longitude != old_lon
                or (self.jitter_position, self.jitter_meters, self.jitter_accuracy,
                    self.accuracy_min, self.accuracy_max) != old_jitter
            )
            if changed:
                self._resolve_locked()
            elif not self.jitter_accuracy:
                self._resolved_accuracy = self.accuracy
            return changed

    def _resolve_locked(self):
        self._resolved_lat, self._resolved_lon = self.latitude, self.longitude
        self._resolved_accuracy = self.accuracy
        if self.jitter_position and self.jitter_meters > 0:
            angle = self._rng.random() * 2 * math.pi
            dist = math.sqrt(self._rng.random()) * self.jitter_meters
            self._resolved_lat, self._resolved_lon = geo.offset_m(
                self.latitude, self.longitude, dist, angle)
        if self.jitter_accuracy:
            span = max(0.0, self.accuracy_max - self.accuracy_min)
            self._resolved_accuracy = round(
                self.accuracy_min + self._rng.random() * span, 1)

    def snapshot(self):
        with self._lock:
            return {
                "latitude": self.latitude, "longitude": self.longitude,
                "altitude": self.altitude, "accuracy": self.accuracy,
                "speed": self.speed_mps, "bearing": self.bearing_deg,
                "jitterPosition": self.jitter_position,
                "jitterMeters": self.jitter_meters,
                "jitterAccuracy": self.jitter_accuracy,
                "accuracyMin": self.accuracy_min, "accuracyMax": self.accuracy_max,
                "resolvedLatitude": self._resolved_lat,
                "resolvedLongitude": self._resolved_lon,
                "resolvedAccuracy": self._resolved_accuracy,
                "quality": self.quality, "satellites": self.satellites,
            }

    def next_fix(self):
        """Next fix to send. Handles the one-frame jump speed."""
        with self._lock:
            lat, lon = self._resolved_lat, self._resolved_lon
            speed, bearing = self.speed_mps, self.bearing_deg
            if self._last_lat is not None:
                dist = geo.distance_m(self._last_lat, self._last_lon, lat, lon)
                if dist > JUMP_THRESHOLD_M:
                    speed = JUMP_SPEED_MPS
                    bearing = geo.bearing_deg(self._last_lat, self._last_lon, lat, lon)
            self._last_lat, self._last_lon = lat, lon
            return Fix(lat, lon, self.altitude, speed, bearing,
                       self._resolved_accuracy, self.quality, self.satellites)
