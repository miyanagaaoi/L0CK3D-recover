#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : tools_parse_free_space.py
# 用途    : 把 `btrfs-debug-tree -t 10`（free space tree）的文本 dump 解析成
#           **逻辑区间列表** JSON，供 tools_make_ranges.py 换算成物理扫描区间，
#           再交给 carve_all.py 在空闲空间里做雕刻。
# 运行位置: 操作机（只解析文本，不碰块设备）。
# 参数    : FREE_TXT   free space tree dump 文本（位置参数）
#           FREE_JSON  输出 JSON（位置参数）：[[logical_start, length], ...]
#           选项    : --last-len N  最后一个区间的长度（默认丢弃：见下）
#                     --infer-info  从 FREE_SPACE_INFO 的 extent_count 反推块组边界
#                     --dry-run     只打印统计，不写 JSON
# 生成输入: /usr/sbin/btrfs-debug-tree -t 10 /dev/mapper/vg2-volume_2 > free.txt
# 输出    : JSON 数组；stderr 打印 FREE_SPACE_EXTENT / FREE_SPACE_INFO / BITMAP 计数。
# 长度推导（重要）:
#   btrfs 的 FREE_SPACE_EXTENT 项通常 **itemsize = 0**，只记录区间起始地址，
#   区间长度不在数据里。因此本工具：
#     1) 若 item 数据里确实有 "length N"，直接用；
#     2) 否则用「下一个 FREE_SPACE_EXTENT 起始 - 本起始」作为长度上界；
#     3) 最后一个区间没有下一条可参照，默认丢弃（可用 --last-len 手工给值）。
#   第 2 步得到的是**上界**，可能把夹在中间的一小段已用空间也算进来；这对雕刻
#   无害（只多扫一点只读数据，且 carve_all.py 要求「格式+原始大小」完全匹配，
#   误报会被过滤），但会略微增加扫描时间。需要精确总量时请用
#   `btrfs filesystem usage -b <挂载点>` 交叉核对。
# 限制    : free-space bitmap 模式（FREE_SPACE_BITMAP 项）不支持，脚本会明确报错。
# 只读保证: 只读文本文件，只写 JSON。
# 依赖    : Python 3.8+ 标准库。
# 脱敏说明: 本脚本为按案例调用约定重写的等价实现（原始实现未在案例目录中留存），
#           不含任何 IP/账号/公司名；设备路径仅出现在注释示例中。
# 来源    : 案例 carve_step2.sh / carve_step4.sh 中的调用约定（重写）。
# =============================================================================
"""Parse `btrfs-debug-tree -t 10` free space tree text into [[start, length], ...]."""
import argparse
import json
import re
import sys

FREESPACE_TYPES = ("FREE_SPACE_EXTENT", "FREE_SPACE_INFO", "FREE_SPACE_BITMAP")
KEYLINE = re.compile(r"item\s+\d+\s+key\s+\(([^)]*)\)")
LEN = re.compile(r"\blength (\d+)")


def parse_key(body):
    """从 key(...) 的内容里解析出 (type, objectid, offset)，容错两种打印顺序。"""
    toks = body.split()
    typ = None
    nums = []
    for t in toks:
        if t in FREESPACE_TYPES:
            typ = t
        elif t.isdigit():
            nums.append(int(t))
    if typ is None:
        return None
    objid = nums[0] if nums else 0
    off = nums[1] if len(nums) > 1 else 0
    return typ, objid, off


def main():
    ap = argparse.ArgumentParser(description="解析 free space tree dump 为逻辑区间 JSON")
    ap.add_argument("free_txt")
    ap.add_argument("free_json")
    ap.add_argument("--last-len", type=int, default=0,
                    help="给最后一个区间指定长度（默认 0 = 丢弃该区间）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    extents = []      # [(start, length_or_None)]
    info = 0
    bitmap = 0
    cur = None        # [type, start, length_or_None]
    for line in open(args.free_txt, encoding="utf-8", errors="replace"):
        m = KEYLINE.search(line)
        if m:
            if cur and cur[0] == "FREE_SPACE_EXTENT":
                extents.append((cur[1], cur[2]))
            k = parse_key(m.group(1))
            cur = [k[0], k[1], None] if k else None
            if k and k[0] == "FREE_SPACE_INFO":
                info += 1
            elif k and k[0] == "FREE_SPACE_BITMAP":
                bitmap += 1
            continue
        if cur is None:
            continue
        if cur[0] == "FREE_SPACE_EXTENT" and cur[2] is None:
            m = LEN.search(line)
            if m:
                cur[2] = int(m.group(1))
    if cur and cur[0] == "FREE_SPACE_EXTENT":
        extents.append((cur[1], cur[2]))

    sys.stderr.write("FREE_SPACE_EXTENT=%d FREE_SPACE_INFO=%d FREE_SPACE_BITMAP=%d\n"
                     % (len(extents), info, bitmap))
    if bitmap and not extents:
        sys.exit("ERROR: 该卷使用 free-space bitmap 模式（FREE_SPACE_BITMAP 项），"
                 "本工具不解析位图。请改用挂载后 SEEK_DATA/SEEK_HOLE 方式枚举空闲区。")
    if not extents:
        sys.exit("ERROR: 未解析到任何 FREE_SPACE_EXTENT；请确认 dump 是 `-t 10` 的输出。")

    extents.sort()
    starts = [s for s, _l in extents]
    out = []
    explicit = 0
    for i, (s, l) in enumerate(extents):
        if l is not None and l > 0:
            out.append([s, l])
            explicit += 1
            continue
        if i + 1 < len(extents):
            out.append([s, starts[i + 1] - s])          # 长度上界
        elif args.last_len > 0:
            out.append([s, args.last_len])
        # 否则丢弃最后一个无法定长的区间

    # 合并首尾相接的区间
    merged = []
    for s, l in out:
        if l <= 0:
            continue
        if merged and merged[-1][0] + merged[-1][1] == s:
            merged[-1][1] += l
        else:
            merged.append([s, l])

    total = sum(l for _s, l in merged)
    sys.stderr.write("explicit_len=%d inferred=%d merged=%d total=%.2f GiB\n"
                     % (explicit, len(out) - explicit, len(merged),
                        total / float(1 << 30)))
    if args.dry_run:
        print("dry-run: %d ranges, %.2f GiB" % (len(merged), total / 1e9))
        return
    with open(args.free_json, "w", encoding="utf-8") as f:
        json.dump(merged, f)
    print("wrote %s ranges=%d total GB %.2f" % (args.free_json, len(merged), total / 1e9))


if __name__ == "__main__":
    main()
