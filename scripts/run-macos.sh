#!/bin/bash
# macOS 一键启动 Web 控制台: http://127.0.0.1:8737/
# 用法:
#   bash scripts/run-macos.sh
#   bash scripts/run-macos.sh --lat 31.2304 --lon 121.4737 --port 8737
# 启动前先把 iPhone 用数据线连上、解锁、点"信任此电脑"。
# 退出按 Ctrl+C, 再拔线, 确认手机恢复真实定位。
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(uname -s)" != "Darwin" ]; then
  echo "这个脚本只在 macOS 上跑 (当前: $(uname -s))" >&2
  exit 2
fi
if [ ! -x .venv/bin/python ]; then
  echo "还没安装, 先跑: bash scripts/setup-macos.sh" >&2
  exit 1
fi
if [ -z "${ACCESSORY_MFI_CERT_FILE:-}" ] || [ -z "${ACCESSORY_MFI_KEY_FILE:-}" ]; then
  echo "提醒: 未设 MFi 身份, 认证不会通过, 手机会忽略定位 (先跑识别/日志没问题)"
  echo "  export ACCESSORY_MFI_CERT_FILE=/path/to/certificate.p7b"
  echo "  export ACCESSORY_MFI_KEY_FILE=/path/to/identity.pk8"
fi
exec .venv/bin/python -m app.main serve "$@"
