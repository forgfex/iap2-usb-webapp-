#!/bin/bash
# macOS 一键安装: Python 3.11+ + uv + pymobiledevice3/cryptography + 自测
# 用法 (在 Mac 终端, 项目根目录):
#   bash scripts/setup-macos.sh
# 不下载、不安装任何 MFi 身份; 身份由你自己准备, 见 README.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(uname -s)" != "Darwin" ]; then
  echo "这个脚本只在 macOS 上跑 (当前: $(uname -s))" >&2
  exit 2
fi

# 1. Python 3.11+
if ! command -v python3 >/dev/null; then
  echo "没找到 python3: 请先装 Xcode Command Line Tools: xcode-select --install" >&2
  exit 1
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Python: $PY_VER"
python3 -c 'import sys; assert sys.version_info >= (3, 11), "需要 Python 3.11+"' \
  || { echo "Python 版本太低, 需要 3.11+ (建议用 python.org 或 brew 装 3.12/3.13)" >&2; exit 1; }

# 2. uv (没有就装; 已有就直接用)
if ! command -v uv >/dev/null; then
  echo "正在安装 uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "uv: $(uv --version)"

# 3. 建虚拟环境 + 装依赖 (只装 pymobiledevice3 + cryptography)
uv sync
echo "依赖已装到 .venv"

# 4. 自测 (不需要 iPhone, 不需要身份)
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3

# 5. 身份检查: 只检查文件在不在, 不读内容、不上传
if [ -z "${ACCESSORY_MFI_CERT_FILE:-}" ] || [ -z "${ACCESSORY_MFI_KEY_FILE:-}" ]; then
  echo ""
  echo "提醒: 还没设 MFi 身份环境变量, 现在只能跑到识别, 认证不会通过:"
  echo "  export ACCESSORY_MFI_CERT_FILE=/path/to/certificate.p7b"
  echo "  export ACCESSORY_MFI_KEY_FILE=/path/to/identity.pk8"
  echo "  (身份放仓库外面, 不要进 Git)"
else
  [ -f "$ACCESSORY_MFI_CERT_FILE" ] || { echo "证书文件不存在: $ACCESSORY_MFI_CERT_FILE" >&2; exit 1; }
  [ -f "$ACCESSORY_MFI_KEY_FILE" ] || { echo "私钥文件不存在: $ACCESSORY_MFI_KEY_FILE" >&2; exit 1; }
  echo "MFi 身份文件已找到 (只校验存在, 不打印内容)"
fi

echo ""
echo "安装完成, 启动:"
echo "  bash scripts/run-macos.sh"
