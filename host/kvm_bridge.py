#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ESP32-S3 USB -> BLE 键鼠桥  （主机端）

在台式机上运行。捕获本机键盘和鼠标原始输入，鼠标滑到屏幕右边缘时
自动把输入转发给笔记本（通过 COM 口发给 ESP32-S3，由它转成 BLE HID）。

    python kvm_bridge.py --port COM4

退出远程模式：鼠标向左累计移动超过阈值，或按 Ctrl+Alt+Q 强制切回。

依赖：pyserial
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import sys
import os
import math
import threading
import time

try:
    import serial
except ImportError:
    print("缺少 pyserial，请先安装： pip install pyserial")
    sys.exit(1)

# 无控制台运行（--noconsole 的 exe 或 pythonw）时，把输出重定向到日志文件。
# 此时 sys.stdout 为 None，任何 print 都会抛异常，必须在最前面处理。
if sys.stdout is None or getattr(sys, "frozen", False):
    _candidates = [
        os.path.join(os.environ.get("APPDATA", ""), "KvmBridge"),
        os.path.dirname(os.path.abspath(sys.argv[0])),
        os.environ.get("TEMP", "."),
    ]
    for _d in _candidates:
        if not _d:
            continue
        try:
            os.makedirs(_d, exist_ok=True)
            _f = open(os.path.join(_d, "bridge_log.txt"), "a",
                      encoding="utf-8", buffering=1)
            sys.stdout = _f
            sys.stderr = _f
            break
        except Exception:
            continue

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# ---------------------------------------------------------------- 常量
WM_INPUT = 0x00FF
WM_CLOSE = 0x0010
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1

RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_LEFT_BUTTON_UP = 0x0002
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_RIGHT_BUTTON_UP = 0x0008
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_MIDDLE_BUTTON_UP = 0x0020
RI_MOUSE_WHEEL = 0x0400

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
SPI_SETCURSORS = 0x0057
OCR_NORMAL = 32512

# 快捷键切换（不依赖任何反向通信）
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
HK_LOCAL = 1
HK_REMOTE = 2
HK_TOGGLE = 3
TOGGLE_VKS = (0x91,)        # 只用 ScrollLock 切换

# ---------------------------------------------------------------- 结构体

class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wt.USHORT),
        ("usUsage", wt.USHORT),
        ("dwFlags", wt.DWORD),
        ("hwndTarget", wt.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wt.DWORD),
        ("dwSize", wt.DWORD),
        ("hDevice", wt.HANDLE),
        ("wParam", wt.WPARAM),
    ]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", wt.USHORT),
        ("ulButtons", wt.ULONG),
        ("ulRawButtons", wt.ULONG),
        ("lLastX", wt.LONG),
        ("lLastY", wt.LONG),
        ("ulExtraInformation", wt.ULONG),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wt.USHORT),
        ("Flags", wt.USHORT),
        ("Reserved", wt.USHORT),
        ("VKey", wt.USHORT),
        ("Message", wt.UINT),
        ("ExtraInformation", wt.ULONG),
    ]


class _RAWUNION(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD)]


class RAWINPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("header", RAWINPUTHEADER), ("data", _RAWUNION)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", wt.UINT),
        ("wParam", wt.WPARAM),
        ("lParam", wt.LPARAM),
        ("time", wt.DWORD),
        ("pt", POINT),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)

# 64 位下必须显式声明参数类型，否则句柄会被截断成 32 位
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wt.WORD

user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wt.HWND

user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT

user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE),
                                           wt.UINT, wt.UINT]
user32.RegisterRawInputDevices.restype = wt.BOOL

user32.GetRawInputData.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_void_p,
                                   ctypes.POINTER(wt.UINT), wt.UINT]
user32.GetRawInputData.restype = wt.UINT

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HMODULE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HANDLE

