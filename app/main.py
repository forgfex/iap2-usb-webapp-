"""CLI entry point.

Usage (macOS, with a trusted iPhone on USB):
  export ACCESSORY_MFI_CERT_FILE=/path/to/certificate.p7b
  export ACCESSORY_MFI_KEY_FILE=/path/to/identity.pk8
  python3 -m app.main serve --port 8737
  # -> open http://127.0.0.1:8737/ , pick a point on the map, add the USB device

  python3 -m app.main devices          # list USB iPhones via usbmux
  python3 -m app.main run --transport tcp --host 127.0.0.1 --port 12345 \
      --lat 31.2304 --lon 121.4737      # single session, no Web UI

Without --auth-helper / identity files the link and identification
still work, but iOS will not accept location from an unauthenticated
accessory -- the Web console shows this as a warning, and the log
shows 0xAA00 with no 0xAA05. No identity material is bundled.
"""
import argparse
import asyncio
import json
import os
import sys
import webbrowser
from pathlib import Path

from .config import AppConfig
from .session import HELPER_DEFAULT, SessionManager
from .target import LocationTarget
from .web import WebServer

ROOT = Path(__file__).resolve().parent.parent


def _helper_path(explicit):
    if explicit:
        return explicit
    candidate = ROOT / HELPER_DEFAULT
    return str(candidate) if candidate.exists() else None


def _serve(args):
    config = AppConfig(args.config)
    target = LocationTarget(args.lat, args.lon)
    manager = SessionManager(target, config,
                             auth_helper=_helper_path(args.auth_helper))
    manager.log.add("info", "iAP2 USB 定位控制台已启动（仅监听本机）")
    if manager.auth_helper:
        manager.log.add("info", f"MFi 认证 helper：{manager.auth_helper}（密钥仍在 helper 进程内，"
                                "不在本程序内存里）")
    else:
        manager.log.add("warn", "未找到认证 helper：认证不会通过，见 README 的 MFi 一节")
    server = WebServer(manager, config, port=args.port).start()
    url = f"http://127.0.0.1:{server.port}/"
    print(f"控制页面：{url}")
    print("按 Ctrl+C 退出；断开 USB 后请确认手机恢复真实定位。")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        import time
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n正在停止全部会话…")
    finally:
        manager.shutdown()
        server.stop()
    return 0


def _devices(_args):
    from .transport import list_usb_devices
    devices, error = list_usb_devices()
    if error:
        print(f"USB 枚举不可用：{error}")
        print("（macOS 上安装 pymobiledevice3 后重试；Linux 本机无 usbmux 时属正常）")
    if not devices:
        print("未发现 USB iPhone。")
        return 0
    for d in devices:
        print(json.dumps(d, ensure_ascii=False))
    return 0


async def _run_once(args):
    """Single-session CLI, for scripts / probe. Mirrors iap2loc `run`."""
    config = AppConfig(args.config, rotate=False)
    target = LocationTarget(args.lat, args.lon)
    manager = SessionManager(target, config,
                             auth_helper=_helper_path(args.auth_helper))
    address = args.udid or (f"{args.host}:{args.port}" if args.transport == "tcp" else "")
    session = manager.add(args.transport, address, label="cli",
                          rate_ms=args.rate)
    try:
        await asyncio.sleep(args.duration or 3600)
    except asyncio.CancelledError:
        pass
    finally:
        manager.shutdown()
    print(json.dumps(session.snapshot(), ensure_ascii=False))
    return 0 if session.error is None else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="iap2-usb-webapp",
                                     description="USB iAP2 定位 + Web 选点")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="启动 Web 控制台（默认）")
    p.add_argument("--port", type=int, default=8737)
    p.add_argument("--lat", type=float, default=31.2304)
    p.add_argument("--lon", type=float, default=121.4737)
    p.add_argument("--config", default=None)
    p.add_argument("--auth-helper", default=None,
                   help="MFi-v3 JSONL helper，默认 tools/local_mfi_v3_helper.py")
    p.add_argument("--no-browser", action="store_true")

    p = sub.add_parser("devices", help="列出 USB iPhone")
    p.add_argument("--config", default=None)

    p = sub.add_parser("run", help="单会话（无 Web 界面），用于脚本/自测")
    p.add_argument("--transport", choices=["usb", "tcp"], default="usb")
    p.add_argument("--udid", default=None)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--lat", type=float, default=31.2304)
    p.add_argument("--lon", type=float, default=121.4737)
    p.add_argument("--rate", type=int, default=1000)
    p.add_argument("--duration", type=int, default=0)
    p.add_argument("--config", default=None)
    p.add_argument("--auth-helper", default=None)

    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    if args.command == "devices":
        return _devices(args)
    if args.command == "run":
        return asyncio.run(_run_once(args))
    return 2


if __name__ == "__main__":
    sys.exit(main())
