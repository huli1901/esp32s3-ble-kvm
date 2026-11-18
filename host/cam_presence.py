#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
摄像头板视觉从机 —— 台式机端"有人/无人"检测

解析摄像头板串口输出的人脸框：
    $x1,y1,x2,y2,#
    （无人脸时板子输出 $0,0,320,240,# —— 全屏框）

规则：
    无人脸持续 ABSENT_SEC 秒 -> 锁屏 + 关显示器
    重新检测到人脸          -> 点亮显示器

用法：
    python cam_presence.py --port COM10
    python cam_presence.py --test          # 键盘模拟输入测试

依赖：pyserial
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

try:
    import serial
except ImportError:
    print("缺少 pyserial，请先安装： pip install pyserial")
    sys.exit(1)

# 无控制台运行时把输出重定向到日志
if sys.stdout is None or getattr(sys, "frozen", False):
    for _d in (os.path.join(os.environ.get("APPDATA", ""), "CamPresence"),
               os.path.dirname(os.path.abspath(sys.argv[0])),
               os.environ.get("TEMP", ".")):
        if not _d:
            continue
        try:
            os.makedirs(_d, exist_ok=True)
            _f = open(os.path.join(_d, "cam_log.txt"), "a",
                      encoding="utf-8", buffering=1)
            sys.stdout = _f
            sys.stderr = _f
            break
        except Exception:
            continue

user32 = ctypes.WinDLL("user32", use_last_error=True)

HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
MONITOR_OFF = 2
MONITOR_ON = -1

user32.LockWorkStation.restype = wt.BOOL
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = wt.LPARAM


def monitor_power(on):
    """on=True 点亮显示器；on=False 关闭显示器（不进睡眠）。"""
    lp = wt.LPARAM(MONITOR_ON if on else MONITOR_OFF)
    return bool(user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND,
                                    SC_MONITORPOWER, lp))


def lock_workstation():
    return bool(user32.LockWorkStation())


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def get_idle_seconds():
    """距上次鼠标/键盘输入的空闲秒数"""
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        user32.GetLastInputInfo(ctypes.byref(lii))
        return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0
    except Exception:
        return 0.0


def wake_display():
    """唤醒显示器：电源开消息 + 多次鼠标微动（单次可能被系统吞掉）"""
    monitor_power(True)
    for _ in range(3):
        try:
            user32.mouse_event(0x0001, 1, 0, 0, 0)    # MOUSEEVENTF_MOVE 右移1px
            user32.mouse_event(0x0001, -1, 0, 0, 0)   # 移回
        except Exception:
            pass
        time.sleep(0.06)


class CamPresence:
    def __init__(self, port, baud=115200, absent_sec=5.0,
                 full_w=300, full_h=220, dry_run=False, idle_sec=30.0):
        self.port = port
        self.baud = baud
        self.absent_sec = absent_sec
        self.full_w = full_w        # 框宽 >= 此值 且 高 >= full_h -> 视为无人
        self.full_h = full_h
        self.dry_run = dry_run      # True=只观察，不执行锁屏/亮屏
        self.idle_sec = idle_sec    # 鼠标键盘空闲多少秒才允许锁屏
        self.ser = None
        self.running = True
        self.present = None         # None=未知 True=在座 False=离座
        self._absent_since = None
        self._open()

    def _open(self):
        last = None
        for attempt in range(30):
            try:
                self.ser = serial.Serial(self.port, self.baud,
                                         timeout=0, write_timeout=0.3)
                print("[OPEN] %s @ %d" % (self.port, self.baud))
                try:
                    self.ser.write(b"LED:ON\r\n")   # 摄像头板指示灯：亮=检测中
                except Exception:
                    pass
                return
            except Exception as e:
                last = e
                if attempt == 0:
                    print("[WAIT] %s 暂不可用，等待枚举..." % self.port)
                time.sleep(2)
        raise last

    def _do_lock(self):
        if self.present is False:
            return
        self.present = False
        if self.dry_run:
            print("[DRY] 无人 -> 本应锁屏 + 关显示器（未执行）")
            return
        lock_workstation()
        monitor_power(False)
        print("[ACTION] 无人 -> 锁屏 + 关显示器")

    def _do_wake(self):
        if self.present is True:
            return
        self.present = True
        if self.dry_run:
            print("[DRY] 有人 -> 本应点亮显示器（未执行）")
            return
        wake_display()
        print("[ACTION] 有人 -> 点亮显示器")

    def exec_cmd(self, s):
        """解析 CMD:PERSON / CMD:ABSENT"""
        cmd = s.strip()
        if not cmd:
            return
        if cmd == "CMD:PERSON":
            self._absent_since = None
            self._do_wake()
        elif cmd == "CMD:ABSENT":
            if self._absent_since is None:
                self._absent_since = time.time()
                print("[ABSENT] 开始计时")

    def loop(self):
        buf = b""
        while self.running:
            # 无人 且 鼠标键盘空闲 >= idle_sec -> 休眠屏幕
            if self._absent_since is not None and get_idle_seconds() >= self.idle_sec:
                self._do_lock()
            try:
                data = self.ser.read(128)
            except Exception as e:
                print("[ERR] 串口读失败: %s，5s 后重连" % e)
                try:
                    self.ser.close()
                except Exception:
                    pass
                time.sleep(5)
                try:
                    self._open()
                    buf = b""
                except Exception:
                    pass
                continue
            if not data:
                time.sleep(0.005)
                continue
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.exec_cmd(line.decode("ascii", "ignore"))
            if len(buf) > 4096:
                buf = b""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--absent-sec", type=float, default=5.0,
                    help="无人持续多少秒后锁屏")
    ap.add_argument("--idle-sec", type=float, default=30.0,
                    help="鼠标键盘空闲多少秒才允许锁屏（配合无人）")
    ap.add_argument("--test", action="store_true",
                    help="键盘输入测试（不占用串口），输入如 $0,0,320,240,")
    ap.add_argument("--dry-run", action="store_true",
                    help="只观察串口输出和判断结果，不执行锁屏/亮屏")
    args = ap.parse_args()

    print("=" * 58)
    print(" 摄像头视觉从机 - 有人/无人检测")
    print(" 串口=%s   无人阈值=%ss" % (args.port, args.absent_sec))
    print("=" * 58)

    if args.test:
        obj = CamPresence.__new__(CamPresence)
        obj.port = args.port
        obj.baud = args.baud
        obj.absent_sec = args.absent_sec
        obj.full_w, obj.full_h = 300, 220
        obj.ser = None
        obj.running = True
        obj.present = None
        obj._absent_since = None
        obj.dry_run = True
        print("测试：输入 CMD:PERSON (有人) 或 CMD:ABSENT (无人)")
        try:
            while True:
                obj.exec_cmd(input("> "))
        except (EOFError, KeyboardInterrupt):
            return 0

    cam = CamPresence(args.port, args.baud, args.absent_sec,
                      dry_run=args.dry_run, idle_sec=args.idle_sec)
    print("监听中... Ctrl+C 退出")
    try:
        cam.loop()
    except KeyboardInterrupt:
        pass
    finally:
        cam.running = False
        try:
            cam.ser.write(b"LED:OFF\r\n")   # 关灯：检测已停止
            time.sleep(0.1)
            cam.ser.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