user32.CallNextHookEx.argtypes = [wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT

user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL

user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.PeekMessageW.restype = wt.BOOL

user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT

user32.ClipCursor.argtypes = [ctypes.POINTER(wt.RECT)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

user32.ShowCursor.argtypes = [wt.BOOL]
user32.ShowCursor.restype = ctypes.c_int

user32.CreateCursor.argtypes = [wt.HINSTANCE, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int,
                                ctypes.c_void_p, ctypes.c_void_p]
user32.CreateCursor.restype = wt.HANDLE

user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
user32.LoadCursorW.restype = wt.HANDLE

user32.SetSystemCursor.argtypes = [wt.HANDLE, wt.DWORD]
user32.SetSystemCursor.restype = wt.BOOL

user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
user32.SystemParametersInfoW.restype = wt.BOOL

user32.UnhookWindowsHookEx.argtypes = [wt.HANDLE]
user32.UnhookWindowsHookEx.restype = wt.BOOL

user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.RegisterHotKey.restype = wt.BOOL

user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wt.BOOL

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HANDLE
gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
gdi32.SelectObject.restype = wt.HANDLE

user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(POINT),
                                       ctypes.POINTER(wt.SIZE), wt.HDC,
                                       ctypes.POINTER(POINT), wt.DWORD,
                                       ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.UpdateLayeredWindow.restype = wt.BOOL

kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE

# ---------------------------------------------------------------- VK -> HID 映射
VK_TO_HID = {}
for _i in range(26):                     # A-Z
    VK_TO_HID[0x41 + _i] = 0x04 + _i
for _i in range(10):                     # 0-9
    VK_TO_HID[0x30 + _i] = 0x27 if _i == 0 else 0x1E + (_i - 1)

VK_TO_HID.update({
    0x0D: 0x28,   # Enter
    0x1B: 0x29,   # Esc
    0x08: 0x2A,   # Backspace
    0x09: 0x2B,   # Tab
    0x20: 0x2C,   # Space
    0xBD: 0x2D,   # -
    0xBB: 0x2E,   # =
    0xDB: 0x2F,   # [
    0xDD: 0x30,   # ]
    0xDC: 0x31,   # backslash
    0xBA: 0x33,   # ;
    0xDE: 0x34,   # '
    0xC0: 0x35,   # `
    0xBC: 0x36,   # ,
    0xBE: 0x37,   # .
    0xBF: 0x38,   # /
    0x14: 0x39,   # CapsLock
    0x70: 0x3A, 0x71: 0x3B, 0x72: 0x3C, 0x73: 0x3D,   # F1-F4
    0x74: 0x3E, 0x75: 0x3F, 0x76: 0x40, 0x77: 0x41,   # F5-F8
    0x78: 0x42, 0x79: 0x43, 0x7A: 0x44, 0x7B: 0x45,   # F9-F12
    0x2D: 0x49,   # Insert
    0x24: 0x4A,   # Home
    0x21: 0x4B,   # PageUp
    0x2E: 0x4C,   # Delete
    0x23: 0x4D,   # End
    0x22: 0x4E,   # PageDown
    0x27: 0x4F,   # Right
    0x25: 0x50,   # Left
    0x28: 0x51,   # Down
    0x26: 0x52,   # Up
    0x90: 0x53,   # NumLock
    0x6F: 0x54,   # Numpad /
    0x6A: 0x55,   # Numpad *
    0x6D: 0x56,   # Numpad -
    0x6B: 0x57,   # Numpad +
    0x60: 0x62, 0x61: 0x59, 0x62: 0x5A, 0x63: 0x5B,   # Numpad 0-3
    0x64: 0x5C, 0x65: 0x5D, 0x66: 0x5E, 0x67: 0x5F,   # Numpad 4-7
    0x68: 0x60, 0x69: 0x61, 0x6E: 0x63,               # Numpad 8,9,.
})

# 修饰键：VK -> 报告里的 modifier 位
MOD_VK = {
    0xA0: 0x02, 0xA1: 0x20,   # LShift / RShift
    0xA2: 0x01, 0xA3: 0x10,   # LCtrl  / RCtrl
    0xA4: 0x04, 0xA5: 0x40,   # LAlt   / RAlt
    0x5B: 0x08, 0x5C: 0x80,   # LWin   / RWin
    # Raw Input 有时只给通用修饰键码，按左侧处理
    0x10: 0x02, 0x11: 0x01, 0x12: 0x04,
}


class RippleOverlay:
    """切回本机时在光标位置播放一次扩散圆环，提示光标在哪。"""

    SIZE = 170
    FRAMES = 14
    DURATION = 0.28

    def __init__(self):
        self.hwnd = None
        self._mem_dc = None
        self._bitmap = None
        self._bits = None
        self._busy = False
        self._proc = None
        self._create()

    def _create(self):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.hInstance = hinst
        wc.lpszClassName = "KvmRippleWnd"
        self._proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        if not user32.RegisterClassW(ctypes.byref(wc)):
            if ctypes.get_last_error() != 1410:
                return

        # LAYERED | TRANSPARENT | TOOLWINDOW | TOPMOST | NOACTIVATE
        ex_style = 0x00080000 | 0x00000020 | 0x00000080 | 0x00000008 | 0x08000000
        self.hwnd = user32.CreateWindowExW(
            ex_style, "KvmRippleWnd", "ripple", 0x80000000,
            0, 0, self.SIZE, self.SIZE, None, None, hinst, None)
        if not self.hwnd:
            return

        hdc_screen = user32.GetDC(None)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = self.SIZE
        bi.bmiHeader.biHeight = -self.SIZE
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        ppv = ctypes.c_void_p()
        self._bitmap = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bi), 0,
                                              ctypes.byref(ppv), None, 0)
        self._mem_dc = gdi32.CreateCompatibleDC(hdc_screen)
        if self._bitmap:
            gdi32.SelectObject(self._mem_dc, self._bitmap)
            self._bits = ctypes.cast(ppv, ctypes.POINTER(ctypes.c_uint32))
        user32.ReleaseDC(None, hdc_screen)

    def play(self, x, y):
        if self._busy or not self.hwnd or not self._bits:
            return
        self._busy = True
        threading.Thread(target=self._run, args=(x, y), daemon=True).start()

    def _run(self, x, y):
        try:
            S = self.SIZE
            cx = cy = S // 2
            half = S // 2
            ox, oy = x - half, y - half
            for i in range(self.FRAMES):
                t = i / float(self.FRAMES - 1)
                radius = 10 + t * (half - 18)
                alpha = int(235 * (1.0 - t) ** 1.5)
                self._paint(cx, cy, radius)
                self._present(ox, oy, alpha)
                time.sleep(self.DURATION / self.FRAMES)
            self._present(ox, oy, 0)
        except Exception:
            pass
        finally:
            self._busy = False

    def _paint(self, cx, cy, radius):
        S = self.SIZE
        ctypes.memset(self._bits, 0, S * S * 4)
        bits = self._bits
        thick = 3
        for deg in range(0, 360, 2):
            a = math.radians(deg)
            ca, sa = math.cos(a), math.sin(a)
            for t in range(-thick, thick + 1):
                px = int(cx + (radius + t) * ca)
                py = int(cy + (radius + t) * sa)
                if 0 <= px < S and 0 <= py < S:
                    bits[py * S + px] = 0xFFFFFFFF

    def _present(self, ox, oy, alpha):
        blend = BLENDFUNCTION(0, 0, int(max(1, alpha)), 1)   # AC_SRC_ALPHA
        dst = POINT(ox, oy)
        size = wt.SIZE(self.SIZE, self.SIZE)
        src = POINT(0, 0)
        hdc_screen = user32.GetDC(None)
        user32.UpdateLayeredWindow(self.hwnd, hdc_screen, ctypes.byref(dst),
                                   ctypes.byref(size), self._mem_dc,
                                   ctypes.byref(src), 0, ctypes.byref(blend), 2)
        user32.ReleaseDC(None, hdc_screen)


