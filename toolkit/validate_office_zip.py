#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : validate_office_zip.py
# 用途    : 对雕刻/恢复出来的 ZIP 类容器（.zip/.docx/.xlsx/.pptx）做完整性校验：
#           用 zipfile 打开并 testzip() 逐条目校验 CRC，同时识别是否 OOXML
#           （含 [Content_Types].xml 即 Office 文档）。用于判断雕刻出的 ZIP 是
#           完整文档还是被截断的残片。
# 运行位置: 操作机（纯本地文件读，不碰块设备）。
# 参数    : --root  待校验目录（必填）
#           --out   CSV 输出（默认脚本同目录 office_zip_validate.csv）
# 输出    : CSV：rel, entries, is_office, status(OK / FAIL 原因)
#           stdout：total / zip_ok / office_doc / bad
# 只读保证: zipfile 以只读模式打开；不修改被校验文件。
# 依赖    : Python 3.8+ 标准库（zipfile）。
# 脱敏说明: 原脚本文件名与路径含客户简称及 UNC IP 路径，现改名 validate_office_zip.py
#           并把 root 改为参数；CSV 仅记录操作者目录内的相对路径。
# 来源    : 案例 check21/validate_<客户缩写>_office.py（脱敏改名并改写为参数化）。
# =============================================================================
"""Validate ZIP/OOXML containers carved or restored from the incident."""
import argparse
import csv
import os
import zipfile


def main():
    ap = argparse.ArgumentParser(description="ZIP/OOXML 容器完整性校验（只读）")
    ap.add_argument("--root", required=True, help="待校验目录")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "office_zip_validate.csv"))
    args = ap.parse_args()

    ok = bad = office = 0
    rows = []
    for dp, dn, fn in os.walk(args.root):
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, args.root)
            try:
                with zipfile.ZipFile(p) as z:
                    names = z.namelist()
                    if z.testzip() is not None:
                        bad += 1
                        rows.append([rel, len(names), 0, 'FAIL crc'])
                        continue
                    ok += 1
                    is_office = '[Content_Types].xml' in names
                    if is_office:
                        office += 1
                    rows.append([rel, len(names), int(is_office), 'OK'])
            except Exception as e:
                bad += 1
                rows.append([rel, 0, 0, 'FAIL ' + str(e)[:40]])
    with open(args.out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['rel', 'entries', 'is_office', 'status'])
        w.writerows(rows)
    print('total', ok + bad, 'zip_ok', ok, 'office_doc', office, 'bad', bad)
    print('csv', args.out)


if __name__ == "__main__":
    main()
