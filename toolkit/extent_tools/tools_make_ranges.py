#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : tools_make_ranges.py
# 用途    : 把「逻辑地址的空闲区间」换算成「物理地址的扫描区间」，产出
#           carve_all.py 需要的 ranges 文件（每行 "物理偏移 长度"）。
#           btrfs 的逻辑地址 ≠ 物理地址，必须按 chunk 映射逐段换算；
#           跨 chunk 的空闲区间会被切成多段，相邻段会合并以减少 seek。
# 运行位置: 操作机（纯计算，不碰块设备）。
# 参数    : CHUNKS FREE MINLEN OUT（位置参数）
#           CHUNKS  二选一：
#                    · tools_chunkmap.py 输出的 CHUNKMAP 文本（"logical length physical type"）
#                      —— 按 .txt 后缀或内容自动识别；
#                    · JSON 数组 [{"l":logical,"len":长度,"phys":物理,"end":逻辑结束}, ...]
#           FREE    tools_parse_free_space.py 输出的 JSON：[[logical_start, length], ...]
#                   也接受 [{"start":..,"len":..}, ...]
#           MINLEN  丢弃长度小于该值的区间（字节，0 表示不丢）
#           OUT     输出文件：每行 "物理偏移 长度"
# 输出    : OUT 文件；stderr 区间数/总容量统计。
# 限制    : 只支持单设备 SINGLE/DUP（stripe 0 即完整副本）。RAID 条带卷的物理
#           区间不能这样线性映射，需另外实现条带换算。
# 只读保证: 只读 JSON/文本输入，只写 OUT。
# 依赖    : Python 3.8+ 标准库。
# 脱敏说明: 本脚本为按案例调用约定重写的等价实现（原始实现未在案例目录中留存）。
# 来源    : 案例 carve_step2.sh / carve_step3.sh / carve_step4.sh 中的调用约定（重写）。
# =============================================================================
"""Map logical free-space extents through the chunk map into physical scan ranges."""
import json
import os
import sys


def load_chunks(path):
    """返回 [(logical, length, physical)]，已按 logical 排序。"""
    text = open(path, encoding="utf-8", errors="replace").read()
    out = []
    if path.lower().endswith(".json") or text.lstrip().startswith("["):
        for c in json.loads(text):
            if "l" in c:
                out.append((int(c["l"]), int(c["len"]), int(c["phys"])))
            else:
                out.append((int(c["logical"]), int(c["length"]), int(c["physical"])))
    else:
        for line in text.splitlines():
            p = line.split()
            if len(p) >= 3:
                out.append((int(p[0]), int(p[1]), int(p[2])))
    out.sort()
    return out


def load_free(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for it in data:
        if isinstance(it, dict):
            out.append((int(it.get("start", it.get("logical"))), int(it.get("len", it.get("length")))))
        else:
            out.append((int(it[0]), int(it[1])))
    out.sort()
    return out


def main():
    if len(sys.argv) != 5:
        sys.exit("usage: tools_make_ranges.py CHUNKS FREE MINLEN OUT")
    chunks_path, free_path, minlen, out_path = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
    chunks = load_chunks(chunks_path)
    free = load_free(free_path)

    starts = [c[0] for c in chunks]
    pieces = []
    for s, l in free:
        pos, end = s, s + l
        guard = 0
        while pos < end:
            guard += 1
            if guard > (len(chunks) + len(free) + 16) * 4:
                sys.exit("ERROR: 映射循环异常（pos=%d end=%d），请检查 chunkmap 是否有序/完整" % (pos, end))
            lo, hi = 0, len(starts)
            while lo < hi:                       # 二分找 pos 所在（或前一个）chunk
                mid = (lo + hi) // 2
                if starts[mid] <= pos:
                    lo = mid + 1
                else:
                    hi = mid
            i = lo - 1
            if i < 0:                            # 位于第一个 chunk 之前的洞
                if not chunks:
                    break
                pos = min(starts[0], end)
                continue
            lg, ln, ph = chunks[i]
            cend = lg + ln
            if pos >= cend:                      # 落在 chunk 之间的洞：跳到下一个 chunk 起点
                if i + 1 < len(chunks):
                    pos = min(starts[i + 1], end)
                    continue
                break                            # 最后一个 chunk 之后：剩余部分无映射
            take = min(end, cend) - pos
            pieces.append((ph + (pos - lg), take))
            pos += take

    pieces.sort()
    merged = []
    for a, l in pieces:
        if merged and merged[-1][0] + merged[-1][1] == a:
            merged[-1][1] += l
        else:
            merged.append([a, l])
    if minlen > 0:
        merged = [m for m in merged if m[1] >= minlen]

    with open(out_path, "w", encoding="utf-8") as f:
        for a, l in merged:
            f.write("%d %d\n" % (a, l))
    total = sum(l for _a, l in merged)
    sys.stderr.write("chunks=%d free=%d pieces=%d merged=%d total=%.2f GiB\n"
                     % (len(chunks), len(free), len(pieces), len(merged),
                        total / float(1 << 30)))
    print("ranges %d total GB %.2f" % (len(merged), total / 1e9))


if __name__ == "__main__":
    main()