def clamp(v, lo=-127, hi=127):
    return lo if v < lo else (hi if v > hi else v)


class KvmBridge:
    """LOCAL：正常用本机；REMOTE：键鼠转给笔记本。"""

    def __init__(self, port, cam_port="COM10", edge_margin=2, back_threshold=-400):
        # 开机自启时串口可能还没枚举完，重试等待（最多约 60 秒）
        last_err = None
        for attempt in range(30):
            try:
                self.ser = serial.Serial(port, 115200, timeout=0, write_timeout=0.2)
                break
            except Exception as e:
                last_err = e
                if attempt == 0:
                    print("[WAIT] %s 暂不可用，等待枚举..." % port)
                time.sleep(2)
        else:
            raise last_err
        # 摄像头板串口（可选，失败不影响键鼠）
        self.cam_ser = None
        if cam_port:
            try:
                self.cam_ser = serial.Serial(cam_port, 115200, timeout=0, write_timeout=0.2)
                print("[CAM] 摄像头板串口 %s 已打开" % cam_port)
            except Exception as e:
                print("[CAM] %s 打开失败: %s（头部自动切换不可用）" % (cam_port, e))
        # 双屏/多屏必须用虚拟桌面边界，GetSystemMetrics(0) 只给主屏宽度
        SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
        SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
        self.vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        self.vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        self.screen_w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        self.screen_h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        self.right_edge = self.vx + self.screen_w - 1
        self.edge_margin = edge_margin
        self.back_threshold = back_threshold

        self.mode = "LOCAL"
        self.mod = 0
        self.keys = [0] * 5          # 最多 5 键同时按下
        self.buttons = 0
        self.vx = 0                  # 笔记本上的虚拟光标位置（进入时归零）
        self.vy = 0
        self._local_y = 540          # 切回时还原到的光标纵坐标

        self.hwnd = None
        self.hook = None
        self.mhook = None
        self.running = True
        self._want_toggle = False
        self.conn_count = 1          # 由 ESP32 的 AA 06 ACK 更新
        self.target_slot = 0         # 当前控制的连接槽位
        self._kb_proc = HOOKPROC(self._keyboard_hook)
        self._m_proc = HOOKPROC(self._mouse_hook)
        self.stats = {"kb": 0, "mouse": 0, "raw": 0}
        self.ripple = RippleOverlay()
        # 头部朝向自动切换（默认关闭，Ctrl+Alt+A 开关）
        self.auto_head = False
        self._last_head = None

    # ------------------------------------------------ 发送
    def _send_keyboard(self):
        frame = bytes([0xAA, 0x01, self.mod, 0x00, *self.keys])
        try:
            self.ser.write(frame)
            self.stats["kb"] += 1
        except Exception:
            pass

    def _send_mouse(self, dx, dy, wheel):
        frame = bytes([0xAA, 0x02, self.buttons & 0x07, clamp(dx) & 0xFF,
                       clamp(dy) & 0xFF, int(clamp(wheel)) & 0xFF])
        try:
            self.ser.write(frame)
            self.stats["mouse"] += 1
        except Exception:
            pass

    def _send_target(self):
        """告诉 ESP32 当前控制哪个连接槽位。"""
        try:
            self.ser.write(bytes([0xAA, 0x05, self.target_slot & 0xFF]))
        except Exception:
            pass

    def cycle_target(self):
        """LOCAL -> slot0 -> slot1 ... -> LOCAL，按连接数循环。"""
        if self.mode == "LOCAL":
            self.target_slot = 0
            self.enter_remote()
            return
        nxt = self.target_slot + 1
        if nxt >= max(1, self.conn_count):
            self.exit_remote()
        else:
            self.target_slot = nxt
            self._send_target()
            print("[MODE] -> 目标 %d/%d" % (nxt + 1, self.conn_count))

    def _notify_state(self):
        """0=本地(绿) 1=slot0(蓝) 2=slot1(紫)，供板载 LED 指示。"""
        state = 0 if self.mode == "LOCAL" else (self.target_slot + 1)
        try:
            self.ser.write(bytes([0xAA, 0x04, state & 0xFF]))
        except Exception:
            pass

    # ------------------------------------------------ 模式切换
    def _push_cursor(self, dx, dy):
        """分批发送大位移（单条报告 ±127 上限），把笔记本光标推到角落。"""
        guard = 0
        while (dx or dy) and guard < 120:
            sx = max(-127, min(127, dx))
            sy = max(-127, min(127, dy))
            self._send_mouse(sx, sy, 0)
            dx -= sx
            dy -= sy
            guard += 1

    def enter_remote(self):
        if self.mode == "REMOTE":
            return
        self.mode = "REMOTE"
        self._remote_since = time.time()
        self._clip_mouse(True)
        self._hide_cursor(True)
        # 记住进入前的纵向位置，切回时还原，避免总是掉到屏幕角落
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        self._local_y = pt.y
        if self.mhook is None:
            self.mhook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._m_proc, None, 0)
            if not self.mhook:
                print("[ERR] 鼠标钩子安装失败 code=%d（点击会同时作用于两台机）"
                      % ctypes.get_last_error())
        self._send_target()
        self._notify_state()
        print("[MODE] -> REMOTE（键鼠转发给 slot %d）" % self.target_slot)

    def exit_remote(self):
        if self.mode == "LOCAL":
            return
        self.mode = "LOCAL"
        self._clip_mouse(False)
        self._hide_cursor(False)
        if self.mhook is not None:
            user32.UnhookWindowsHookEx(self.mhook)
            self.mhook = None
        # 松开所有键，避免笔记本上卡键
        self.mod = 0
        self.keys = [0] * 5
        self.buttons = 0
        self._send_keyboard()
        self._send_mouse(0, 0, 0)
        # 光标拉回屏幕内侧
        pt = POINT(self.right_edge - 30, self._local_y)
        user32.SetCursorPos(pt.x, pt.y)
        self.ripple.play(pt.x, pt.y)      # 在光标位置播放水波提示
        self._notify_state()
        print("[MODE] -> LOCAL（回到本机）")

    def _hide_cursor(self, hide):
        """全局替换系统光标：ShowCursor 只对调用线程自身的光标有效。"""
        if hide:
            if not getattr(self, "_cursor_hidden", False):
                # 单色光标：dest = (dest AND mask) XOR xor_mask
                # AND=1 / XOR=0 时保持屏幕原样，即完全透明
                blank = user32.CreateCursor(None, 0, 0, 8, 8,
                                            ctypes.c_char_p(b"\xff" * 16),
                                            ctypes.c_char_p(b"\x00" * 16))
                if blank:
                    user32.SetSystemCursor(blank, OCR_NORMAL)
                    self._cursor_hidden = True
                else:
                    print("[WARN] 创建透明光标失败 code=%d"
                          % ctypes.get_last_error())
        else:
            if getattr(self, "_cursor_hidden", False):
                # 先显式把系统箭头装回去，再用 SPI_SETCURSORS 重载兜底
                arrow = user32.LoadCursorW(None, ctypes.cast(32512, wt.LPCWSTR))
                if arrow:
                    user32.SetSystemCursor(arrow, OCR_NORMAL)
                ok = user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
                print("[CURSOR] 恢复系统光标 arrow=%s spi=%s err=%d"
                      % (bool(arrow), bool(ok), ctypes.get_last_error()))
                self._cursor_hidden = False

    def _clip_mouse(self, on):
        if on:
            r = wt.RECT(self.right_edge - 1, self.vy,
                        self.right_edge + 1, self.vy + self.screen_h)
            user32.ClipCursor(ctypes.byref(r))
        else:
            user32.ClipCursor(None)

    # ------------------------------------------------ 钩子
    def _send_shutdown_laptop(self):
        """通过 KVM 板 GATT 0xFF02 notify 通知笔记本关机"""
        try:
            self.ser.write(bytes([0xAA, 0x07, 0x01]))
            print("[LAPTOP] 已发送关机命令 (AA 07 01)")
        except Exception as e:
            print("[LAPTOP] 发送失败: %s" % e)

    def _keyboard_hook(self, nCode, wParam, lParam):
        if nCode == 0:
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode
            pressed = (kb.flags & 0x80) == 0      # LLKHF_UP = 0x80

            # 单键切换：ScrollLock，按下时切，抬起忽略
            if vk in TOGGLE_VKS:
                if pressed:
                    self.cycle_target()
                return 1

            # 备用逃生键：Ctrl+Alt+Q
            if vk == 0x51:
                ctrl = user32.GetAsyncKeyState(0x11) & 0x8000
                alt = user32.GetAsyncKeyState(0x12) & 0x8000
                if ctrl and alt:
                    if self.mode == "REMOTE":
                        self.exit_remote()
                    return 1

            # 关机笔记本：Ctrl+Alt+End
            if vk == 0x23 and pressed:
                ctrl = user32.GetAsyncKeyState(0x11) & 0x8000
                alt = user32.GetAsyncKeyState(0x12) & 0x8000
                if ctrl and alt:
                    self._send_shutdown_laptop()
                    return 1

            # 头部自动切换功能已移除（避免摄像头误判导致反复切换）

            # 手机息屏/唤醒：Ctrl+Alt+P -> 发 HID Power 键（Usage 0x66）
            if vk == 0x50 and pressed:
                # 用已记录的修饰键位（REMOTE 下 GetAsyncKeyState 不可靠）
                ctrl = bool(self.mod & 0x01) or bool(self.mod & 0x10) or bool(user32.GetAsyncKeyState(0x11) & 0x8000)
                alt = bool(self.mod & 0x04) or bool(self.mod & 0x40) or bool(user32.GetAsyncKeyState(0x12) & 0x8000)
                print("[PHONE] P 按下 ctrl=%s alt=%s mod=0x%02X mode=%s" % (ctrl, alt, self.mod, self.mode))
                if ctrl and alt:
                    self.keys = [0x67, 0, 0, 0, 0]   # HID Sleep（Android 对 Power 0x66 常拦截，改试 Sleep）
                    self.mod = 0
                    self._send_keyboard()
                    self.keys = [0] * 5
                    self._send_keyboard()
                    print("[PHONE] 发送 Sleep 键（息屏/唤醒）")
                    return 1

            if self.mode == "REMOTE":
                # 钩子阻塞后 Raw Input 不再产生键盘事件，转发必须在这里做
                self._apply_key(vk, pressed)
                return 1
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _apply_key(self, vk, pressed):
        """更新修饰键/键槽并发送整份键盘报告。"""
        changed = False
        if vk in MOD_VK:
            bit = MOD_VK[vk]
            new_mod = (self.mod | bit) if pressed else (self.mod & ~bit)
            if new_mod != self.mod:
                self.mod = new_mod
                changed = True
        elif vk in VK_TO_HID:
            code = VK_TO_HID[vk]
            if pressed:
                if code not in self.keys:
                    try:
                        self.keys[self.keys.index(0)] = code
                        changed = True
                    except ValueError:
                        pass          # 已按满 5 键
            else:
                if code in self.keys:
                    self.keys[self.keys.index(code)] = 0
                    changed = True
        if changed and self.mode == "REMOTE":
            self._send_keyboard()

    def _reader_loop(self):
        """读串口：AA 03(BOOT 键) 与 AA 06(目标切换 ACK)。"""
        buf = b""
        while self.running:
            try:
                data = self.ser.read(64)
            except Exception:
                break
            if not data:
                time.sleep(0.005)
                continue
            buf += data
            while len(buf) >= 4:
                i3 = buf.find(b"\xAA\x03")
                i6 = buf.find(b"\xAA\x06")
                if i3 < 0 and i6 < 0:
                    buf = buf[-1:]
                    break
                use3 = i3 >= 0 and (i6 < 0 or i3 < i6)
                i = i3 if use3 else i6
                if len(buf) < i + 4:
                    buf = buf[i:]
                    break
                a, b_ = buf[i + 2], buf[i + 3]
                buf = buf[i + 4:]
                if use3:
                    if a == 1 and b_ >= 0x10:
                        print("[LAPTOP] ACK 0x%02X (%s)" % (b_, "OK" if b_ == 0x10 else "失败"))
                    elif a == 1:
                        self._want_toggle = True
                        print("[BTN] 开发板 BOOT 键")
                else:
                    if a != 0xFF:
                        self.conn_count = max(1, b_)
                        print("[ACK] 目标 slot=%d，连接数=%d" % (a, b_))
                    else:
                        print("[ACK] 切换目标失败，连接数=%d" % b_)

    def _cam_reader_loop(self):
        """读摄像头板串口：解析 CMD:HEAD:*，开关打开时才执行切换。"""
        if not self.cam_ser:
            return
        buf = b""
        while self.running:
            try:
                data = self.cam_ser.read(64)
            except Exception:
                break
            if not data:
                time.sleep(0.02)
                continue
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = line.decode("utf-8", "ignore").strip()
                if "CMD:HEAD:" in s:
                    head = s.split("CMD:HEAD:")[-1].strip()
                    self._on_head(head)

    def _on_head(self, head):
        if not self.auto_head:
            return
        if head == self._last_head:
            return
        self._last_head = head
        if head == "right":
            if self.mode != "REMOTE":
                self.target_slot = 0
                self.enter_remote()
                print("[AUTO] 头部右 -> 切笔记本")
        elif head in ("left", "center"):
            if self.mode != "LOCAL":
                self.exit_remote()
                print("[AUTO] 头部%s -> 切回本地" % head)

    def _mouse_hook(self, nCode, wParam, lParam):
        if nCode == 0:
            if not getattr(self, "_mhook_seen", False):
                self._mhook_seen = True
                print("[HOOK] 鼠标钩子已生效，本机点击/移动被拦截")
            if self.mode == "REMOTE":
                # 返回 1 吞掉系统鼠标事件；Raw Input 来自驱动层，仍能收到
                return 1
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    # ------------------------------------------------ 事件处理
    def on_mouse(self, m):
        dx, dy = m.lLastX, m.lLastY
        btn = m.ulButtons

        if btn:
            if btn & RI_MOUSE_LEFT_BUTTON_DOWN: self.buttons |= 0x01
            if btn & RI_MOUSE_LEFT_BUTTON_UP: self.buttons &= ~0x01
            if btn & RI_MOUSE_RIGHT_BUTTON_DOWN: self.buttons |= 0x02
            if btn & RI_MOUSE_RIGHT_BUTTON_UP: self.buttons &= ~0x02
            if btn & RI_MOUSE_MIDDLE_BUTTON_DOWN: self.buttons |= 0x04
            if btn & RI_MOUSE_MIDDLE_BUTTON_UP: self.buttons &= ~0x04

        wheel = 0
        if btn & RI_MOUSE_WHEEL:
            high = (btn >> 16) & 0xFFFF
            wheel = (high - 0x10000) if high > 0x7FFF else high
            wheel = int(wheel / 120)

        if self.mode == "REMOTE":
            if dx or dy:
                self._send_mouse(dx, dy, 0)
            if wheel:
                self._send_mouse(0, 0, wheel)
            if btn & (RI_MOUSE_LEFT_BUTTON_DOWN | RI_MOUSE_LEFT_BUTTON_UP |
                      RI_MOUSE_RIGHT_BUTTON_DOWN | RI_MOUSE_RIGHT_BUTTON_UP |
                      RI_MOUSE_MIDDLE_BUTTON_DOWN | RI_MOUSE_MIDDLE_BUTTON_UP):
                self._send_mouse(0, 0, 0)
            return

        # LOCAL 分支不做边缘检测：切换完全由全局热键驱动

    def on_keyboard(self, k):
        # REMOTE 时键盘已由低级钩子直接转发：钩子返回 1 阻塞后，Raw Input
        # 不再产生键盘事件，这条路径实际走不到，保留仅为结构完整。
        return

    # ------------------------------------------------ 消息解析
    def handle_input(self, lParam):
        self.stats["raw"] += 1
        size = wt.UINT(0)
        user32.GetRawInputData(lParam, RID_INPUT, None, ctypes.byref(size),
                               ctypes.sizeof(RAWINPUTHEADER))
        if size.value == 0:
            return
        buf = ctypes.create_string_buffer(size.value)
        got = user32.GetRawInputData(lParam, RID_INPUT, buf, ctypes.byref(size),
                                     ctypes.sizeof(RAWINPUTHEADER))
        if got != size.value:
            return
        raw = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
        if raw.header.dwType == RIM_TYPEMOUSE:
            self.on_mouse(raw.data.mouse)
        elif raw.header.dwType == RIM_TYPEKEYBOARD:
            self.on_keyboard(raw.data.keyboard)


