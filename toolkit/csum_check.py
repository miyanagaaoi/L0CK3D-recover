#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : csum_check.py
# 用途    : btrfs 数据校验和（csum）反查验证。直接从块设备解析 superblock →
#           root tree → csum tree（tree 7），取出抽样文件的每个 4 KiB 块 CRC32C，
#           再按 chunkmap 把「逻辑偏移」换算成物理偏移读回数据，逐块重算比对。
#           这是唯一能证明「恢复出来的字节与攻击前完全一致」的硬证据：
#           ok=0 bad=0 missing=0 表示该文件所有块与文件系统记录的校验和一致。
# 运行位置: 源归档 NAS（root），需要读块设备。
# 参数    : --dev      源块设备（默认 /dev/mapper/cachedev_0）
#           --chunkmap chunkmap 文件，每行 "logical length physical [type]"，
#                      由 extent_tools/tools_chunkmap.py 生成
#           --scan     一个或多个扫描 JSON（逗号分隔），从 recoverable_files 抽样
#           --sample   每个扫描 JSON 抽多少个文件（默认 5）
#           --out      结果 JSON（默认 /dev/shm/extent/csum_check_result.json）
# 输出    : stdout 每文件 blocks/ok/bad/missing 与 TOTAL；
#           JSON：[{"path","blocks","ok","bad","missing"}]
# 只读保证: 块设备以 'rb' 打开且只用 seek+read；不改 superblock，不写任何设备。
# 依赖    : Python 3.8+ 标准库。
# 脱敏说明: 原脚本把设备、chunkmap、扫描路径写成位置参数默认值（含真实共享目录
#           片段），现改为显式参数并去掉真实路径；不含 IP/账号/公司名。
# 来源    : 案例 check21/csum_check.py（脱敏改写为 argparse 版本）。
# =============================================================================
"""Sample csum verification: compare recovered data blocks against btrfs CRC32C csum tree."""
import argparse
import bisect
import json
import struct
import sys

HDR = 101
K = 4096


def make_table():
    tab = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
        tab.append(c)
    return tab


TAB = make_table()


def crc32c(data):
    c = 0xFFFFFFFF
    for b in data:
        c = (c >> 8) ^ TAB[(c ^ b) & 0xFF]
    return c ^ 0xFFFFFFFF


