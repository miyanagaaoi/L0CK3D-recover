#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : tools_raw_reader2.py
# 用途    : 只读的「逻辑地址 → 物理地址」随机读取器，既是可复用库（import 后调用
#           RawReader.read(logical, n)），也是命令行小工具：用来手工验证某个
#           bytenr / extent 对应的原始字节是什么（看文件头魔数、页脚标记等）。
#           所有基于 extent 的恢复脚本（extract_jobs / extract_big / stream_recover）
#           内部都是这段逻辑，这里单独抽出来便于排错。
# 运行位置: 源归档 NAS（root），需要读块设备。
# 参数    : DEVICE CHUNKMAP LOGICAL LENGTH [OUT]
#           DEVICE   底层 LV，如 /dev/mapper/vg1-volume_1（O_RDONLY 打开）
#           CHUNKMAP tools_chunkmap.py 输出的 block map
#           LOGICAL  起始逻辑字节（= bytenr / extent 的 disk_bytenr + eoff）
#           LENGTH   读取字节数
#           OUT      可选，写到此文件；省略则二进制写到 stdout
#           附加选项：--hex N 只看前 N 字节的十六进制；--check 只校验映射是否
#           连续覆盖 [LOGICAL, LOGICAL+LENGTH)，不读数据。
# 输出    : 数据 / 十六进制 / 校验结果；stderr 映射统计。
# 只读保证: 设备只以 O_RDONLY 打开并只用 os.pread；绝不 pwrite/写设备。
# 依赖    : Python 3.8+ 标准库。
# 脱敏说明: 本脚本为按案例调用约定重写的等价实现（原始实现未在案例目录中留存），
#           不含 IP/账号/主机名/公司名；设备路径示例为通用 DSM LV 命名。
# 来源    : 案例 check21 中 recover_batch3.py / stream_recover.py 的 Reader 类
#           （抽离为独立可复用工具，重写）。
# =============================================================================
"""Read-only logical→physical reader over a btrfs chunk map (library + CLI)."""
import argparse
import bisect
import os
import sys


class RawReader:
    """按 CHUNKMAP 把 btrfs 逻辑地址换成物理偏移，只读随机读取。"""

    def __init__(self, dev, chunkmap):
        self.dev = dev
        chunks = []
        for line in open(chunkmap, encoding="utf-8"):
            p = line.split()
            if len(p) >= 3:
                chunks.append((int(p[0]), int(p[1]), int(p[2])))
        chunks.sort()
        self.chunks = chunks
        self.starts = [c[0] for c in chunks]
        self.fd = os.open(dev, os.O_RDONLY)

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass

    def _phys(self, logical):
        i = bisect.bisect_right(self.starts, logical) - 1
        if i < 0:
            return None
        lg, ln, ph = self.chunks[i]
        if logical >= lg + ln:
            return None
        return ph + (logical - lg)

    def check(self, logical, length):
        """返回 (是否全程有映射, 实际可读字节数)。"""
        pos, left, want = logical, length, length
        while left > 0:
            i = bisect.bisect_right(self.starts, pos) - 1
            if i < 0:
                return False, want - left
            lg, ln, ph = self.chunks[i]
            if pos >= lg + ln:
                return False, want - left
            take = min(left, lg + ln - pos)
            pos += take
            left -= take
        return True, want

    def read(self, logical, n):
        """读满 n 字节；遇到未映射/空洞返回 None，短读抛 IOError。"""
        end = logical + n
        pos = logical
        parts = []
        while pos < end:
            ph = self._phys(pos)
            if ph is None:
                return None
            i = bisect.bisect_right(self.starts, pos) - 1
            lg, ln, _p = self.chunks[i]
            take = min(end - pos, lg + ln - pos)
            data = os.pread(self.fd, take, ph)
            if len(data) != take:
                raise IOError("short read at logical %d" % pos)
            parts.append(data)
            pos += take
        return b"".join(parts)


def main():
    ap = argparse.ArgumentParser(description="只读逻辑地址读取（btrfs chunkmap）")
    ap.add_argument("dev")
    ap.add_argument("chunkmap")
    ap.add_argument("logical", type=int)
    ap.add_argument("length", type=int)
    ap.add_argument("out", nargs="?", default=None)
    ap.add_argument("--hex", type=int, default=0, help="只打印前 N 字节十六进制")
    ap.add_argument("--check", action="store_true", help="只校验映射连续性")
    args = ap.parse_args()

    rd = RawReader(args.dev, args.chunkmap)
    try:
        if args.check:
            ok, got = rd.check(args.logical, args.length)
            print("mapped=%s bytes=%d/%d" % (ok, got, args.length))
            return 0 if ok else 2
        data = rd.read(args.logical, args.length)
        if data is None:
            sys.stderr.write("ERROR: 逻辑区间未被 chunkmap 完整覆盖\n")
            return 2
        if args.hex:
            print(data[:args.hex].hex())
        elif args.out:
            with open(args.out, "wb") as f:
                f.write(data)
            sys.stderr.write("wrote %d bytes to %s\n" % (len(data), args.out))
        else:
            sys.stdout.buffer.write(data)
        return 0
    finally:
        rd.close()


if __name__ == "__main__":
    sys.exit(main())
