#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : tools_walk_tree.py
# 用途    : 从 superblock 出发，遍历**当前** btrfs 子卷的 fs tree，把叶子里的
#           INODE_ITEM / INODE_REF / EXTENT_DATA 逐条输出为 JSON lines。
#           用于获取「攻击后仍在的 inode 与 extent 引用」，与孤儿叶子
#           （tools_parse_leaves.py）配合生成恢复任务（tools_make_jobs2.py）。
# 运行位置: 源归档 NAS（root），需要读块设备（只读）。
# 参数    : DEVICE CHUNKMAP SUBVOL_ID（均为位置参数，脚本从 stdin 读入在 NAS 上运行）
#           DEVICE   底层 LV，如 /dev/mapper/vg1-volume_1（不是挂载点的 cachedev_N）
#           CHUNKMAP tools_chunkmap.py 的输出
#           SUBVOL_ID 子卷 ID（btrfs subvolume list 可见）
# 用法示例: python3 - /dev/mapper/vg1-volume_1 chunkmap.txt 257 < tools_walk_tree.py > items-current.jsonl
#           （节点大小自动从 superblock 读取，不写死）
# 输出    : stdout JSON lines：{"t":"inode"/"ref"/"ext", ...}；stderr 统计与跳过信息。
# 只读保证: 设备只用 os.open(..., O_RDONLY) + os.pread；脚本无任何写调用。
# 依赖    : Python 3.8+ 标准库。
# 脱敏说明: 无 IP/账号/密码；示例设备路径为 DSM 通用 LV 命名。
# 来源    : 案例 check21/extent_tools/tools_walk_tree.py（加中文头，逻辑等价）。
# =============================================================================
"""Walk the CURRENT fs tree of one subvolume from the superblock and emit its items (read-only).

usage (root on NAS): python3 - DEVICE CHUNKMAP SUBVOL_ID < tools_walk_tree.py > items-current.jsonl
DEVICE = underlying LV, e.g. /dev/mapper/vg1-volume_1 (opened O_RDONLY). Same CHUNKMAP format
and the same JSON item format as tools_parse_leaves.py.
"""
import bisect, json, os, struct, sys

dev, chunkmap, subvol = sys.argv[1], sys.argv[2], int(sys.argv[3])
HDR = 101
chunks = sorted((int(a), int(b), int(c)) for a, b, c, _t in (l.split() for l in open(chunkmap)))
starts = [c[0] for c in chunks]
fd = os.open(dev, os.O_RDONLY)

sb = os.pread(fd, 4096, 65536)
assert sb[64:72] == b"_BHRfS_M", "no btrfs superblock"
sb_gen, root_tree = struct.unpack_from("<QQ", sb, 72)
nodesize, = struct.unpack_from("<I", sb, 148)


def read_node(logical):
    i = bisect.bisect_right(starts, logical) - 1
    lg, ln, ph = chunks[i]
    assert lg <= logical < lg + ln, "unmapped %d" % logical
    blk = os.pread(fd, nodesize, ph + logical - lg)
    bytenr, = struct.unpack_from("<Q", blk, 48)
    gen, owner, nritems = struct.unpack_from("<QQI", blk, 80)
    if bytenr != logical:
        raise ValueError("bad node at %d" % logical)
    return blk, gen, owner, nritems, blk[100]


def walk(logical, visit, stats):
    blk, gen, owner, n, level = read_node(logical)
    if level == 0:
        stats["leaves"] += 1
        visit(blk, gen, n, logical)
        return
    for i in range(n):
        ptr, = struct.unpack_from("<Q", blk, HDR + i * 33 + 17)
        try:
            walk(ptr, visit, stats)
        except Exception as e:
            stats["errors"] += 1
            sys.stderr.write("skip %d: %s\n" % (ptr, e))


found = {}


def find_root(blk, gen, n, logical):
    for i in range(n):
        objid, typ, off, doff, dsize = struct.unpack_from("<QBQII", blk, HDR + i * 25)
        if objid == subvol and typ == 132:      # ROOT_ITEM
            bytenr, = struct.unpack_from("<Q", blk, HDR + doff + 176)
            found["bytenr"] = bytenr


def emit(blk, h_gen, nritems, leaf):
    for i in range(nritems):
        objid, typ, off, doff, dsize = struct.unpack_from("<QBQII", blk, HDR + i * 25)
        d = HDR + doff
        rec = None
        if typ == 1:            # INODE_ITEM
            igen, transid, size, nbytes = struct.unpack_from("<QQQQ", blk, d)
            mode, = struct.unpack_from("<I", blk, d + 52)
            rec = {"t": "inode", "ino": objid, "size": size, "mode": mode, "transid": transid}
        elif typ == 12:         # INODE_REF
            names, q = [], d
            while q < d + dsize:
                _idx, nlen = struct.unpack_from("<QH", blk, q)
                names.append(blk[q + 10:q + 10 + nlen].decode("utf-8", "replace"))
                q += 10 + nlen
            rec = {"t": "ref", "ino": objid, "parent": off, "names": names}
        elif typ == 108:        # EXTENT_DATA
            egen, ram, comp, _e, _o, etype = struct.unpack_from("<QQBBHB", blk, d)
            rec = {"t": "ext", "ino": objid, "foff": off, "egen": egen, "comp": comp, "etype": etype}
            if etype != 0:
                dbytenr, dnum, eoff, nb = struct.unpack_from("<QQQQ", blk, d + 21)
                rec.update(dbytenr=dbytenr, dnum=dnum, eoff=eoff, nbytes=nb)
        if rec:
            rec["lgen"] = h_gen
            rec["leaf"] = leaf
            print(json.dumps(rec, ensure_ascii=False))


stats = {"leaves": 0, "errors": 0}
walk(root_tree, find_root, {"leaves": 0, "errors": 0})
sys.stderr.write("sb gen %d root tree %d subvol %d root %s\n"
                 % (sb_gen, root_tree, subvol, found.get("bytenr")))
walk(found["bytenr"], emit, stats)
sys.stderr.write(json.dumps(stats) + "\n")
