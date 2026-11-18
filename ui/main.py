# -*- coding: utf-8 -*-
"""KVM 控制台 V1

- 深色界面：状态卡 + 参数页 + 热键录制 + 日志
- 参数存 config.json（改完即存）
- 托盘常驻
"""
import ctypes
import json
import os
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGroupBox, QSystemTrayIcon, QMenu, QTabWidget,
    QSlider, QSpinBox, QComboBox, QKeySequenceEdit, QFormLayout,
    QCheckBox, QTextEdit, QLineEdit,
)

BASE = os.path.dirname(os.path.abspath(sys.argv[0]))
CFG = os.path.join(BASE, "config.json")
LOG = os.path.join(os.environ.get("APPDATA", ""), "KvmBridge", "bridge_log.txt")

DEFAULT = {
    "kvm": {"port": "COM4", "hotkey_toggle": "Ctrl+Alt+S", "hotkey_shutdown": "Ctrl+Alt+End"},
    "presence": {"port": "COM10", "idle_sec": 30, "enabled": True},
    "cam_exe": r"D:\workbuddy_file\2026-10-19-14-03-29\esp32s3_ble_kvm\host\dist\CamPresence.exe",
    "ui": {"theme": "dark"},
}


def load_cfg():
    try:
        with open(CFG, "r", encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        c = {}
    for k, v in DEFAULT.items():
        if isinstance(v, dict):
            c.setdefault(k, {})
            for kk, vv in v.items():
                c[k].setdefault(kk, vv)
        else:
            c.setdefault(k, v)
    return c


def save_cfg(c):
    try:
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


QSS = """
QMainWindow, QWidget { background:#1e1f22; color:#dcdcdc; font-family:"Microsoft YaHei"; }
QLabel#title { font-size:19px; font-weight:bold; color:#fff; }
QLabel#state { color:#8ab4f8; font-size:13px; }
QGroupBox { border:1px solid #35383d; border-radius:8px; margin-top:12px; padding-top:12px; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 4px; color:#8ab4f8; }
QPushButton { background:#35383d; border:none; border-radius:6px; padding:7px 14px; color:#ddd; }
QPushButton:hover { background:#45484d; }
QPushButton#danger { background:#8f3333; color:#fff; }
QPushButton#danger:hover { background:#a94444; }
QSlider::groove:horizontal { height:4px; background:#35383d; border-radius:2px; }
QSlider::handle:horizontal { background:#8ab4f8; width:14px; margin:-5px 0; border-radius:7px; }
QTabWidget::pane { border:1px solid #35383d; border-radius:6px; }
QTabBar::tab { background:#26282b; padding:7px 16px; border-top-left-radius:6px; border-top-right-radius:6px; }
QTabBar::tab:selected { background:#35383d; color:#8ab4f8; }
QKeySequenceEdit, QSpinBox, QComboBox, QLineEdit { background:#26282b; border:1px solid #35383d;
    border-radius:4px; padding:4px; color:#ddd; }
QTextEdit { background:#16171a; border:1px solid #35383d; border-radius:4px; color:#9aa; font-family:Consolas; }
QLabel#hint { color:#777; font-size:11px; }
"""

user32 = ctypes.windll.user32
VK_CONTROL, VK_MENU = 0x11, 0x12
KEYUP = 0x0002

QT_KEY_TO_VK = {
    Qt.Key_S: 0x53, Qt.Key_A: 0x41, Qt.Key_D: 0x44, Qt.Key_F: 0x46,
    Qt.Key_Q: 0x51, Qt.Key_W: 0x57, Qt.Key_E: 0x45, Qt.Key_R: 0x52,
    Qt.Key_End: 0x23, Qt.Key_Home: 0x24, Qt.Key_F9: 0x78,
}


def press_vk(vk):
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYUP, 0)
    user32.keybd_event(VK_MENU, 0, KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)


