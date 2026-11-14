"""Session manager: one iAP2 session per USB iPhone, one shared target.

Ports accessory-location cli.run() (the USB handshake + send loop)
into a long-running per-device asyncio task, and iap2loc
Engine/SessionManager.cs + RunSession.cs (shared LocationTarget,
per-session state, Web snapshot) into Python.

State machine, per session (message ids from docs/protocol.md):
  0x1D00 StartIdentification -> reply 0x1D01 identity
  0x1D02 IdentificationAccepted / 0x1D03 rejected
  0xAA00 request certificate -> 0xAA01 (via auth helper, if configured)
  0xAA02 challenge -> 0xAA03 signature (ECDSA P-256, prehashed, r||s)
  0xAA05 AuthenticationSucceeded / 0xAA04 failed
  0xFFFA StartLocationInformation -> start sending GGA/RMC
  0xFFFC StopLocationInformation -> stop sending

An acknowledgement only confirms delivery, not adoption: the phone's
map is the only proof the position moved (both upstreams say this).
"""
import asyncio
import math
import threading
import time
from datetime import datetime

from .iap2.auth import AuthenticationProvider
from .iap2.link import Link
from .iap2.profile import identity as identity_message, location_message
from .iap2.wire import parameters
from .target import LocationTarget
from .transport import TcpStream, open_usb_stream

HELPER_DEFAULT = "tools/local_mfi_v3_helper.py"


