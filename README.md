# iAP2 USB 定位 + Web 选点

把两个开源项目融合成一个 App：**USB 有线连 iPhone + 网页地图选点**。

| | aur3l14no/accessory-location | D1ngtalk/iap2loc | 本项目 |
|---|---|---|---|
| 语言 / 平台 | Python，macOS | C# / .NET 10，Windows（+ macOS 蓝牙移植） | **Python 3.11+，macOS 为主** |
| 传输 | **USB**：`pymobiledevice3` + `com.apple.carkit.service` | 蓝牙 RFCOMM 为主，TCP 隧道 | **USB（取 accessory-location），TCP 保留作自测/隧道** |
| 协议 | iAP2 link（stop-and-wait，4096 字节）、`0x1D/0xAA/0xFFFA` | iAP2 link（重传队列、乱序重组，65535 字节） | **accessory-location 的 link/wire/auth 原样复用**，不重写协议 |
| 认证 | MFi-v3 helper 子进程（JSONL），密钥不出 helper | 进程内 `MfiSigner`（ECDSA P-256 预哈希） | **取 accessory-location 的 helper 隔离设计** |
| 选点 | 命令行 `--latitude/--longitude`，启动后固定 | **Web 控制台 `localhost:8737`、Leaflet 地图、OSM/高德、GCJ-02 换算、收藏、多设备、实时改点** | **取 iap2loc 的 Web + 目标模型，移植到 Python** |
| 目标逻辑 | 固定静止点，1 秒上报 | 共享目标、随机偏移/精度只掷一次、>100 m 跳变帧 250 m/s | **取 iap2loc 的 LocationTarget** |
| 身份轮换 | 固定 `AL-LOCAL-001` | 每次启动换 serial/name | **取 iap2loc：每次启动换 serial** |

结论：USB 这条路只有 accessory-location 有现成实现，Web 这套只有 iap2loc
有现成实现；C# 里没有 USB 实现，Python 里没有 Web 实现。所以融合方向是
**Python + accessory-location 的 USB 引擎 + iap2loc 的 Web/目标模型**，
而不是把 USB 移植到 C#。

## 架构

```
浏览器 (Leaflet 地图, OSM / 高德, WGS-84 <-> GCJ-02)
  │  http://127.0.0.1:8737  (仅本机, stdlib http.server, 无第三方依赖)
  ▼
app/web.py  ── /api/state /api/target /api/sessions /api/devices
              /api/favorites /api/log
  ▼
app/session.py  SessionManager ── 一个共享 LocationTarget, 每台 iPhone 一个会话
  │   app/target.py (iap2loc: jitter 只掷一次, 跳变 250 m/s 一帧)
  │   app/nmea.py   (GGA+RMC, 高度/速度/航向/精度/质量/卫星数)
  ▼
app/iap2/  wire.py link.py auth.py ── accessory-location 原样复用 (GPL-3.0)
           profile.py ── 身份 0x1D01 (字段可配) + 定位 0xFFFB
  ▼
app/transport.py  USB: pymobiledevice3 / usbmux → com.apple.carkit.service
                  TCP: asyncio (本机自测 / Wi-Fi 隧道)
  ▼
iPhone (已解锁, 已信任此电脑) ── 0x1D00→0x1D01, 0xAA00→0xAA01,
  0xAA02→0xAA03, 0xAA05 后 0xFFFA 订阅, 按 0xFFFB 收 GGA/RMC
```

关键流程（两个上游都一样，缺一不可）：
`0x1D02 IdentificationAccepted` → `0xAA05 AuthenticationSucceeded` →
`0xFFFA StartLocationInformation` → 每 1 秒发 GGA+RMC。
**ACK 只证明送达，不证明手机采纳了定位——必须看手机地图的蓝点。**

## 文件

```
app/main.py            CLI: serve / devices / run
app/web.py             本地 Web 服务 + JSON API (对齐 iap2loc 的接口形状)
app/web/index.html     地图控制台 (改编自 iap2loc Web/index.html, 传输改为 USB/TCP)
app/session.py         会话管理 + 每设备 asyncio 任务
app/target.py          共享目标 (iap2loc LocationTarget 移植)
app/nmea.py            GGA/RMC 编码 (两项目合并)
app/geo.py             距离/航向/偏移 + WGS-84<->GCJ-02
app/config.py          iap2-usb-webapp.json: 配件身份 + 收藏点, 启动轮换 serial
app/transport.py       USB / TCP 传输
app/iap2/wire.py       iAP2 成帧 (accessory-location 原样)
app/iap2/link.py       iAP2 链路 (accessory-location 原样)
app/iap2/auth.py       认证 provider 边界 (accessory-location 原样)
app/iap2/profile.py    身份 + 定位消息 (accessory-location 基础, 字段/NMEA 扩展)
tools/local_mfi_v3_helper.py   MFi-v3 helper (accessory-location 原样)
tests/test_app.py      11 个测试, 含本机回环假 iAP2 对端
upstream/              两个上游仓库的只读副本 (参考用, 不参与运行)
```