def main():
    # 单实例锁：任务计划和手动启动不会重复占用同一个串口
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    kernel32.CreateMutexW.restype = wt.HANDLE
    _h = kernel32.CreateMutexW(None, False, "Global\\KvmBridgeSingleton")
    if _h and ctypes.get_last_error() == 183:        # ERROR_ALREADY_EXISTS
        print("[WARN] 已有 KvmBridge 实例在运行，本进程退出")
        return 0

    # 上次若被强杀，系统光标可能停在隐藏状态，启动时先还原
    user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--cam-port", default="",
                    help="摄像头板串口（头部朝向），默认空=不读；presence 由 cam_presence.py 负责")
    args = ap.parse_args()

    bridge = KvmBridge(args.port, cam_port=args.cam_port)
    print("=" * 58)
    print(" ESP32-S3 键鼠桥 主机端")
    print(" 串口: %s   虚拟桌面 %dx%d @ (%d,%d)   右边界 x=%d" %
          (args.port, bridge.screen_w, bridge.screen_h,
           bridge.vx, bridge.vy, bridge.right_edge))
    print(" 按 ScrollLock 单键切换：台式机 <-> 笔记本")
    print(" 本脚本无窗口，Ctrl+C 或任务管理器结束")
    print("=" * 58)

    # 1) 先创建隐藏窗口（INPUTSINK 必须绑定真实 hwnd，否则报 87 无效参数）
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.hInstance = hinst
    wc.lpszClassName = "KvmBridgeWnd"
    wnd_proc_ref = WNDPROC(lambda h, m, w, l: _wnd_proc(bridge, h, m, w, l))
    bridge._wnd_proc_ref = wnd_proc_ref        # 保持引用防止被 GC 回收
    wc.lpfnWndProc = ctypes.cast(wnd_proc_ref, ctypes.c_void_p)

    if not user32.RegisterClassW(ctypes.byref(wc)):
        err = ctypes.get_last_error()
        if err != 1410:                        # 1410 = 类已存在，可忽略
            print("注册窗口类失败:", err)
            return 1

    hwnd = user32.CreateWindowExW(0, "KvmBridgeWnd", "kvm", 0, 0, 0, 0, 0,
                                  None, None, hinst, None)
    if not hwnd:
        print("创建窗口失败:", ctypes.get_last_error())
        return 1
    bridge.hwnd = hwnd

    # 2) 注册 Raw Input，绑定到该窗口
    devs = (RAWINPUTDEVICE * 2)()
    devs[0].usUsagePage = 0x01
    devs[0].usUsage = 0x02          # mouse
    devs[0].dwFlags = RIDEV_INPUTSINK
    devs[0].hwndTarget = hwnd
    devs[1].usUsagePage = 0x01
    devs[1].usUsage = 0x06          # keyboard
    devs[1].dwFlags = RIDEV_INPUTSINK
    devs[1].hwndTarget = hwnd
    if not user32.RegisterRawInputDevices(devs, 2, ctypes.sizeof(RAWINPUTDEVICE)):
        print("Raw Input 注册失败:", ctypes.get_last_error())
        return 1
    print("Raw Input 注册成功，后台监听中...")

    # 键盘钩子常驻：REMOTE 时拦截按键，同时负责 ScrollLock 单键切换
    bridge.hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, bridge._kb_proc, None, 0)
    if not bridge.hook:
        print("[ERR] 键盘钩子安装失败 code=%d（单键切换将不可用）"
              % ctypes.get_last_error())
    else:
        print("已就绪：按 ScrollLock 单键切换控制权")

    # 备用热键：Ctrl+Alt+S 切换（系统级 RegisterHotKey，不依赖 LL 钩子）
    if user32.RegisterHotKey(hwnd, HK_TOGGLE, MOD_CONTROL | MOD_ALT, 0x53):
        print("备用热键 Ctrl+Alt+S 已注册（切换控制权）")
    else:
        print("[WARN] 备用热键 Ctrl+Alt+S 注册失败 code=%d" % ctypes.get_last_error())

    # 启动串口读线程，接收笔记本经 ESP32 转来的切回信号
    threading.Thread(target=bridge._reader_loop, daemon=True).start()
    # 启动摄像头板读线程（头部朝向）
    if bridge.cam_ser:
        threading.Thread(target=bridge._cam_reader_loop, daemon=True).start()

    msg = MSG()
    last_report = time.time()
    PM_REMOVE = 1
    try:
        while True:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == 0x0012:          # WM_QUIT
                    raise KeyboardInterrupt
                if msg.message == WM_HOTKEY:
                    if msg.wParam == HK_TOGGLE:
                        print("[HOTKEY] Ctrl+Alt+S 切换")
                        bridge.cycle_target()
                    elif msg.wParam == HK_REMOTE:
                        bridge.enter_remote()
                    elif msg.wParam == HK_LOCAL:
                        bridge.exit_remote()
                    continue
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            # 开发板 BOOT 键请求切换（走串口反向上报，不经键盘栈）
            if bridge._want_toggle:
                bridge._want_toggle = False
                if bridge.mode == "REMOTE":
                    bridge.exit_remote()
                else:
                    bridge.enter_remote()
            time.sleep(0.002)
            if time.time() - last_report > 5:
                last_report = time.time()
                pt = POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                print("[STAT] mode=%s cursor=(%d,%d) edge_x=%d raw=%d kb=%d mouse=%d" %
                      (bridge.mode, pt.x, pt.y, bridge.right_edge,
                       bridge.stats["raw"], bridge.stats["kb"], bridge.stats["mouse"]))
    except KeyboardInterrupt:
        pass
    finally:
        bridge.running = False
        try:
            bridge.exit_remote()
        except Exception:
            pass
        # 双保险：即使 exit_remote 出错也还原系统光标
        try:
            user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
        except Exception:
            pass
        try:
            bridge.ser.close()
        except Exception:
            pass
        print("已退出")
    return 0


def _wnd_proc(bridge, hwnd, msg, wparam, lparam):
    if msg == WM_INPUT:
        bridge.handle_input(lparam)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


if __name__ == "__main__":
    sys.exit(main())
