# -*- coding: utf-8 -*-
"""笔记本端常驻代理：连接 KVM 板 BLE，收命令 -> 回 ACK -> 执行

用法：
    pip install bleak
    python laptop_agent.py

命令协议（GATT 0xFF00 服务）：
    台式机 -> 笔记本：notify 0xFF02，1 字节 cmd（0x01=关机）
    笔记本 -> 台式机：write 0xFF01，1 字节 ack（0x01=OK 0x02=失败）
"""
import asyncio
import subprocess
import sys

from bleak import BleakClient, BleakScanner

CHR_CMD = "0000ff01-0000-1000-8000-00805f9b34fb"     # 写：ACK 回台式机
CHR_NOTIFY = "0000ff02-0000-1000-8000-00805f9b34fb"  # 订阅：收台式机命令

CMD_SHUTDOWN = 0x01
ACK_OK = 0x10
ACK_FAIL = 0x11


async def reply(client, val):
    try:
        await client.write_gatt_char(CHR_CMD, bytes([val]), response=False)
        print("[ACK] 已回 %d" % val)
    except Exception as e:
        print("[ACK] 回写失败: %s" % e)


def do_shutdown():
    print("[EXEC] 执行关机 ...")
    subprocess.Popen('shutdown /s /t 3 /c "KVM remote shutdown"', shell=True)


async def main():
    addr = None
    if "--addr" in sys.argv:
        i = sys.argv.index("--addr")
        if i + 1 < len(sys.argv):
            addr = sys.argv[i + 1]

    if addr:
        target_addr, target_name = addr, "(手动指定)"
    else:
        print("扫描 KVM 设备（8 秒）...")
        devs = await BleakScanner.discover(timeout=8.0)
        print("扫到 %d 个设备：" % len(devs))
        for d in devs:
            print("  %s  %s" % (d.address, d.name or "-"))
        target = None
        for d in devs:
            n = (d.name or "").upper()
            if "ESP" in n or "KVM KEYBOARD" in n or "BLE KEYBOARD" in n or "YAHBOOM" in n:
                target = d
                break
        if target is None:
            print("\n没找到 KVM 板。请把上面的设备列表发我（重点看哪个是 ESP32/键盘）")
            return 1
        target_addr, target_name = target.address, target.name

    print("连接 %s (%s)" % (target_addr, target_name))
    async with BleakClient(target_addr) as client:
        print("已连接，轮询 0xFF02 等待命令 ... (Ctrl+C 退出)")
        last = None
        while True:
            try:
                v = await client.read_gatt_char(CHR_NOTIFY)
                cmd = v[0] if v else 0
                if cmd != last:
                    last = cmd
                    print("[CMD] 收到 0x%02X" % cmd)
                    if cmd == CMD_SHUTDOWN:
                        await reply(client, ACK_OK)
                        do_shutdown()
                        return
            except Exception as e:
                print("[POLL] 读取异常: %s" % e)
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n退出")