class Log:
    def __init__(self, capacity=800):
        self._lock = threading.Lock()
        self._entries = []
        self._seq = 0
        self.capacity = capacity

    def add(self, level, text):
        with self._lock:
            self._seq += 1
            self._entries.append({
                "seq": self._seq,
                "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "level": level, "text": text})
            del self._entries[: -self.capacity]

    def since(self, seq=0):
        with self._lock:
            return [e for e in self._entries if e["seq"] > seq]


class DeviceSession:
    def __init__(self, sid, label, transport, address, target, config,
                 log, auth_helper=None, rate_ms=1000):
        self.id = sid
        self.label = label
        self.transport = transport  # "usb" | "tcp"
        self.address = address      # UDID for usb, "host:port" for tcp
        self.target = target
        self.config = config
        self.log = log
        self.auth_helper = auth_helper
        self.rate_ms = max(100, int(rate_ms))
        self.running = False
        self.authenticated = False
        self.subscribed = False
        self.sent = 0
        self.error = None
        self.link_state = "Idle"
        self._stop = None  # asyncio.Event, created in the loop thread
        self._task = None

    def snapshot(self):
        return {"id": self.id, "label": self.label, "transport": self.transport,
                "address": self.address, "running": self.running,
                "link": self.link_state, "authenticated": self.authenticated,
                "subscribed": self.subscribed, "sent": self.sent,
                "error": self.error}

    def request_stop(self):
        if self._stop is not None:
            # Called from the Web thread; the loop thread owns the event.
            loop = getattr(self, "_loop", None)
            if loop is not None:
                loop.call_soon_threadsafe(self._stop.set)
                return
            self._stop.set()

    async def run(self):
        """Full session. Runs until stopped, on error, or on link close."""
        self._stop = asyncio.Event()
        self.running = True
        self.error = None
        phone = link = auth = None
        try:
            if self.transport == "usb":
                phone, stream = await open_usb_stream(
                    None if self.address in (None, "", "usb") else self.address)
                self.log.add("info", f"[{self.label}] USB 已连接，打开 com.apple.carkit.service")
            elif self.transport == "tcp":
                host, _, port = self.address.rpartition(":")
                stream = await TcpStream.connect(host or "127.0.0.1", int(port or 0))
                self.log.add("info", f"[{self.label}] TCP 已连接 {self.address}")
            else:
                raise ValueError(f"unknown transport '{self.transport}'")

            auth = (AuthenticationProvider(self.auth_helper)
                    if self.auth_helper else None)
            if auth is None:
                self.log.add("warn", f"[{self.label}] 未配置 MFi 认证 helper：识别可用，认证不会通过，"
                                     "手机会忽略定位数据")
            self.link_state = "Handshake"
            link = Link(stream, emit=lambda **e: self._on_link_event(e))
            await link.handshake()
            self.link_state = "Ready"
            self.log.add("ok", f"[{self.label}] iAP2 链路就绪，等待手机识别配件")

            identified = authenticated = False
            active, last_sent, intervals = set(), {}, {}
            while not self._stop.is_set():
                try:
                    message = await link.receive(0.25)
                except TimeoutError:
                    message = None
                if message is not None:
                    fields = parameters(message.body)
                    if message.identifier == 0x1D00:
                        await link.send(identity_message(
                            **self.config.profile(), transport="usb"))
                        self.log.add("step", f"[{self.label}] rx 0x1D00 -> tx 0x1D01 identity")
                    elif message.identifier == 0x1D02:
                        identified = True
                        self.log.add("ok", f"[{self.label}] rx 0x1D02 IdentificationAccepted")
                    elif message.identifier == 0x1D03:
                        raise RuntimeError(
                            f"Identification rejected: {[i for i, _ in fields]}")
                    elif message.identifier == 0xAA00:
                        if auth is None:
                            self.log.add("warn", f"[{self.label}] rx 0xAA00，但无认证 helper")
                        else:
                            await link.send(await auth.certificate())
                            self.log.add("step", f"[{self.label}] rx 0xAA00 -> tx 0xAA01 certificate")
                    elif message.identifier == 0xAA02:
                        if auth is None:
                            self.log.add("warn", f"[{self.label}] rx 0xAA02，但无认证 helper")
                        else:
                            challenges = [b for i, b in fields if i == 0]
                            if len(challenges) != 1:
                                raise RuntimeError("Invalid authentication challenge parameters")
                            await link.send(await auth.sign(challenges[0]))
                            self.log.add("step", f"[{self.label}] rx 0xAA02 -> tx 0xAA03 signature")
                    elif message.identifier == 0xAA05:
                        authenticated = True
                        self.authenticated = True
                        self.log.add("ok", f"[{self.label}] rx 0xAA05 AuthenticationSucceeded")
                    elif message.identifier == 0xAA04:
                        raise RuntimeError("Accessory authentication failed (0xAA04)")
                    elif message.identifier == 0xFFFA:
                        active = {i for i, b in fields if i in (1, 2) and not b}
                        intervals = {
                            i - 0x8000: max(1.0, int.from_bytes(b, "big") / 1000)
                            for i, b in fields
                            if i in (0x8001, 0x8002) and len(b) == 4}
                        last_sent.clear()
                        self.subscribed = True
                        self.log.add("ok", f"[{self.label}] rx 0xFFFA StartLocationInformation "
                                           f"sentences={sorted(active)}")
                    elif message.identifier == 0xFFFC:
                        active.clear()
                        self.subscribed = False
                        self.log.add("info", f"[{self.label}] rx 0xFFFC StopLocationInformation")
                if identified and authenticated and active:
                    now = asyncio.get_running_loop().time()
                    interval = max(self.rate_ms / 1000.0, 0.1)
                    due = sorted(i for i in active
                                 if now - last_sent.get(i, -math.inf)
                                 >= max(interval, intervals.get(i, 1)))
                    if due:
                        fix = self.target.next_fix()
                        await link.send(location_message(fix, set(due)))
                        last_sent.update({i: now for i in due})
                        self.sent += 1
                        if self.sent == 1 or self.sent % 10 == 0:
                            self.log.add("info", f"[{self.label}] 已上报 {self.sent} 次 "
                                                f"{fix.latitude:.6f},{fix.longitude:.6f}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = str(exc)
            self.log.add("error", f"[{self.label}] 会话结束：{exc}")
        finally:
            self.running = False
            self.link_state = "Idle"
            self.subscribed = False
            if link is not None:
                try:
                    await link.close()
                except Exception:
                    pass
            if phone is not None:
                try:
                    await phone.close()
                except Exception:
                    pass
            if auth is not None:
                try:
                    await auth.close()
                except Exception:
                    pass

    def _on_link_event(self, event):
        # Link emits link_ready / retransmit / message events; keep the log readable.
        if event.get("event") == "link_ready":
            self.log.add("info", f"[{self.label}] link_ready control_session={event.get('control_session')}")
        elif event.get("event") == "retransmit":
            self.log.add("warn", f"[{self.label}] 重传 attempt={event.get('attempt')}")


class SessionManager:
    """Owns every running session; all share one LocationTarget."""

    def __init__(self, target=None, config=None, auth_helper=None):
        self.target = target or LocationTarget()
        self.config = config
        self.auth_helper = auth_helper
        self.log = Log()
        self._lock = threading.Lock()
        self._sessions = {}
        self._next_id = 0
        self._loop = None
        self._loop_thread = None

    # -- asyncio loop in a background thread (Web server is sync) --
    def _ensure_loop(self):
        if self._loop is not None:
            return self._loop
        loop = asyncio.new_event_loop()
        self._loop = loop

        def _run():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        t = threading.Thread(target=_run, daemon=True, name="iap2-loop")
        t.start()
        self._loop_thread = t
        return loop

    def add(self, transport, address, label=None, rate_ms=1000):
        with self._lock:
            self._next_id += 1
            sid = f"s{self._next_id}"
            session = DeviceSession(
                sid, label or address or "iPhone", transport, address,
                self.target, self.config, self.log,
                auth_helper=self.auth_helper, rate_ms=rate_ms)
            self._sessions[sid] = session
        loop = self._ensure_loop()
        session._loop = loop
        session._task = asyncio.run_coroutine_threadsafe(session.run(), loop)
        # Surface start-up failures that happen synchronously in run()
        # via the session snapshot / log; the task itself logs them.
        return session

    def remove(self, sid):
        with self._lock:
            session = self._sessions.pop(sid, None)
        if session is None:
            return False
        session.request_stop()
        if session._task is not None:
            session._task.cancel()
        return True

    def stop_all(self):
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            s.request_stop()
            if s._task is not None:
                s._task.cancel()

    def apply_target(self, update, rate_ms=None):
        changed = self.target.apply(**update)
        if rate_ms is not None:
            with self._lock:
                for s in self._sessions.values():
                    s.rate_ms = max(100, int(rate_ms))
        return changed

    def snapshot(self):
        with self._lock:
            sessions = [s.snapshot() for s in self._sessions.values()]
        warning = None
        if not self.auth_helper:
            warning = "未配置 MFi 认证 helper：链路与识别可用，认证不会通过"
        else:
            import os
            cert, key = (os.environ.get("ACCESSORY_MFI_CERT_FILE"),
                         os.environ.get("ACCESSORY_MFI_KEY_FILE"))
            if not cert or not key or not os.path.exists(cert) or not os.path.exists(key):
                warning = ("未设置 ACCESSORY_MFI_CERT_FILE / ACCESSORY_MFI_KEY_FILE："
                           "链路与识别可用，认证不会通过，手机会忽略定位数据")
        return {
            "warning": warning,
            "identity": self.config.profile() if self.config else None,
            "target": self.target.snapshot(),
            "sessions": sessions,
        }

    def shutdown(self):
        self.stop_all()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
