"""Transports: USB (pymobiledevice3, macOS-first) and TCP (test / Wi-Fi tunnel).

The iAP2 Link in app.iap2.link expects an async stream with
``recvall(n) / sendall(bytes) / close()`` -- exactly what
pymobiledevice3's lockdown service object provides, which is why
accessory-location passes it straight in. UsbStream is therefore a
thin adapter: if the service already has those methods it is used
as-is; otherwise we adapt its sync recv/send.

TCP exists for two reasons, both taken from iap2loc:
1. a loopback self-test without an iPhone (tests/test_app.py runs a
   fake iAP2 peer on 127.0.0.1 and checks GGA/RMC arrive);
2. the Wi-Fi-tunnel shape iap2loc documents (point it at a tunnel
   relay instead of a phone).

No transport is imported at module load: pymobiledevice3 is only
needed on a Mac with a phone attached, so `python -m app.main serve`
works on any machine with stdlib only.
"""
import asyncio
import inspect
import threading


class TcpStream:
    """asyncio stream wrapper with the Link interface."""

    def __init__(self, reader, writer):
        self._reader, self._writer = reader, writer

    @classmethod
    async def connect(cls, host, port, timeout=10):
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout)
        return cls(reader, writer)

    async def recvall(self, size):
        return await self._reader.readexactly(size)

    async def sendall(self, data):
        self._writer.write(data)
        await self._writer.drain()

    async def close(self):
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            pass


class UsbStream:
    """Adapter around a pymobiledevice3 lockdown service."""

    def __init__(self, service):
        self._service = service

    async def recvall(self, size):
        if hasattr(self._service, "recvall"):
            return await self._service.recvall(size)
        # Sync recv() fallback, run off the loop.
        chunks, got = [], 0
        while got < size:
            chunk = await asyncio.to_thread(self._service.recv, size - got)
            if not chunk:
                raise EOFError("USB stream closed")
            chunks.append(chunk)
            got += len(chunk)
        return b"".join(chunks)

    async def sendall(self, data):
        if hasattr(self._service, "sendall"):
            return await self._service.sendall(data)
        await asyncio.to_thread(self._service.send, data)

    async def close(self):
        close = getattr(self._service, "close", None)
        if close is None:
            return
        result = close()
        if asyncio.iscoroutine(result):
            await result


def _await_maybe(result):
    """Await a value if it is a coroutine, otherwise return it as-is.

    pymobiledevice3's ``usbmux.list_devices`` is async in current
    versions (sync in older ones). Calling it without awaiting used
    to return a coroutine object, which then failed with
    ``TypeError: 'coroutine' object is not iterable`` in the caller
    and left a "coroutine was never awaited" warning. The Web server
    runs in a plain thread with no event loop, so ``asyncio.run``
    is the normal path; if a loop is already running in this thread,
    run the coroutine on a fresh thread instead.
    """
    if not inspect.iscoroutine(result):
        return result
    try:
        return asyncio.run(result)
    except RuntimeError:
        box, err = {}, {}

        def _target():
            try:
                box["value"] = asyncio.run(result)
            except Exception as exc:  # noqa: BLE001 - re-raised below
                err["error"] = exc

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join()
        if err:
            raise err["error"]
        return box.get("value")


def list_usb_devices():
    """Best-effort USB iPhone enumeration via pymobiledevice3/usbmux.

    Returns a list of dicts {udid, ...} or [] when pymobiledevice3 is
    absent / no device is attached. Never raises: the Web UI shows
    "no USB device" instead of crashing the server.
    Raises nothing; errors are returned as {"error": ...} in the caller.
    """
    try:
        from pymobiledevice3.usbmux import list_devices
    except Exception as exc:  # pymobiledevice3 not installed (e.g. Linux CI)
        return [], f"pymobiledevice3 unavailable: {exc}"
    try:
        devices = _await_maybe(list_devices())
    except Exception as exc:
        return [], f"usbmux list failed: {exc}"
    out = []
    for d in devices or []:
        # pymobiledevice3 returns objects/dicts depending on version.
        udid = getattr(d, "udid", None) or (d.get("udid") if isinstance(d, dict) else None)
        serial = getattr(d, "serial", None) or (d.get("serial") if isinstance(d, dict) else None)
        out.append({"udid": udid or serial or "unknown",
                    "serial": serial or "", "transport": "usb"})
    return out, None


async def open_usb_stream(udid=None):
    """Open com.apple.carkit.service over USB to the (first) trusted iPhone.

    Mirrors accessory-location cli.py: create_using_usbmux(USB) +
    start_lockdown_service("com.apple.carkit.service"). The phone must
    be unlocked and already trust this Mac.
    """
    from pymobiledevice3.lockdown import create_using_usbmux

    kwargs = {"connection_type": "USB", "autopair": False}
    if udid:
        kwargs["udid"] = udid
    phone = await create_using_usbmux(**kwargs)
    service = await phone.start_lockdown_service("com.apple.carkit.service")
    return phone, UsbStream(service)
