#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : tools_chunkmap.py
# 用途    : 把 btrfs chunk tree 的文本 dump 转成统一的 CHUNKMAP 文件，供本目录
#           其它工具（walk_tree / parse_leaves / extract_jobs / raw_reader2 等）使用。
#           输出行格式：logical length physical type
# 运行位置: 操作机（只解析文本，不碰块设备）。
# 参数    : DUMP_TXT   btrfs-debug-tree 的输出文本（位置参数）
# 生成输入: btrfs-debug-tree -t 3 /dev/mapper/cachedev_0 > chunk-tree.txt     # btrfs-progs 4.x
#           btrfs inspect-internal dump-tree -t chunk /dev/... > chunk-tree.txt  # 新版
# 输出    : stdout CHUNKMAP 行；stderr 跳过统计。
# 只读保证: 只读文本文件。
# 限制    : 只支持单设备 SINGLE / DUP（stripe 0 即完整副本）；RAID0/5/6/10 chunk
#           会打到 stderr 并跳过，需要真正的条带换算才能支持。
# 脱敏说明: 设备路径只出现在注释示例中，且为 DSM 通用路径；不含 IP/账号/主机名。
# 来源    : 案例 check21/extent_tools/tools_chunkmap.py（加中文头，逻辑保持等价）。
# =============================================================================
"""Convert a text dump of the btrfs chunk tree into CHUNKMAP lines for the other tools.

usage:
  btrfs-debug-tree -t 3 /dev/mapper/cachedev_0 > chunk-tree.txt            # btrfs-progs 4.x (DSM)
  btrfs inspect-internal dump-tree -t chunk /dev/mapper/cachedev_0 > chunk-tree.txt  # newer
  python3 tools_chunkmap.py chunk-tree.txt > chunkmap.txt

Output lines: logical length physical type   (physical = stripe 0)
Only single-device SINGLE / DUP profiles are supported (stripe 0 holds a full copy).
RAID0/5/6/10 chunks are reported on stderr and skipped; the tools would need real
stripe math for those. Read-only: this script only parses text.
"""
import re
import sys

KEY = re.compile(r"key \(FIRST_CHUNK_TREE CHUNK_ITEM (\d+)\)")
LEN = re.compile(r"\blength (\d+)")
TYPE = re.compile(r"\btype ([A-Z0-9|]+)")
STRIPE0 = re.compile(r"stripe 0 devid \d+ offset (\d+)")

cur = None
out = []
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    m = KEY.search(line)
    if m and "itemoff" in line:
        cur = {"logical": int(m.group(1))}
        continue
    if cur is None:
        continue
    for name, rx in (("length", LEN), ("type", TYPE), ("phys", STRIPE0)):
        m = rx.search(line)
        if m and name not in cur:
            cur[name] = m.group(1) if name == "type" else int(m.group(1))
    if {"length", "type", "phys"} <= cur.keys():
        t = cur["type"]
        if any(p in t for p in ("RAID0", "RAID5", "RAID6", "RAID10")):
            sys.stderr.write("skip striped chunk %d %s\n" % (cur["logical"], t))
        else:
            out.append((cur["logical"], cur["length"], cur["phys"], t))
        cur = None

for row in sorted(out):
    print("%d %d %d %s" % row)
sys.stderr.write("%d chunks\n" % len(out))
