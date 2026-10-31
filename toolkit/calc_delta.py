#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : calc_delta.py
# 用途    : 对指定的若干个 .L0CK3D 文件单独计算 delta（逻辑偏移 - 物理偏移），
#           并生成 stream_recover.py / recover_batch3.py 可直接消费的扫描 JSON。
#           用于 scan_recoverable.py 覆盖不到的场景：例如 0x25 分段加密文件被
#           scan 判为 fragmented，但手工按页脚 pct 重算加密段后仍有唯一明文区。
# 运行位置: 源归档 NAS（root）——需要 FIEMAP ioctl。
# 参数    : FILE [FILE ...]  一个或多个 .L0CK3D 路径
# 输出    : stdout 每文件一行 OK/NO_PLAIN 摘要；
#           扫描 JSON 写到 --out（默认 /dev/shm/extent/scan_archives.json，
#           放内存盘避免占用救援空间；也可指定任意路径）。
# 只读保证: 只用 O_RDONLY 打开文件做 FIEMAP 和读页脚，不写源文件/源卷。
# 依赖    : Python 3.8+ 标准库（fcntl/struct）。
# 脱敏说明: 原脚本硬编码 /dev/shm/extent/ 输出路径，现改为 --out 参数；
#           不涉及 IP、账号、密码。
# 来源    : 案例 check21/calc_delta.py（脱敏改写，逻辑保持等价）。
# =============================================================================
"""Compute delta for single 0x25 files via FIEMAP and emit scan JSON for stream_recover."""
import argparse
import fcntl
import json
import os
import struct
import sys

FIEMAP = 0xC020660B
FHSIZE = 32
FESIZE = 56
K = 4096


def fiemap(path):
    """返回 (文件大小, [(logical, physical, length, flags)])。"""
    fd = os.open(path, os.O_RDONLY)
    try:
        size = os.fstat(fd).st_size
        ents = []
        start = 0
        while start < size:
            buf = bytearray(FHSIZE + 64 * FESIZE)
            struct.pack_into('<QQIIII', buf, 0, start, min(1 << 40, size - start), 0, 0, 64, 0)
            fcntl.ioctl(fd, FIEMAP, buf)
            n = struct.unpack_from('<I', buf, 20)[0]
            if n == 0:
                break
            for i in range(n):
                off = FHSIZE + i * FESIZE
                lo, ph, ln, _r, flags = struct.unpack_from('<QQQIQ', buf, off)
                if ln:
                    ents.append((lo, ph, ln, flags))
            start = max(e[0] + e[2] for e in ents)
        return size, ents
    finally:
        os.close(fd)


def overlaps(lo, ln, ranges):
    for a, b in ranges:
        if lo < b and lo + ln > a:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="单文件 delta 计算（只读 FIEMAP）")
    ap.add_argument("--out", default="/dev/shm/extent/scan_archives.json",
                    help="扫描 JSON 输出路径")
    ap.add_argument("--mode25-pct", type=int, default=None,
                    help="强制按 0x25 的 pct 重算加密段（默认读页脚）")
    ap.add_argument("files", nargs="+", help="一个或多个 .L0CK3D 文件")
    ap.add_argument("--root", default="", help="写入 JSON 的 root 字段（相对路径基准）")
    args = ap.parse_args()

    out = []
    for p in args.files:
        size, ents = fiemap(p)
        with open(p, 'rb') as f:
            f.seek(-588, 2)
            foot = f.read(588)
        orig = struct.unpack_from('<Q', foot, 580)[0]
        mode = foot[572]
        pct = args.mode25_pct if args.mode25_pct is not None else foot[573]
        # 0x25 默认按 pct=25 的 3 段规则估算；命中 pct=50 时中间为 5 段
        part = (orig // 100) * 4
        gap = max(0, (orig - 3 * part) // 2)
        enc = [(0, part), (part + gap, 2 * part + gap), (orig - part, orig)]
        deltas = {}
        for lo, ph, ln, flags in ents:
            if flags & 1:          # FIEMAP_EXTENT_SHARED/UNKNOWN 之类的兜底跳过
                continue
            if overlaps(lo, ln, enc):
                continue
            d = ph - lo
            deltas[d] = deltas.get(d, 0) + ln
        if not deltas:
            print('NO_PLAIN', os.path.basename(p), flush=True)
            continue
        delta = max(deltas, key=deltas.get)
        rel = os.path.relpath(p, args.root) if args.root else os.path.basename(p)
        out.append({"path": rel, "orig": orig, "delta": delta})
        print('OK', os.path.basename(p),
              'size=%d orig=%d mode=0x%02x delta=%d' % (size, orig, mode, delta), flush=True)
    with open(args.out, 'w', encoding="utf-8") as f:
        json.dump({"root": args.root, "stats": {}, "recoverable_files": out}, f,
                  ensure_ascii=False)
    print('wrote %s entries=%d' % (args.out, len(out)), flush=True)


if __name__ == "__main__":
    main()
