#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : tools_parse_leaves.py
# 用途    : 直接解析 `btrfs-find-root -a -o OWNER` 找出的**孤儿旧叶子**（攻击前的
#           fs tree 叶子块，已不被当前树引用，但块内容仍留在盘上），输出与
#           tools_walk_tree.py 相同格式的 JSON lines（另含 DIR_INDEX）。
#           这些旧 EXTENT_DATA 记录 ≈ 攻击前的 extent 布局，是精确恢复的关键。
# 运行位置: 源归档 NAS（root），只读块设备与块映射。
# 参数    : DEVICE CHUNKMAP LEAFLIST OWNER（位置参数，脚本从 stdin 读入）
#           DEVICE   底层 LV，如 /dev/mapper/vg1-volume_1
#           CHUNKMAP tools_chunkmap.py 的输出
#           LEAFLIST "gen level bytenr" 每行一条，来自 btrfs-find-root
#           OWNER    子卷 ID（用于校验叶子块头 owner 字段，过滤误命中）
# 用法示例: python3 - /dev/mapper/vg1-volume_1 chunkmap.txt roots.txt 257 < tools_parse_leaves.py > items-old.jsonl
# 输出    : stdout JSON lines：inode / ref / dir / ext；stderr 统计。
# 注意    : NODESIZE 默认 16 KiB（btrfs 默认）。若卷 nodesize 不同需修改 NODESIZE。
# 只读保证: 设备只以 O_RDONLY 打开 + os.pread；无写调用。
# 依赖    : Python 3.8+ 标准库。
# 脱敏说明: 不含 IP/账号/密码/公司名；设备路径为通用 DSM LV 命名。
# 来源    : 案例 check21/extent_tools/tools_parse_leaves.py（加中文头，逻辑等价）。
# =============================================================================
"""Parse orphaned btrfs fs-tree leaves of one subvolume straight from the block device.

Usage on NAS (root, read-only):
  python3 - DEVICE CHUNKMAP LEAFLIST OWNER < tools_parse_leaves.py > items.jsonl
DEVICE = underlying LV, e.g. /dev/mapper/vg1-volume_1 (opened O_RDONLY)
CHUNKMAP lines: logical length physical type   (stripe 0, single/DUP)
LEAFLIST lines: gen level bytenr                (from btrfs-find-root -a -o OWNER)
Emits one JSON object per INODE_ITEM / INODE_REF / DIR_INDEX / EXTENT_DATA item,
tagged with the leaf generation. Never writes to the device.
"""
import bisect, json, os, struct, sys

dev, chunkmap, leaflist, owner = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
NODESIZE = 16384
HDR = 101

chunks = []
for line in open(chunkmap):
    lg, ln, ph, _t = line.split()
    chunks.append((int(lg), int(ln), int(ph)))
chunks.sort()
starts = [c[0] for c in chunks]


def phys(logical):
    i = bisect.bisect_right(starts, logical) - 1
    if i < 0:
        return None
    lg, ln, ph = chunks[i]
    if logical >= lg + ln:
        return None
    return ph + (logical - lg)


fd = os.open(dev, os.O_RDONLY)
seen = set()
stats = {"leaves": 0, "bad": 0, "items": 0}
for line in open(leaflist):
    gen, level, bytenr = (int(x) for x in line.split())
    if level != 0 or bytenr in seen:
        continue
    seen.add(bytenr)
    p = phys(bytenr)
    if p is None:
        stats["bad"] += 1
        continue
    blk = os.pread(fd, NODESIZE, p)
    h_bytenr, = struct.unpack_from("<Q", blk, 48)
    h_gen, h_owner, nritems = struct.unpack_from("<QQI", blk, 80)
    h_level = blk[100]
    if h_bytenr != bytenr or h_owner != owner or h_level != 0 or nritems > 1000:
        stats["bad"] += 1
        continue
    stats["leaves"] += 1
    for i in range(nritems):
        objid, typ, off, doff, dsize = struct.unpack_from("<QBQII", blk, HDR + i * 25)
        d = HDR + doff
        if d + dsize > NODESIZE:
            continue
        rec = None
        if typ == 1:  # INODE_ITEM
            igen, transid, size, nbytes = struct.unpack_from("<QQQQ", blk, d)
            mode, = struct.unpack_from("<I", blk, d + 52)
            mtime, = struct.unpack_from("<Q", blk, d + 136)
            rec = {"t": "inode", "ino": objid, "size": size, "mode": mode, "transid": transid, "mtime": mtime}
        elif typ == 12:  # INODE_REF (offset = parent dir)
            names, q = [], d
            while q < d + dsize:
                _idx, nlen = struct.unpack_from("<QH", blk, q)
                names.append(blk[q + 10:q + 10 + nlen].decode("utf-8", "replace"))
                q += 10 + nlen
            rec = {"t": "ref", "ino": objid, "parent": off, "names": names}
        elif typ == 96:  # DIR_INDEX (objid = parent dir)
            child, = struct.unpack_from("<Q", blk, d)
            nlen, = struct.unpack_from("<H", blk, d + 27)
            name = blk[d + 30:d + 30 + nlen].decode("utf-8", "replace")
            rec = {"t": "dir", "parent": objid, "ino": child, "name": name}
        elif typ == 108:  # EXTENT_DATA (offset = file offset)
            egen, ram, comp, _enc, _oth, etype = struct.unpack_from("<QQBBHB", blk, d)
            rec = {"t": "ext", "ino": objid, "foff": off, "egen": egen, "comp": comp, "etype": etype}
            if etype == 0:
                rec["inline_len"] = dsize - 21
                rec["ram"] = ram
            else:
                dbytenr, dnum, eoff, nbytes = struct.unpack_from("<QQQQ", blk, d + 21)
                rec.update(dbytenr=dbytenr, dnum=dnum, eoff=eoff, nbytes=nbytes)
        if rec:
            rec["lgen"] = h_gen
            rec["leaf"] = bytenr
            stats["items"] += 1
            print(json.dumps(rec, ensure_ascii=False))
sys.stderr.write(json.dumps(stats) + "\n")
