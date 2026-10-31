#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : carve_all.py
# 用途    : 空闲空间雕刻（carving）候选提取。在 btrfs 空闲空间（物理区间）里按
#           文件头魔数（JPEG/PNG/PDF/ZIP）扫描，用「文件尾魔数」确定结束位置，
#           并只保留长度恰好命中「加密文件清单」中 (格式, 原始大小) 组合的候选，
#           极大降低误报。适用于 0x24（全文加密）文件：原文件内容已被 CoW 覆盖，
#           只能靠空闲区残留数据 + 原始大小匹配找回。
# 运行位置: 源归档 NAS（root），需要直接读块设备。
# 参数    : --list    加密文件清单 TSV：root<TAB>相对路径<TAB>原始大小<TAB>格式(img/pdf/zip)
#           --ranges  物理扫描区间文件，每行 "物理偏移 长度"（由 tools_make_ranges.py 生成）
#           --out     候选结果 JSON（[[fmt, 物理偏移, 长度, 相对路径], ...]）
#           --dev     源块设备（默认 /dev/mapper/cachedev_0）
#           --block   单次读取块大小（默认 8 MiB）
# 输出    : JSON 候选数组 + stdout 进度。
# 只读保证: 块设备只用 os.open(..., O_RDONLY) + os.pread；除输出 JSON 外不写任何文件；
#           不写源卷、不做 balance/scrub、不 remount。
# 依赖    : Python 3.8+ 标准库。
# 脱敏说明: 原脚本硬编码 /dev/shm/extent/ 路径与设备路径，现全部参数化；
#           --list 由操作者自己生成，脚本内不含任何真实路径/公司名。
# 来源    : 案例 check21/carve_all.py（脱敏改写，逻辑保持等价）。
# =============================================================================
"""Carve img/pdf/zip candidates from free space, matching (fmt, N) lists."""
import argparse
import json
import os
import struct


def main():
    ap = argparse.ArgumentParser(description="空闲空间雕刻候选提取（只读块设备）")
    ap.add_argument("--list", required=True, help="清单 TSV: root<TAB>rel<TAB>size<TAB>fmt")
    ap.add_argument("--ranges", required=True, help="物理区间文件，每行: offset length")
    ap.add_argument("--out", required=True, help="候选 JSON 输出")
    ap.add_argument("--dev", default=os.environ.get("SRC_DEV", "/dev/mapper/cachedev_0"))
    ap.add_argument("--block", type=int, default=8 << 20)
    ap.add_argument("--max-file", type=int, default=4 << 20, help="单候选最大长度（默认 4 MiB）")
    args = ap.parse_args()

    Ns = {'img': set(), 'pdf': set(), 'zip': set()}
    files = {}
    for line in open(args.list, encoding='utf-8'):
        parts = line.rstrip('\n').split('\t')
        if len(parts) != 4:
            continue
        root, rel, n, fmt = parts
        if fmt not in Ns:
            continue
        n = int(n)
        Ns[fmt].add(n)
        base = rel.lower()[:-7]
        if fmt == 'img':
            if base.endswith(('.jpg', '.jpeg')):
                f = 'jpeg'
            elif base.endswith('.png'):
                f = 'png'
            else:
                continue
        else:
            f = fmt
        files.setdefault((f, n), []).append(rel)
    print('N sets', {k: len(v) for k, v in Ns.items()}, 'keys', len(files), flush=True)

    ranges = []
    for line in open(args.ranges):
        a, b = line.split()
        ranges.append((int(a), int(b)))
    dev = os.open(args.dev, os.O_RDONLY)
    BLK = args.block
    cands = []
    scanned = 0
    for a, b in ranges:
        pos, left = a, b
        buf = b''
        base = 0
        while left > 0:
            take = min(BLK, left)
            buf = buf + os.pread(dev, take, pos)
            pos += take
            left -= take
            i = 0
            consumed = len(buf)
            while True:
                best = None
                for sig, fmt in ((b'\xff\xd8\xff', 'jpeg'), (b'\x89PNG\r\n\x1a\n', 'png'),
                                 (b'%PDF', 'pdf'), (b'PK\x03\x04', 'zip')):
                    j = buf.find(sig, i)
                    if j >= 0 and (best is None or j < best[0]):
                        best = (j, fmt)
                if best is None:
                    break
                p0, fmt = best
                if fmt == 'jpeg':
                    e = buf.find(b'\xff\xd9', p0 + 3)
                    need = e + 2 if e >= 0 else -1
                elif fmt == 'png':
                    e = buf.find(b'IEND\xae\x42\x60\x82', p0 + 8)
                    need = e + 8 if e >= 0 else -1
                elif fmt == 'pdf':
                    e = buf.find(b'%%EOF', p0 + 5)
                    need = e + 5 if e >= 0 else -1
                else:
                    e = buf.find(b'PK\x05\x06', p0 + 4)
                    if e >= 0 and e + 22 <= len(buf):
                        clen = struct.unpack_from('<H', buf, e + 20)[0]
                        need = e + 22 + clen
                    else:
                        need = -1
                if need < 0:
                    consumed = p0
                    break
                ln = need - p0
                key = 'img' if fmt in ('jpeg', 'png') else fmt
                if 512 <= ln <= args.max_file and ln in Ns[key]:
                    rel = files.get((fmt, ln)) or files.get(('jpeg', ln)) or files.get(('png', ln))
                    if rel:
                        cands.append([fmt, a + (base + p0), ln, rel[0]])
                i = p0 + 4
            base += consumed
            buf = buf[consumed:]
        scanned += b
        if scanned % (4 << 30) < BLK:
            print('scanned %.1f GB cands %d' % (scanned / 1e9, len(cands)), flush=True)
    print('CANDIDATES', len(cands), flush=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(cands, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
