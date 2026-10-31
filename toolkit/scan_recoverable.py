#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : scan_recoverable.py
# 用途    : 遍历指定目录，找出所有 *.L0CK3D 勒索包裹文件，解析其 588 字节页脚，
#           结合 FIEMAP 判断「是否存在独立的明文 extent」，从而把文件分为
#           可恢复（recoverable）/ 分片（fragmented）/ 无明文（no_plain）/
#           模式不支持（other）/ 过小（too_small）。
# 运行位置: 源归档 NAS（DSM / Linux，root），脚本必须在被扫描目录所在主机上运行，
#           因为要用 fcntl + FS_IOC_FIEMAP ioctl。
# 参数    : --root  扫描根目录（必填）
#           --out   扫描结果 JSON（默认 /tmp/ir_scan.json）
# 输出    : JSON：{"root", "total", "stats", "recoverable_files":[{"path","orig","mode","delta"}]}
#           其中 delta = 明文数据的「逻辑偏移 - 文件逻辑偏移」，即 recover_batch3.py /
#           stream_recover.py 需要的物理读取偏移差。
# 只读保证: 源文件只用 open(..., "rb") 读取页脚；FIEMAP ioctl 只查询 extent 映射，
#           不修改任何数据；不打开块设备写句柄，不写源卷。
# 依赖    : Python 3.8+ 标准库（ctypes/fcntl/struct/json）。
# 脱敏说明: 原始脚本中的设备路径、输出路径已改为参数/环境变量；本脚本不含任何
#           IP、账号、密码、主机名、公司名。
# 来源    : 案例 check21/scan_recoverable.py（脱敏改写，逻辑保持等价）。
# =============================================================================
"""Scan .L0CK3D files: classify recoverable (independent plain extent exists)."""
import argparse
import ctypes
import fcntl
import json
import os
import struct

FS_IOC_FIEMAP = 0xC020660B
FACT = {10: 4, 15: 5, 20: 7, 25: 9, 30: 10, 40: 14, 50: 10, 60: 20, 70: 23, 80: 27}
K = 4096
MIB = 1024 * 1024


class Ext(ctypes.Structure):
    _fields_ = [("logical", ctypes.c_uint64), ("physical", ctypes.c_uint64),
                ("length", ctypes.c_uint64), ("r64", ctypes.c_uint64 * 2),
                ("flags", ctypes.c_uint32), ("r32", ctypes.c_uint32 * 3)]


class Fm(ctypes.Structure):
    _fields_ = [("start", ctypes.c_uint64), ("length", ctypes.c_uint64),
                ("flags", ctypes.c_uint32), ("mapped", ctypes.c_uint32),
                ("count", ctypes.c_uint32), ("reserved", ctypes.c_uint32)]


def fiemap(path):
    """只读查询单个文件的 extent 映射，返回 [(logical, physical, length)]。"""
    fd = os.open(path, os.O_RDONLY)
    try:
        buf = bytearray(ctypes.sizeof(Fm))
        fm = Fm.from_buffer(buf)
        fm.start = 0
        fm.length = 0xFFFFFFFFFFFFFFFF
        fm.count = 0
        fcntl.ioctl(fd, FS_IOC_FIEMAP, buf, True)
        n = fm.mapped
        buf = bytearray(ctypes.sizeof(Fm) + n * ctypes.sizeof(Ext))
        fm = Fm.from_buffer(buf)
        fm.start = 0
        fm.length = 0xFFFFFFFFFFFFFFFF
        fm.count = n
        fcntl.ioctl(fd, FS_IOC_FIEMAP, buf, True)
        off = ctypes.sizeof(Fm)
        out = []
        for i in range(fm.mapped):
            e = Ext.from_buffer_copy(buf[off + i * ctypes.sizeof(Ext): off + (i + 1) * ctypes.sizeof(Ext)])
            out.append((e.logical, e.physical, e.length))
        return out
    finally:
        os.close(fd)


def segments(n, mode, pct):
    """按 C3RB3R 页脚记录的加密模式返回被加密的 [偏移, 长度] 段（相对原文件）。"""
    if mode == 0x24:
        return [(0, n)]
    if mode == 0x26:
        return [(0, min(n, MIB))]
    if mode == 0x25 and pct in FACT:
        length = (n // 100) * FACT[pct]
        cnt = 5 if pct == 50 else 3
        gap = length if pct == 50 else (n - 3 * length) // 2
        return [(i * (length + gap), length) for i in range(cnt)]
    return None


def main():
    ap = argparse.ArgumentParser(description="扫描 .L0CK3D 并判断可恢复性（只读）")
    ap.add_argument("--root", required=True, help="扫描根目录")
    ap.add_argument("--out", default="/tmp/ir_scan.json", help="扫描结果 JSON 输出路径")
    args = ap.parse_args()
    stats = {"mode24": 0, "mode26": 0, "mode25": 0, "other": 0,
             "recoverable": 0, "recoverable_bytes": 0,
             "fragmented": 0, "no_plain": 0, "too_small": 0}
    rec_list = []
    total = 0
    for dp, dn, fn in os.walk(args.root):
        for f in fn:
            if not f.endswith(".L0CK3D"):
                continue
            p = os.path.join(dp, f)
            total += 1
            try:
                st = os.stat(p)
                size = st.st_size
                if size < 588:
                    stats["too_small"] += 1
                    continue
                with open(p, "rb") as fh:
                    fh.seek(-588, 2)
                    b = fh.read(588)
                mode, pct = b[572], b[573]
                n = struct.unpack_from("<Q", b, 580)[0]
                if size != n + 588:
                    stats["other"] += 1
                    continue
                if mode == 0x24:
                    stats["mode24"] += 1
                    continue
                if mode == 0x26:
                    stats["mode26"] += 1
                elif mode == 0x25:
                    stats["mode25"] += 1
                else:
                    stats["other"] += 1
                    continue
                segs = segments(n, mode, pct)
                enc = [(a - a % K, ((a + l + K - 1) // K) * K) for a, l in segs]
                ex = fiemap(p)
                deltas = set()
                for (l, ph, ln) in ex:
                    e0, e1 = l, l + ln
                    if e1 > n:
                        continue
                    if any(not (e1 <= a or e0 >= b_) for a, b_ in enc):
                        continue
                    deltas.add(ph - l)
                if not deltas:
                    stats["no_plain"] += 1
                elif len(deltas) > 1:
                    stats["fragmented"] += 1
                else:
                    stats["recoverable"] += 1
                    stats["recoverable_bytes"] += n
                    rec_list.append({"path": os.path.relpath(p, args.root), "orig": n,
                                     "mode": hex(mode), "delta": deltas.pop()})
            except Exception:
                stats["other"] += 1
    out = {"root": args.root, "total": total, "stats": stats,
           "recoverable_files": rec_list[:5000]}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(json.dumps({"total": total, **stats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