def main():
    ap = argparse.ArgumentParser(description="btrfs csum 逐块验证（只读块设备）")
    ap.add_argument("--dev", default="/dev/mapper/cachedev_0")
    ap.add_argument("--chunkmap", required=True)
    ap.add_argument("--scan", required=True, help="扫描 JSON，多个用逗号分隔")
    ap.add_argument("--sample", type=int, default=5)
    ap.add_argument("--out", default="/dev/shm/extent/csum_check_result.json")
    args = ap.parse_args()

    chunks = []
    for line in open(args.chunkmap):
        p = line.split()
        if len(p) >= 3:
            chunks.append((int(p[0]), int(p[1]), int(p[2])))
    chunks.sort()
    cstarts = [c[0] for c in chunks]
    devf = open(args.dev, 'rb', buffering=0)

    def read_logical(logical, n):
        out = bytearray()
        pos, left = logical, n
        while left > 0:
            i = bisect.bisect_right(cstarts, pos) - 1
            if i < 0:
                raise ValueError('bad logical %d' % pos)
            lg, ln, ph = chunks[i]
            take = min(left, lg + ln - pos)
            devf.seek(ph + (pos - lg))
            data = devf.read(take)
            if len(data) != take:
                raise ValueError('short read')
            out += data
            pos += take
            left -= take
        return bytes(out)

    # --- superblock ---
    devf.seek(65536)
    sup = devf.read(4096)
    if sup[0x40:0x48] != b'_BHRfS_M':
        sys.exit('no btrfs superblock at 65536')
    root_tree, = struct.unpack_from('<Q', sup, 0x50)
    nodesize, = struct.unpack_from('<I', sup, 0x94)
    root_level = sup[0xc6]
    print('root_tree', root_tree, 'level', root_level, 'nodesize', nodesize, flush=True)

    def read_node(logical):
        return read_logical(logical, nodesize)

    def walk(bytenr, leaf_cb, stats, depth=0):
        blk = read_node(bytenr)
        lvl = blk[100]
        nritems, = struct.unpack_from('<I', blk, 96)
        if lvl == 0:
            stats['leaves'] += 1
            leaf_cb(blk, nritems)
            return
        for i in range(nritems):
            ptr, = struct.unpack_from('<Q', blk, HDR + i * 33 + 17)
            try:
                walk(ptr, leaf_cb, stats, depth + 1)
            except Exception:
                stats['err'] += 1

    # --- 在 root tree 里定位 csum tree（对象号 7，类型 132=ROOT_ITEM）---
    csum_root = [None]
    csum_level = [None]

    def find_root_cb(blk, nritems):
        for i in range(nritems):
            objid, typ, off, doff, dsize = struct.unpack_from('<QBQII', blk, HDR + i * 25)
            if objid == 7 and typ == 132:
                data_off = HDR + doff
                bytenr, = struct.unpack_from('<Q', blk, data_off + 176)
                level = blk[data_off + 238]
                csum_root[0] = bytenr
                csum_level[0] = level

    walk(root_tree, find_root_cb, {'leaves': 0, 'err': 0})
    print('csum_root', csum_root[0], 'level', csum_level[0], flush=True)
    if csum_root[0] is None:
        sys.exit('csum root not found')

    # --- 抽样文件范围 ---
    ranges = []
    files = []
    for scan in args.scan.split(','):
        try:
            d = json.load(open(scan))
        except Exception:
            continue
        for it in d.get('recoverable_files', [])[:args.sample]:
            n = int(it['orig'])
            delta = int(it['delta'])
            files.append({'path': it['path'], 'orig': n, 'delta': delta})
            ranges.append((delta, delta + n))
    ranges.sort()
    rstarts = [r[0] for r in ranges]

    def hit(addr):
        i = bisect.bisect_right(rstarts, addr) - 1
        return i >= 0 and addr < ranges[i][1]

    csum_map = {}

    def csum_leaf_cb(blk, nritems):
        for i in range(nritems):
            objid, typ, off, doff, dsize = struct.unpack_from('<QBQII', blk, HDR + i * 25)
            if typ != 128:      # 128 = EXTENT_CSUM
                continue
            data = blk[HDR + doff:HDR + doff + dsize]
            nblk = dsize // 4
            for j in range(nblk):
                addr = off + j * K
                if hit(addr):
                    csum_map[addr] = struct.unpack_from('<I', data, j * 4)[0]

    stats = {'leaves': 0, 'err': 0}
    walk(csum_root[0], csum_leaf_cb, stats)
    print('csum walk', stats, 'extracted', len(csum_map), flush=True)

    res = []
    for f in files:
        delta, n = f['delta'], f['orig']
        try:
            data = read_logical(delta, n)
        except Exception as e:
            res.append({'path': f['path'], 'error': str(e)})
            continue
        full = n // K
        ok = bad = missing = 0
        for j in range(full):
            addr = delta + j * K
            want = csum_map.get(addr)
            if want is None:
                missing += 1
                continue
            got = crc32c(data[j * K:(j + 1) * K])
            if got == want:
                ok += 1
            else:
                bad += 1
        res.append({'path': f['path'], 'blocks': full, 'ok': ok, 'bad': bad, 'missing': missing})
        print('%-70s blocks=%d ok=%d bad=%d missing=%d'
              % (f['path'][-70:], full, ok, bad, missing), flush=True)

    tot = {'ok': 0, 'bad': 0, 'missing': 0, 'blocks': 0}
    for r in res:
        for k in tot:
            tot[k] += r.get(k, 0)
    print('TOTAL', tot, flush=True)
    json.dump(res, open(args.out, 'w'), ensure_ascii=False)


if __name__ == "__main__":
    main()