## 运行（macOS，真机）

```sh
# 0. 一键安装 (Mac 终端, 项目根目录; 会装 uv + 依赖, 并跑 11 个自测)
bash scripts/setup-macos.sh
#    启动:
bash scripts/run-macos.sh
# 手动装也可以:
pip install "pymobiledevice3>=9.40,<10" "cryptography>=43"
# 2. MFi 身份 (仓库不含任何身份材料, 必须自备合法来源的)
#    identity.pk8 (DER PKCS#8 P-256 私钥) + certificate.p7b (DER PKCS#7 证书链)
export ACCESSORY_MFI_CERT_FILE=/path/to/certificate.p7b
export ACCESSORY_MFI_KEY_FILE=/path/to/identity.pk8
#    检查格式 (不含身份也能跑, 但认证不会通过):
#    python3 tools/local_mfi_v3_helper.py  # 按 JSONL 协议自测, 见上游 docs/authentication.md
# 3. 启动
python3 -m app.main serve --port 8737
#    浏览器自动打开 http://127.0.0.1:8737/
# 4. iPhone 用数据线连接, 解锁, 点"信任此电脑"
# 5. 网页: 点地图选点 → 应用修改 → 设备下拉选 iPhone → 添加
#    看日志依次出现 0x1D02 / 0xAA05 / 0xFFFA, 再打开手机地图确认蓝点移动
# 6. 定位没变化时 (两上游同款操作):
#    关 Wi-Fi/流量/定位服务 10 秒 → 只开定位服务 → 打开地图确认
#    → 再开 Wi-Fi, 稳定后再开流量
# 7. 结束: 网页点"停止全部", 拔线, 确认手机恢复真实定位
```

单会话 / 无界面：

```sh
python3 -m app.main devices
python3 -m app.main run --transport usb --lat 31.2304 --lon 121.4737 --duration 180
```

## 本机验证（已跑过，无真机）

```sh
python3 -m unittest discover -s tests -v   # 11 passed
```

- NMEA：上海 31.2304,121.4737 → `3113.8240,N` / `12128.4220,E`，校验和逐句重算通过。
- 目标：随机偏移只掷一次；>100 m 跳变第一帧速度 250 m/s、带航向，下一帧恢复静止。
- 回环：`tests/test_app.py` 起了一个最小假 iAP2 对端（SYN+ACK → 0x1D00 →
  0x1D02/0xAA05/0xFFFA），真 `DeviceSession` 经 TCP 收到订阅后发出了含目标坐标的
  GGA+RMC。
- Web：`serve` 启动后 `/`、`/api/state`、`/api/devices`（本机无 usbmux 时返回
  `[]`，不崩）、`POST /api/target`（上海→北京生效）、`/api/log` 均已用 curl 验过。

**未验证的**：真机 USB 握手、MFi 认证、iOS 是否采纳定位、Windows USB。
这几项只能在 macOS + 已信任的 iPhone + 合法 MFi 身份上验证，见上文第 4–6 步。

## MFi 身份与合规

- 本仓库**不包含、不生成、不下载任何证书/私钥**。accessory-location 的
  DiPlay 导入器也没有复制进来——如需用它，请自行去上游按其 provenance
  说明评估，密钥放仓库外，权限 0600，不要进 Git。
- 认证走独立 helper 子进程：私钥只在 helper 里，`certificate.p7b` /
  `identity.pk8` 的路径用环境变量传，不进日志、不进配置文件。
- 仅用于**自有设备、协议研究与隐私测试**。两上游 README 都写明：把定位
  伪造用于考勤、网约车、保险里程、游戏等可能构成欺诈或违约；来源不明的
  MFi 身份可能违反 Apple 授权条款。
- 许可证：两上游均为 **GPL-3.0-only**，融合代码同样 GPL-3.0-only，
  见 `LICENSE`；分发时附许可证与源码获取方式。

## 已知限制

- USB 是 macOS 优先：`pymobiledevice3` + usbmux 在 macOS 上最成熟；
  Windows 上 iap2loc 原本就没有 USB，只有蓝牙/TCP，本项目也不承诺 Windows USB。
- 上报周期默认 1000 ms（accessory-location 下限 1 秒）；Web 里可调到 100 ms，
  但 iOS 在 `0xFFFA` 里指定的 `0x8001/0x8002` 间隔优先。
- 定位最终由 iOS 融合：配件只是其中一个来源，Wi-Fi/基站/其他 Apple 设备
  的缓存定位都会参与，远距离改点要先按上文第 6 步切断这些来源。
- 蓝牙传输没有移植：accessory-location 的蓝牙桥是实验性的，iap2loc 的是
  Windows/macOS 原生实现，两者都不在本次 USB+Web 的目标里；如需蓝牙，
  直接用 iap2loc 原项目。
