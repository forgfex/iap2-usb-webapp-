"""Local Web console. Port of iap2loc Web/WebServer.cs to stdlib only.

TcpListener -> http.server.ThreadingHTTPServer, bound to loopback
only (127.0.0.1, default port 8737 -- same as iap2loc, so the
console URL is familiar). No external packages, no URL reservation.

API (same shapes as iap2loc, so its index.html logic ports directly):
  GET  /                     index.html (Leaflet map)
  GET  /api/state            {warning, identity, target, sessions[]}
  GET  /api/devices          [{name, address, likelyApple}] -- USB via usbmux
  GET  /api/log?since=N      {last, entries[]}
  GET  /api/favorites        [{id, name, latitude, longitude}]
  POST /api/target           {latitude, longitude, altitude, accuracy,
                             jitterPosition, jitterMeters, jitterAccuracy,
                             accuracyMin, accuracyMax, rate, quality, satellites}
  POST /api/sessions         {transport: usb|tcp, udid, host, port, label, rate}
  POST /api/sessions/remove  {id}
  POST /api/stop-all         {}
  POST /api/identity/rotate  {}
  POST /api/favorites        {name, latitude, longitude}
  POST /api/favorites/remove {id}
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INDEX = Path(__file__).resolve().parent / "web" / "index.html"


def _target_update(payload):
    mapping = {
        "latitude": "latitude", "longitude": "longitude",
        "altitude": "altitude", "accuracy": "accuracy",
        "speed": "speed_mps", "bearing": "bearing_deg",
        "jitterPosition": "jitter_position", "jitterMeters": "jitter_meters",
        "jitterAccuracy": "jitter_accuracy",
        "accuracyMin": "accuracy_min", "accuracyMax": "accuracy_max",
        "quality": "quality", "satellites": "satellites",
    }
    update = {}
    for src, dst in mapping.items():
        if payload.get(src) is not None:
            try:
                update[dst] = float(payload[src]) if dst not in (
                    "jitter_position", "jitter_accuracy") else bool(payload[src])
            except (TypeError, ValueError):
                continue
    if "quality" in update:
        update["quality"] = int(update["quality"])
    if "satellites" in update:
        update["satellites"] = int(update["satellites"])
    return update


class WebServer:
    def __init__(self, manager, config, port=8737, host="127.0.0.1"):
        self.manager = manager
        self.config = config
        self.port = port
        self.host = host
        self._httpd = None
        self._index = INDEX.read_bytes()

    def start(self):
        manager, config = self.manager, self.config
        index_bytes = self._index

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # keep stdout for app logs only
                pass

            def _json(self, obj, status=200):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if not length:
                    return {}
                try:
                    return json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    return {}

            def do_GET(self):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(self.path)
                path, query = parsed.path, parse_qs(parsed.query)
                if path in ("/", "/index.html"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(index_bytes)))
                    self.end_headers()
                    self.wfile.write(index_bytes)
                    return
                if path == "/api/state":
                    state = manager.snapshot()
                    if state.get("identity") is not None:
                        state["identity"] = {**state["identity"],
                                             "path": str(config.path)}
                    self._json(state)
                    return
                if path == "/api/devices":
                    from .transport import list_usb_devices
                    devices, error = list_usb_devices()
                    if error:
                        manager.log.add("warn", f"USB 设备枚举：{error}")
                    self._json([{"name": f"iPhone {d['udid'][:8]}",
                                 "address": d["udid"], "likelyApple": True}
                                for d in devices])
                    return
                if path == "/api/log":
                    try:
                        since = int(query.get("since", ["0"])[0])
                    except ValueError:
                        since = 0
                    entries = manager.log.since(since)
                    last = entries[-1]["seq"] if entries else since
                    # also report the global last seq even with no new entries
                    with manager.log._lock:
                        last = manager.log._seq
                    self._json({"last": last, "entries": entries})
                    return
                if path == "/api/favorites":
                    self._json(config.favorites)
                    return
                self._json({"error": "not found"}, status=404)

            def do_POST(self):
                payload = self._read_json()
                path = self.path.split("?", 1)[0]
                if path == "/api/target":
                    manager.apply_target(_target_update(payload),
                                         payload.get("rate"))
                    self._json({"ok": True, "target": manager.target.snapshot()})
                    return
                if path == "/api/sessions":
                    transport = (payload.get("transport") or "usb").lower()
                    if transport == "usb":
                        address = payload.get("udid") or payload.get("mac") or ""
                        if not address:
                            self._json({"ok": False, "error": "请先选择 USB 设备"})
                            return
                        label = payload.get("label") or f"iPhone {address[:8]}"
                    elif transport == "tcp":
                        host = payload.get("host") or "127.0.0.1"
                        port = int(payload.get("port") or 0)
                        if not port:
                            self._json({"ok": False, "error": "缺少 TCP 端口"})
                            return
                        address, label = f"{host}:{port}", payload.get("label") or address
                    else:
                        self._json({"ok": False, "error": f"未知传输 '{transport}'"})
                        return
                    session = manager.add(transport, address, label=label,
                                          rate_ms=int(payload.get("rate") or 1000))
                    self._json({"ok": session.error is None,
                                "error": session.error, "id": session.id})
                    return
                if path == "/api/sessions/remove":
                    self._json({"ok": manager.remove(payload.get("id") or "")})
                    return
                if path == "/api/stop-all":
                    manager.stop_all()
                    self._json({"ok": True})
                    return
                if path == "/api/identity/rotate":
                    profile = config.rotate_identity()
                    config.save()
                    self._json({"ok": True, "identity": profile})
                    return
                if path == "/api/favorites":
                    try:
                        fav = config.add_favorite(payload.get("name"),
                                                  payload["latitude"],
                                                  payload["longitude"])
                    except (KeyError, TypeError, ValueError):
                        self._json({"ok": False, "error": "收藏需要 latitude/longitude"})
                        return
                    self._json({"ok": True, "favorite": fav})
                    return
                if path == "/api/favorites/remove":
                    self._json({"ok": config.remove_favorite(payload.get("id") or "")})
                    return
                self._json({"error": "not found"}, status=404)

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True,
                         name="web-server").start()
        return self

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
