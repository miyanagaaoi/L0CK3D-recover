#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : check_carve_pil.py
# 用途    : 用 PIL 抽样解码雕刻出来的图片，统计「能真正解码」的比例，作为雕刻
#           质量的快速判据（只查文件头魔数不够，必须真解码才能排除残片）。
#           默认对 --root 下的 certain/ 与 maybe/ 两个子目录分别抽样。
# 运行位置: 操作机（需 Pillow；不碰块设备）。
# 参数    : --root   雕刻结果根目录（其下含 certain/、maybe/；可用 --subdirs 自定义）
#           --subdirs 逗号分隔的子目录名（默认 certain,maybe）
#           --sample  每个子目录最多抽样多少个文件（默认 150）
#           --seed    随机种子（默认 7，保证结果可复现）
# 输出    : stdout 每个子目录一行：total/sample/ok/bad/ok_rate
# 只读保证: 只读图片文件。
# 依赖    : Python 3.8+ 标准库 + Pillow（PIL）。
# 脱敏说明: 原脚本把 UNC 路径 \\<IP>\\share\\_carve 硬编码，现改为 --root 参数。
# 来源    : 案例 check21/check_carve_pil.py（脱敏改写）。
# =============================================================================
"""Sample-decode carved images with PIL to estimate carving quality."""
import argparse
import os
import random

from PIL import Image


def main():
    ap = argparse.ArgumentParser(description="雕刻图片 PIL 抽样解码校验（只读）")
    ap.add_argument("--root", required=True, help="雕刻结果根目录")
    ap.add_argument("--subdirs", default="certain,maybe")
    ap.add_argument("--sample", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    for sub in [s.strip() for s in args.subdirs.split(",") if s.strip()]:
        root = os.path.join(args.root, sub)
        if not os.path.isdir(root):
            print(sub, 'MISSING')
            continue
        files = []
        for dp, dn, fn in os.walk(root):
            for f in fn:
                files.append(os.path.join(dp, f))
        random.seed(args.seed)
        sample = random.sample(files, min(args.sample, len(files)))
        ok = 0
        bad = 0
        for p in sample:
            try:
                im = Image.open(p)
                im.load()
                ok += 1
            except Exception:
                bad += 1
        print(sub, 'total', len(files), 'sample', len(sample), 'ok', ok, 'bad', bad,
              'ok_rate %.1f%%' % (100.0 * ok / max(1, len(sample))))


if __name__ == "__main__":
    main()
