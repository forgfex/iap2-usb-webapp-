"""App config + favourites. Port of iap2loc Engine/AppConfig.cs.

One JSON file next to the app (default ./iap2-usb-webapp.json):
accessory identity + saved points. The serial rotates on every
start -- iOS keeps per-accessory location state and drops fixes from
an accessory whose last known position is far away -- so a fresh
serial is what makes a cross-city move stick. Deleting the file
resets to defaults.
"""
import json
import os
import random
import string
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "iap2-usb-webapp.json"


def _random_serial():
    return "LC" + "".join(random.choice("0123456789ABCDEF") for _ in range(12))


class AppConfig:
    def __init__(self, path=None, rotate=True):
        self.path = Path(path) if path else DEFAULT_PATH
        self.name = "iAP2 Loc"
        self.model = "Loc1,1"
        self.manufacturer = "iap2-usb-web"
        self.serial = "LOC000000001"
        self.firmware = "0.1.0"
        self.hardware = "1.0"
        self.favorites = []  # [{id, name, latitude, longitude}]
        self._load()
        if rotate:
            self.rotate_identity()
        self.save()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        for key in ("name", "model", "manufacturer", "serial", "firmware", "hardware"):
            if isinstance(data.get(key), str) and data[key].strip():
                setattr(self, key, data[key].strip())
        if isinstance(data.get("favorites"), list):
            self.favorites = [f for f in data["favorites"]
                              if isinstance(f, dict) and "latitude" in f and "longitude" in f]

    def save(self):
        try:
            self.path.write_text(json.dumps({
                "name": self.name, "model": self.model,
                "manufacturer": self.manufacturer, "serial": self.serial,
                "firmware": self.firmware, "hardware": self.hardware,
                "favorites": self.favorites,
            }, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def rotate_identity(self):
        self.serial = _random_serial()
        self.name = "iAP2 Loc " + self.serial[-4:]
        return self.profile()

    def profile(self):
        return {"name": self.name, "model": self.model,
                "manufacturer": self.manufacturer, "serial": self.serial,
                "firmware": self.firmware, "hardware": self.hardware}

    def add_favorite(self, name, latitude, longitude):
        fav = {"id": "".join(random.choice(string.ascii_lowercase + string.digits)
                             for _ in range(8)),
               "name": name or f"{latitude:.6f},{longitude:.6f}",
               "latitude": float(latitude), "longitude": float(longitude)}
        self.favorites.append(fav)
        self.save()
        return fav

    def remove_favorite(self, fav_id):
        before = len(self.favorites)
        self.favorites = [f for f in self.favorites if f.get("id") != fav_id]
        if len(self.favorites) != before:
            self.save()
            return True
        return False