def last_log(n=12):
    if not os.path.exists(LOG):
        return "（暂无日志）"
    try:
        with open(LOG, "r", encoding="utf-8", errors="ignore") as f:
            return "".join(f.readlines()[-n:])
    except Exception as e:
        return "读取失败: %s" % e


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        self.cam_proc = None
        self.setWindowTitle("KVM 控制台")
        self.resize(560, 620)
        self.setStyleSheet(QSS)

        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(16, 16, 16, 16)

        title = QLabel("ESP32-S3 KVM 控制台")
        title.setObjectName("title")
        v.addWidget(title)

        self.state = QLabel("状态：本地 · 等待连接")
        self.state.setObjectName("state")
        v.addWidget(self.state)

        # 控制按钮
        box = QGroupBox("控制")
        h = QHBoxLayout(box)
        b1 = QPushButton("切换控制权")
        b1.clicked.connect(lambda: press_vk(QT_KEY_TO_VK[Qt.Key_S]))
        b2 = QPushButton("关机笔记本")
        b2.setObjectName("danger")
        b2.clicked.connect(lambda: press_vk(QT_KEY_TO_VK[Qt.Key_End]))
        h.addWidget(b1)
        h.addWidget(b2)
        v.addWidget(box)

        # 参数标签页
        tabs = QTabWidget()
        tabs.addTab(self._tab_kvm(), "KVM")
        tabs.addTab(self._tab_presence(), "presence")
        tabs.addTab(self._tab_general(), "通用")
        v.addWidget(tabs, 1)

        # 日志
        logbox = QGroupBox("最近事件")
        lv = QVBoxLayout(logbox)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(120)
        self.log.setPlainText(last_log())
        lv.addWidget(self.log)
        v.addWidget(logbox)

        # 底部操作区：保存 / 恢复默认
        btn_row = QHBoxLayout()
        b_save = QPushButton("保存设置")
        b_save.setStyleSheet("background:#2d6a4f;color:#fff;padding:8px 22px;font-weight:bold;border-radius:6px;")
        b_save.clicked.connect(self._save_all)
        b_reset = QPushButton("恢复默认")
        b_reset.setStyleSheet("background:#4a3a2a;color:#ddd;padding:8px 16px;border-radius:6px;")
        b_reset.clicked.connect(self._reset_defaults)
        btn_row.addWidget(b_save)
        btn_row.addWidget(b_reset)
        btn_row.addStretch()
        v.addLayout(btn_row)

        hint = QLabel("提示：本程序通过模拟热键控制 KvmBridge，不占用串口")
        hint.setObjectName("hint")
        v.addWidget(hint)

        # 托盘
        self.tray = QSystemTrayIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon), self)
        self.tray.setToolTip("KVM 控制台")
        menu = QMenu()
        a_show = QAction("显示窗口", self)
        a_show.triggered.connect(self.showNormal)
        a_quit = QAction("退出", self)
        a_quit.triggered.connect(QApplication.quit)
        menu.addAction(a_show)
        menu.addAction(a_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(2000)

        # UI 启动时同步摄像头检测按钮状态（可能在 UI 重启前就已经在跑）
        try:
            if self._cam_running():
                self.cam_switch.setText("停止摄像头检测")
            elif self.cfg["presence"]["enabled"]:
                # 配置里"无人检测"是开着的 → 开机自动拉起
                self._start_cam()
        except Exception:
            pass

    # ---------- 页面 ----------
    def _tab_kvm(self):
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(14, 14, 14, 14)

        self.kvm_port = QLineEdit(self.cfg["kvm"]["port"])
        self.kvm_port.editingFinished.connect(self._save_kvm)
        f.addRow("串口", self.kvm_port)

        self.hk_toggle = QKeySequenceEdit()
        self.hk_toggle.setKeySequence(QKeySequence(self.cfg["kvm"]["hotkey_toggle"]))
        f.addRow("切换热键", self.hk_toggle)

        self.hk_shutdown = QKeySequenceEdit()
        self.hk_shutdown.setKeySequence(QKeySequence(self.cfg["kvm"]["hotkey_shutdown"]))
        f.addRow("关机笔记本热键", self.hk_shutdown)

        btn = QPushButton("保存 KVM 参数")
        btn.clicked.connect(self._save_kvm)
        f.addRow(btn)
        return w

    def _tab_presence(self):
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(14, 14, 14, 14)

        self.pr_enable = QCheckBox("启用无人检测")
        self.pr_enable.setChecked(bool(self.cfg["presence"]["enabled"]))
        f.addRow(self.pr_enable)

        self.idle_spin = QSpinBox()
        self.idle_spin.setRange(5, 3600)
        self.idle_spin.setSuffix(" 秒")
        self.idle_spin.setValue(int(self.cfg["presence"]["idle_sec"]))
        f.addRow("无人息屏时间", self.idle_spin)

        self.pr_port = QLineEdit(self.cfg["presence"]["port"])
        f.addRow("摄像头串口", self.pr_port)

        btn = QPushButton("保存 presence 参数")
        btn.clicked.connect(self._save_presence)
        f.addRow(btn)

        self.cam_switch = QPushButton("启动摄像头检测")
        self.cam_switch.clicked.connect(self._toggle_cam)
        f.addRow(self.cam_switch)
        return w

    def _tab_general(self):
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(14, 14, 14, 14)
        self.autostart = QCheckBox("开机自启 KvmBridge")
        self.autostart.setChecked(True)
        f.addRow(self.autostart)
        hint = QLabel("（自启功能下一版接入）")
        hint.setObjectName("hint")
        f.addRow(hint)
        return w

    # ---------- 保存 ----------
    def _save_kvm(self):
        self.cfg["kvm"]["port"] = self.kvm_port.text().strip()
        self.cfg["kvm"]["hotkey_toggle"] = self.hk_toggle.keySequence().toString()
        self.cfg["kvm"]["hotkey_shutdown"] = self.hk_shutdown.keySequence().toString()
        save_cfg(self.cfg)
        self.state.setText("状态：KVM 参数已保存")

    def _save_presence(self):
        self.cfg["presence"]["enabled"] = self.pr_enable.isChecked()
        self.cfg["presence"]["idle_sec"] = self.idle_spin.value()
        self.cfg["presence"]["port"] = self.pr_port.text().strip()
        save_cfg(self.cfg)
        self.state.setText("状态：presence 参数已保存（重启 cam_presence 生效）")

    def _cam_running(self):
        """检查 CamPresence.exe 是否在跑（兼容 UI 重启前启动的进程）"""
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CamPresence.exe"],
                                 capture_output=True, text=True, encoding="gbk", errors="ignore")
            return "CamPresence.exe" in (out.stdout or "")
        except Exception:
            return False

    def _start_cam(self):
        if self._cam_running():
            self.cam_switch.setText("停止摄像头检测")
            self.state.setText("状态：摄像头检测已在运行")
            return True
        exe = self.cfg.get("cam_exe", "")
        if not exe or not os.path.exists(exe):
            self.state.setText("状态：找不到 CamPresence.exe")
            return False
        port = self.cfg["presence"]["port"]
        idle = self.cfg["presence"]["idle_sec"]
        try:
            self.cam_proc = subprocess.Popen([exe, "--port", port, "--idle-sec", str(idle)])
            self.cam_switch.setText("停止摄像头检测")
            self.state.setText("状态：摄像头检测已启动（%s）" % port)
            return True
        except Exception as e:
            self.state.setText("状态：启动失败 %s" % e)
            return False

    def _stop_cam(self):
        # 1) 杀 CamPresence.exe
        try:
            subprocess.run(["taskkill", "/F", "/IM", "CamPresence.exe"],
                           capture_output=True, text=True, encoding="gbk", errors="ignore")
        except Exception:
            pass
        # 2) 杀 python/pythonw 跑的 cam_presence.py（按命令行匹配，防手动残留）
        try:
            ps = ("Get-CimInstance Win32_Process -Filter \"name='python.exe' or name='pythonw.exe'\" | "
                  "Where-Object { $_.CommandLine -like '*cam_presence*' } | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="gbk", errors="ignore")
        except Exception:
            pass
        self.cam_proc = None
        self.cam_switch.setText("启动摄像头检测")
        # 3) 补发关灯（强杀不会走 finally）
        self._send_led_off()

    def _send_led_off(self):
        """直接向摄像头板发 LED:OFF（CamPresence 被强杀后补发）"""
        port = self.cfg["presence"]["port"]
        try:
            import time as _t
            import serial
            _t.sleep(0.8)   # 等 CamPresence 释放串口
            s = serial.Serial(port, 115200, timeout=0, write_timeout=0.3)
            s.write(b"LED:OFF\r\n")
            _t.sleep(0.1)
            s.close()
        except Exception:
            pass

    def _toggle_cam(self):
        """启动 / 停止摄像头无人检测（CamPresence.exe）"""
        if self.cam_proc and self.cam_proc.poll() is None:
            self._stop_cam()
            self.state.setText("状态：摄像头检测已停止")
        else:
            self._start_cam()

    def _save_all(self):
        """统一保存所有设置，并给出成功/失败提示"""
        try:
            self.cfg["kvm"]["port"] = self.kvm_port.text().strip()
            self.cfg["kvm"]["hotkey_toggle"] = self.hk_toggle.keySequence().toString()
            self.cfg["kvm"]["hotkey_shutdown"] = self.hk_shutdown.keySequence().toString()
            self.cfg["presence"]["enabled"] = self.pr_enable.isChecked()
            self.cfg["presence"]["idle_sec"] = self.idle_spin.value()
            self.cfg["presence"]["port"] = self.pr_port.text().strip()
            save_cfg(self.cfg)

            # 立即把"无人检测"开关同步到实际进程（关=真停，开=真起）
            cam_running = self.cam_proc is not None and self.cam_proc.poll() is None
            if self.cfg["presence"]["enabled"] and not cam_running:
                self._start_cam()
            elif not self.cfg["presence"]["enabled"] and cam_running:
                self._stop_cam()

            self.state.setText("状态：设置已保存")
            self.state.setStyleSheet("color:#4ade80;font-size:13px;")
            self._notify("设置已保存", "参数已写入 config.json\n无人检测已按开关同步")
        except Exception as e:
            self.state.setText("状态：保存失败 %s" % e)
            self.state.setStyleSheet("color:#f87171;font-size:13px;")
            self._notify("保存失败", str(e)[:120])

    def _reset_defaults(self):
        """恢复默认参数（同时刷新界面）"""
        self.cfg = json.loads(json.dumps(DEFAULT))
        save_cfg(self.cfg)
        self.kvm_port.setText(self.cfg["kvm"]["port"])
        self.hk_toggle.setKeySequence(QKeySequence(self.cfg["kvm"]["hotkey_toggle"]))
        self.hk_shutdown.setKeySequence(QKeySequence(self.cfg["kvm"]["hotkey_shutdown"]))
        self.pr_enable.setChecked(bool(self.cfg["presence"]["enabled"]))
        self.idle_spin.setValue(int(self.cfg["presence"]["idle_sec"]))
        self.pr_port.setText(self.cfg["presence"]["port"])
        self.state.setText("状态：已恢复默认设置")
        self.state.setStyleSheet("color:#facc15;font-size:13px;")
        self._notify("已恢复默认", "所有参数已重置为默认值")

    def _notify(self, title, msg):
        try:
            self.tray.showMessage(title, msg, QSystemTrayIcon.MessageIcon.Information, 3000)
        except Exception:
            pass

    # ---------- 刷新 ----------
    def _refresh(self):
        txt = last_log()
        self.log.setPlainText(txt)
        for line in reversed(txt.splitlines()):
            if "[MODE]" in line:
                self.state.setText("状态：" + line.strip()[-70:])
                break

    def _on_tray(self, reason):
        # 单击即可显示窗口（双击也兼容）
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def closeEvent(self, event):
        """关闭按钮 = 隐藏到托盘（不退出）；退出请用托盘右键菜单"""
        event.ignore()
        self.hide()
        try:
            self.tray.showMessage(
                "KVM 控制台",
                "已最小化到托盘，右键托盘图标可退出",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
