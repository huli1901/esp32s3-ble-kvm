#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YAHBOOM 摄像头模块 —— 头部朝向数据采集器（电脑端）

通过 HTTP 从模块取实时画面，键盘标注后保存到数据集目录，供后续训练。

前置：
    pip install opencv-python numpy requests
    电脑连上板子的 WiFi：SSID = Yahboom_ESP32_WIFI（无密码）
    板子 IP 默认 192.168.4.1

用法：
    python cam_capture.py --url http://192.168.4.1/capture --out dataset

操作：
    1 = 左   2 = 右   3 = 中（按一下存一帧）
    0 = 无人（空画面，人离开/挡住镜头）
    空格 = 连续采集当前类别
    q / ESC = 退出

输出结构：
    dataset/left/*.jpg  right/  center/
"""

import argparse
import os
import sys
import time

try:
    import cv2
    import numpy as np
    import requests
except ImportError:
    print("缺少依赖，请先安装： pip install opencv-python numpy requests")
    sys.exit(1)

CLASSES = {"1": "left", "2": "right", "3": "center", "0": "none"}


def fetch(url, timeout=2.0):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    arr = np.frombuffer(r.content, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def save_frame(out, cls, frame, size):
    d = os.path.join(out, cls)
    os.makedirs(d, exist_ok=True)
    n = len(os.listdir(d))
    path = os.path.join(d, "%s_%05d.jpg" % (cls, n))
    small = cv2.resize(frame, (size, size), interpolation=cv2.INTER_AREA)
    cv2.imwrite(path, small)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://192.168.4.1/capture",
                    help="板子抓图地址")
    ap.add_argument("--out", default="dataset", help="数据集根目录")
    ap.add_argument("--size", type=int, default=96, help="保存尺寸(正方形)")
    ap.add_argument("--interval", type=float, default=0.15,
                    help="连续采集间隔(秒)")
    args = ap.parse_args()

    for c in CLASSES.values():
        os.makedirs(os.path.join(args.out, c), exist_ok=True)
    counts = {c: len(os.listdir(os.path.join(args.out, c)))
              for c in CLASSES.values()}

    print("=" * 60)
    print(" YAHBOOM 手势采集器")
    print(" 地址: %s" % args.url)
    print(" 输出: %s  尺寸: %dx%d" % (args.out, args.size, args.size))
    print(" 按键: 1左 2右 3中 0无人  空格=连续  q=退出")
    print("=" * 60)

    cur = None
    last = 0.0
    fails = 0

    while True:
        try:
            frame = fetch(args.url)
            fails = 0
        except Exception as e:
            fails += 1
            if fails % 20 == 1:
                print("取图失败(%d): %s" % (fails, e))
            time.sleep(0.3)
            continue

        if frame is None:
            continue

        h, w = frame.shape[:2]
        disp = frame.copy()
        cv2.line(disp, (w // 2, 0), (w // 2, h), (0, 255, 0), 1)
        cv2.line(disp, (0, h // 2), (w, h // 2), (0, 255, 0), 1)
        stat = "  ".join("%s:%d" % (c, n) for c, n in counts.items())
        cv2.putText(disp, stat, (5, 16), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (0, 255, 255), 1)
        if cur:
            cv2.putText(disp, "REC " + cur, (5, h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        cv2.imshow("YAHBOOM capture", disp)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break

        ch = chr(key) if key < 128 else ""
        if ch in CLASSES:
            cur = CLASSES[ch]
            save_frame(args.out, cur, frame, args.size)
            counts[cur] += 1
            last = time.time()
        elif key == 32 and cur:          # 空格：连续采集当前类别
            if time.time() - last >= args.interval:
                save_frame(args.out, cur, frame, args.size)
                counts[cur] += 1
                last = time.time()

    cv2.destroyAllWindows()
    print("\n采集结束：")
    for c in CLASSES.values():
        print("  %-6s %d 张" % (c, counts[c]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
