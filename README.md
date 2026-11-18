# ESP32-S3 BLE KVM

一套键鼠，通过 ESP32-S3（BLE HID）控制多台设备（Windows 台式机 / 笔记本 / Android 手机），
附带摄像头"有人/无人"检测（无人自动锁屏）和一键远程关机。

> 不是 USB KVM 切换器，而是**软件 KVM**：台式机上的主程序捕获键鼠，经串口发给 ESP32-S3，
> 由 ESP32-S3 以蓝牙 HID 键盘/鼠标的身份转发给目标设备。

---

## 功能

| 功能 | 说明 |
|---|---|
| **键鼠切换** | Ctrl+Alt+S 在本地 / 远程设备之间切换 |
| **键鼠转发** | 本地键鼠 → ESP32-S3 → BLE HID → 目标设备 |
| **远程关机** | Ctrl+Alt+End → 目标设备执行关机（带 ACK 回执） |
| **无人检测** | 摄像头识别有人/无人，无人一段时间后自动锁屏 |
| **GUI 控制台** | PySide6 托盘程序，参数配置、状态显示、一键操作 |
| **多平台** | Windows（已验证）/ Android（HID 直连）/ macOS（理论支持） |

---

## 架构

```
┌─────────────┐   USB 串口    ┌────────────┐   BLE HID    ┌──────────────┐
│  Windows    │◄────────────►│ ESP32-S3   │◄────────────►│ 笔记本 / 手机 │
│  KvmBridge  │  AA 帧协议    │ BLE KVM 板 │              │ (HID Host)   │
└─────────────┘              └────────────┘              └──────────────┘
      ▲                            ▲
      │ 键盘钩子/鼠标钩子            │ UART
┌─────────────┐              ┌────────────┐
│ 物理键鼠     │              │ 摄像头板    │
└─────────────┘              │ (有人检测)  │
                             └────────────┘
```

- **KvmBridge**（台式机）：全局键盘/鼠标钩子，捕获事件 → 串口 AA 帧 → ESP32-S3
- **ESP32-S3**：BLE HID 键盘+鼠标，把串口帧转成 BLE HID 报告
- **摄像头板**（可选）：ESP32-S3 + 摄像头，跑 presence 二分类模型，串口输出 `CMD:PERSON/ABSENT`
- **KVMConsole**（UI）：PySide6 托盘程序，读写 config.json、启停各组件

---

## 目录结构

```
.
├── firmware/                 ESP32-S3 KVM 板固件（ESP-IDF）
│   ├── CMakeLists.txt
│   ├── sdkconfig.defaults
│   └── main/
│       ├── esp_hid_device_main.c    HID 主逻辑 + 串口协议
│       └── esp_hid_gap.c            广播/连接管理
├── host/                     台式机端 Python
│   ├── kvm_bridge.py         键鼠钩子 + 串口桥（核心）
│   ├── cam_presence.py       摄像头无人检测 → 锁屏
│   ├── laptop_agent.py       目标端（笔记本）常驻代理
│   └── cam_capture.py        摄像头抓帧工具
├── ui/
│   └── main.py               PySide6 控制台（托盘）
└── docs/
    └── protocol.md           串口协议说明
```

---

## 硬件

| 部件 | 型号 | 说明 |
|---|---|---|
| KVM 板 | ESP32-S3（N8R8 或 N4R2） | USB Serial/JTAG 接台式机，BLE 连目标 |
| 摄像头板 | YAHBOOM ESP32-S3 图传 Lite | DVP 摄像头，跑 presence 模型（可选） |

KVM 板通过 USB 接台式机（CDC 串口），同时以 BLE HID 身份被目标设备连接。

---

## 快速开始

### 1. 烧录固件

```bash
# 需要 ESP-IDF v5.x
cd firmware
idf.py set-target esp32s3
idf.py build
idf.py -p COM4 flash monitor
```

### 2. 运行主机桥

```bash
cd host
pip install pyserial
python kvm_bridge.py --port COM4
```

启动后：
- 按 **Ctrl+Alt+S** 切换控制权
- 按 **Ctrl+Alt+End** 远程关机
- 按 **Ctrl+Alt+Q** 强制回本地（逃生）

### 3. 目标设备配对

- 笔记本 / 手机：蓝牙设置里配对 `ESP Keyboard`
- 配对后目标设备把它当键盘/鼠标

### 4. UI 控制台（可选）

```bash
cd ui
pip install PySide6 pyserial
python main.py
```

或自行用 PyInstaller 打包：

```bash
pyinstaller --onefile --noconsole --name KVMConsole \
  --collect-all PySide6 --hidden-import serial main.py
```

### 5. 无人检测（可选）

```bash
# 摄像头板烧录 presence 固件后
python cam_presence.py --port COM10 --absent-sec 5 --idle-sec 30
```

---

## 串口协议

完整说明见 [docs/protocol.md](docs/protocol.md)。速览：

| 方向 | 帧 | 含义 |
|---|---|---|
| 主机 → 板 | `AA 01 <mod> 00 <k1..k5>` | 键盘报告（HID boot 格式） |
| 主机 → 板 | `AA 02 <buttons> <dx> <dy>` | 鼠标报告 |
| 主机 → 板 | `AA 05 <slot>` | 切换目标连接 |
| 主机 → 板 | `AA 07 01` | 远程关机命令 |
| 板 → 主机 | `AA 06 <slot> <conn>` | 切换 ACK（含连接数） |
| 板 → 主机 | `AA 03 01 <val>` | 命令 ACK（0x10=OK） |
| 板 → 主机 | `AA 08 <rid> <val>` | HID LED 状态（仅日志） |

---

## 踩过的坑（重要）

| 坑 | 现象 | 根因 |
|---|---|---|
| 广播秒停 | 扫不到设备 | `ble_gap_adv_start` 的 duration 写 0（NimBLE 里 0=立即停） |
| 键鼠全哑 | HID 连上但无输入 | 新增 NOTIFY 特征导致 CCCD 超限（`CONFIG_BT_NIMBLE_MAX_CCCDS`） |
| 用着用着切回本地 | 键盘灯变化时自动切回 | LED Output 帧与 BOOT 键同用 `AA 03` |
| 热键从不触发 | Ctrl+Alt+S 无反应 | `RegisterHotKey` 只声明了 argtypes 从未调用 |
| 关屏不生效 | 远程关机后屏幕不灭 | 服务/Session0 下钩子失效，必须用户会话运行 |
| 并行编译卡死 | idf.py build 卡在 852/1131 | 设 `CMAKE_BUILD_PARALLEL_LEVEL=1` 单线程可解 |

---

## 已知限制

- **Android 无法通过 HID 触发息屏**：系统拦截键盘的 Power/Sleep 键（安全设计），只能装 App（DeviceAdmin/无障碍）实现
- **Windows 钩子需要用户会话**：不能作为 Windows 服务运行（Session 0 下钩子失效）
- **macOS/iOS 未验证**：理论上支持标准 HID，但 Apple 对 bonding 要求更严

---

## License

MIT
