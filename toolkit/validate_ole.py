#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : validate_ole.py
# 用途    : 对雕刻/恢复出来的 OLE 复合文档（.doc/.xls/.ppt/.msg）做结构校验：
#           1) 前 8 字节必须是 OLE 签名 D0CF11E0A1B11AE1；
#           2) 扇区大小来自头部（512 或 4096），目录项必须可读；
#           3) 名称长度、对象类型、文件大小与扇区对齐关系合法。
#           用于在「可能残缺」的雕刻结果里筛出结构完整的文档，避免交付坏文件。
# 运行位置: 操作机（Linux/macOS/Windows 均可，纯本地文件读，不碰块设备）。
# 参数    : --root  待校验目录（必填）
#           --out   CSV 输出（默认脚本同目录 ole_validate.csv，UTF-8 with BOM）
#           --only 只处理该扩展名（如 .doc，默认全部）
# 输出    : CSV：rel, size, status(OK / BAD_HEADER / BAD_DIR / BAD_STRUCT / FAIL 原因)
# 只读保证: 只用 open(..., 'rb') 读文件头与目录扇区；不修改被校验文件。
# 依赖    : Python 3.8+ 标准库。
# 脱敏说明: 原脚本把 UNC 路径 \\<IP>\\share\\... 硬编码，现改为 --root 参数；
#           输出 CSV 里的相对路径来自操作者自己的目录，脚本内不含敏感路径。
# 来源    : 案例 check21/validate_ole.py（脱敏改写，逻辑保持等价）。
# =============================================================================
"""Structural validation of OLE compound documents (.doc/.xls/.ppt/.msg)."""
import argparse
import csv
import os
import struct


def main():
    ap = argparse.ArgumentParser(description="OLE 复合文档结构校验（只读）")
    ap.add_argument("--root", required=True, help="待校验目录")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "ole_validate.csv"))
    ap.add_argument("--only", default=None, help="只校验指定扩展名，如 .doc")
    args = ap.parse_args()

    ok = bad = 0
    rows = []
    for dp, dn, fn in os.walk(args.root):
        for f in fn:
            if args.only and not f.lower().endswith(args.only.lower()):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, args.root)
            try:
                size = os.path.getsize(p)
                with open(p, 'rb') as fh:
                    hdr = fh.read(512)
                    if len(hdr) < 512 or hdr[:8] != b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
                        bad += 1
                        rows.append([rel, size, 'BAD_HEADER'])
                        continue
                    sshift = struct.unpack_from('<H', hdr, 30)[0]
                    ss = 1 << sshift if sshift in (9, 12) else 512
                    dirstart = struct.unpack_from('<I', hdr, 48)[0]
                    fh.seek(512 + dirstart * ss)
                    entry = fh.read(128)
                    if len(entry) < 128:
                        bad += 1
                        rows.append([rel, size, 'BAD_DIR'])
                        continue
                    nl = struct.unpack_from('<H', entry, 64)[0]
                    typ = entry[66]
                    if 0x10 <= nl <= 0x80 and typ in (0, 1, 2, 5) and (size - 512) % ss == 0:
                        ok += 1
                        rows.append([rel, size, 'OK'])
                    else:
                        bad += 1
                        rows.append([rel, size, 'BAD_STRUCT'])
            except Exception as e:
                bad += 1
                rows.append([rel, 0, 'FAIL ' + str(e)[:40]])
    with open(args.out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['rel', 'size', 'status'])
        w.writerows(rows)
    print('total', ok + bad, 'ok', ok, 'bad', bad)
    print('csv', args.out)


if __name__ == "__main__":
    main()
