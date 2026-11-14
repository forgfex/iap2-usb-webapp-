"""Stdlib-only tests. Run: python3 -m unittest discover -s tests -v

No iPhone, no pymobiledevice3, no network map tiles needed.
The loopback test runs a minimal fake iAP2 peer on 127.0.0.1 and
checks a real DeviceSession sends GGA/RMC for the shared target.
"""
import asyncio
import json
import socket
import struct
import sys
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import geo, nmea
from app.config import AppConfig
from app.iap2.wire import Message, MessageBuffer, Packet, parameter, parameters
from app.session import DeviceSession, Log, SessionManager
from app.target import LocationTarget
from app.web import WebServer


class TestNmea(unittest.TestCase):
    def test_shanghai(self):
        s = nmea.location_sentences(31.2304, 121.4737)
        self.assertIn("3113.8240,N", s[1])   # 31 + 0.2304*60
        self.assertIn("12128.4220,E", s[1])
        self.assertTrue(s[1].startswith("$GPGGA,"))
        self.assertTrue(s[2].startswith("$GPRMC,"))
        for sentence in s.values():
            body = sentence[1:].split("*")[0]
            expect = 0
            for b in body.encode():
                expect ^= b
            self.assertEqual(sentence.strip().split("*")[1], f"{expect:02X}")

    def test_coordinate_no_sixty_minutes(self):
        # 48.1173 etc. must never produce 60.0000 minutes (accessory-location)
        text, _ = nmea.coordinate(48.1173, True)
        self.assertNotIn("60.0000", text)

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            nmea.location_sentences(91, 0)


class TestGeo(unittest.TestCase):
    def test_distance_and_bearing(self):
        # Shanghai -> Beijing ~1068 km, roughly north
        d = geo.distance_m(31.2304, 121.4737, 39.9042, 116.4074)
        self.assertTrue(1_000_000 < d < 1_150_000, d)
        b = geo.bearing_deg(31.2304, 121.4737, 39.9042, 116.4074)
        self.assertTrue(330 < b < 360 or 0 <= b < 30, b)

    def test_gcj_roundtrip(self):
        lat, lon = geo.gcj2wgs(*geo.wgs2gcj(31.2304, 121.4737))
        self.assertAlmostEqual(lat, 31.2304, places=4)
        self.assertAlmostEqual(lon, 121.4737, places=4)


class TestTarget(unittest.TestCase):
    def test_jitter_rolled_once(self):
        t = LocationTarget(31.2304, 121.4737, jitter_position=True, jitter_meters=10)
        # apply() with no change must not re-roll
        first = t.snapshot()["resolvedLatitude"], t.snapshot()["resolvedLongitude"]
        t.apply(latitude=31.2304, longitude=121.4737)
        second = t.snapshot()["resolvedLatitude"], t.snapshot()["resolvedLongitude"]
        self.assertEqual(first, second)

    def test_jump_frame_then_stationary(self):
        t = LocationTarget(31.2304, 121.4737)
        t.next_fix()  # establish last fix
        t.apply(latitude=39.9042, longitude=116.4074)  # Beijing, >100 m
        jump = t.next_fix()
        self.assertEqual(jump.speed_mps, 250.0)
        self.assertIsNotNone(jump.bearing_deg)
        rest = t.next_fix()
        self.assertIsNone(rest.speed_mps)


class TestWire(unittest.TestCase):
    def test_packet_roundtrip(self):
        p = Packet(0xC0, 7, 3, 1, b"hello")
        self.assertEqual(Packet.decode(p.encode()), p)

    def test_message_parameters(self):
        m = Message(0xFFFB, parameter(0, b"abc") + parameter(1, b""))
        raw = m.encode()
        buf = MessageBuffer()
        out = buf.feed(raw)
        self.assertEqual(out[0].identifier, 0xFFFB)
        self.assertEqual(parameters(out[0].body), [(0, b"abc"), (1, b"")])


class TestWeb(unittest.TestCase):
    def test_api(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cfg = AppConfig(Path(td) / "cfg.json", rotate=False)
            mgr = SessionManager(LocationTarget(), cfg, auth_helper=None)
            srv = WebServer(mgr, cfg, port=0).start()
            base = f"http://127.0.0.1:{srv.port}"
            try:
                with urllib.request.urlopen(base + "/") as r:
                    self.assertIn("iAP2 USB", r.read().decode())
                with urllib.request.urlopen(base + "/api/state") as r:
                    state = json.loads(r.read())
                self.assertEqual(state["target"]["latitude"], 31.2304)
                req = urllib.request.Request(
                    base + "/api/target", data=json.dumps(
                        {"latitude": 39.9042, "longitude": 116.4074}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req) as r:
                    self.assertTrue(json.loads(r.read())["ok"])
                self.assertAlmostEqual(mgr.target.snapshot()["latitude"], 39.9042)
                # USB enumeration must not crash on a machine without usbmux
                with urllib.request.urlopen(base + "/api/devices") as r:
                    self.assertIsInstance(json.loads(r.read()), list)
            finally:
                srv.stop()
                mgr.shutdown()


class FakeIap2Peer(threading.Thread):
    """Minimal responder: SYN+ACK, 0x1D00, 0xFFFA, then capture 0xFFFB."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.received_nmea = []
        self.done = threading.Event()

    def _read_exact(self, conn, n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    def _read_packet(self, conn):
        header = self._read_exact(conn, 9)
        size = int.from_bytes(header[2:4], "big")
        rest = self._read_exact(conn, size - 9) if size > 9 else b""
        return Packet.decode(header + rest)

    def run(self):
        conn, _ = self.sock.accept()
        conn.settimeout(10)
        server_seq = 42
        client_seq = 0
        buf = MessageBuffer()
        try:
            syn = self._read_packet(conn)
            assert syn.flags & 0x80, "expected SYN"
            client_seq = syn.sequence
            offer = struct.pack(">BBHHHBB", 1, 1, 4096, 2000, 22, 5, 1) + bytes([1, 0, 2])
            conn.sendall(Packet(0xC0, server_seq, client_seq, 0, offer).encode())

            def send_csm(msg):
                nonlocal server_seq
                server_seq = (server_seq + 1) & 0xFF
                conn.sendall(Packet(0x40, server_seq, client_seq, 1,
                                    msg.encode()).encode())

            def ack(pkt):
                conn.sendall(Packet(0x40, server_seq, pkt.sequence, 0).encode())

            # Wait for client's handshake ACK, then identify + subscribe.
            # The phone initiates identification (0x1D00); the client only
            # replies 0x1D01 after that, so the peer must send 0x1D00 first.
            identified = subscribed = False
            sent_ident_request = False
            import time
            end = time.time() + 8
            while time.time() < end and len(self.received_nmea) < 1:
                pkt = self._read_packet(conn)
                client_seq = pkt.sequence
                if not pkt.payload and not sent_ident_request:
                    send_csm(Message(0x1D00))
                    sent_ident_request = True
                if pkt.payload:
                    ack(pkt)
                    for msg in buf.feed(pkt.payload):
                        if msg.identifier == 0x1D01 and not identified:
                            identified = True
                            send_csm(Message(0x1D02))
                            # Fake peer stands in for a phone that already
                            # accepted authentication; real iOS sends 0xAA05
                            # only after 0xAA00/0xAA01 + 0xAA02/0xAA03.
                            send_csm(Message(0xAA05))
                            send_csm(Message(0xFFFA,
                                             parameter(1, b"") + parameter(2, b"")))
                            subscribed = True
                        elif msg.identifier == 0xFFFB:
                            for _, body in parameters(msg.body):
                                self.received_nmea.append(body.decode())
                # empty ACK packets need no reply
            self.done.set()
        except Exception as exc:  # test asserts on received_nmea anyway
            print("fake peer error:", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass


class TestLoopbackSession(unittest.TestCase):
    def test_tcp_session_sends_gga_rmc(self):
        peer = FakeIap2Peer()
        peer.start()
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cfg = AppConfig(Path(td) / "cfg.json", rotate=False)
            target = LocationTarget(31.2304, 121.4737)
            session = DeviceSession("s1", "test", "tcp", f"127.0.0.1:{peer.port}",
                                    target, cfg, Log(), auth_helper=None,
                                    rate_ms=1000)

            async def _run():
                task = asyncio.create_task(session.run())
                # poll until peer captured a fix, or timeout
                for _ in range(40):
                    if peer.received_nmea:
                        break
                    await asyncio.sleep(0.25)
                session.request_stop()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            asyncio.run(_run())
        peer.join(timeout=5)
        self.assertTrue(peer.received_nmea, "no 0xFFFB received from session")
        joined = "".join(peer.received_nmea)
        self.assertIn("3113.8240,N", joined)
        self.assertIn("12128.4220,E", joined)
        self.assertIn("$GPGGA,", joined)
        self.assertIn("$GPRMC,", joined)


if __name__ == "__main__":
    unittest.main()
