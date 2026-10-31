#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : tools_make_jobs2.py
# 用途    : 为某个目录前缀下所有仍在的加密文件生成恢复任务（jobs.jsonl）。
#           同时使用「当前树的 extent 引用」和「孤儿旧叶子的 extent 引用」：
#           先放精确的旧引用（foff/dbytenr/eoff/nbytes 全对），再补按整 extent
#           合成的引用（用于旧引用只覆盖文件一部分的情况）。
# 运行位置: 操作机（纯 JSON/TSV 处理，不碰块设备）。
# 参数    : CURRENT_ITEMS.jsonl ORPHAN_ITEMS.jsonl CUTOFF ENCRYPTED_LIST.tsv PREFIX JOBS.jsonl
#           CURRENT_ITEMS  tools_walk_tree.py 输出
#           ORPHAN_ITEMS   tools_parse_leaves.py 输出（可为空文件）
#           CUTOFF         攻击发生时的 btrfs generation；只接受 egen < CUTOFF 的旧 extent
#           ENCRYPTED_LIST 清单 TSV：size<TAB>路径(相对 PREFIX).L0CK3D
#           PREFIX         目标目录相对子卷根，如 USER/Photos/
#           JOBS           输出 jobs.jsonl
# 输出    : jobs.jsonl：{"rel","ino","extents":[[foff,dbytenr,eoff,nbytes],...]}
#           stdout 统计计数器。
# 只读保证: 只读输入文件，只写 jobs.jsonl。
# 依赖    : Python 3.8+ 标准库。
# 脱敏说明: 示例前缀为泛化目录名；不含真实人名/公司名/IP。
# 来源    : 案例 check21/extent_tools/tools_make_jobs2.py（加中文头，逻辑等价）。
# =============================================================================
"""Jobs for every live encrypted file under PREFIX, using the current tree plus orphaned old leaves.

usage: python3 tools_make_jobs2.py CURRENT_ITEMS.jsonl ORPHAN_ITEMS.jsonl CUTOFF ENCRYPTED_LIST.tsv PREFIX JOBS.jsonl
  PREFIX = target directory relative to the subvolume root, e.g. USER/Photos/
  ENCRYPTED_LIST.tsv lines: size<TAB>path-under-PREFIX.L0CK3D
  ORPHAN_ITEMS.jsonl may be an empty file if no orphaned leaves were parsed.
Extent list order: exact pre-attack refs first, then whole-extent refs synthesized from
still-referenced old extents (file offset foff-eoff, extent byte 0, full disk_num_bytes).
"""
import collections, json, sys

cur_path, old_path, cutoff, listing, prefix, out_path = (sys.argv[1], sys.argv[2], int(sys.argv[3]),
                                                         sys.argv[4], sys.argv[5], sys.argv[6])

names = {}
cur_ext = collections.defaultdict(list)
for line in open(cur_path, encoding="utf-8"):
    r = json.loads(line)
    if r["t"] == "ref":
        names[r["ino"]] = (r["parent"], r["names"][0])
    elif r["t"] == "ext" and r["etype"] in (1, 2) and r.get("dbytenr") and not r["comp"]:
        cur_ext[r["ino"]].append(r)

old_ext = collections.defaultdict(dict)
for line in open(old_path, encoding="utf-8"):
    r = json.loads(line)
    if r["t"] == "ext" and r["etype"] in (1, 2) and r.get("dbytenr") and not r["comp"] and r["egen"] < cutoff:
        cur = old_ext[r["ino"]].get(r["foff"])
        if cur is None or r["egen"] > cur["egen"]:
            old_ext[r["ino"]][r["foff"]] = r


def path_of(ino, depth=0):
    if ino == 256:
        return ""
    if depth > 40 or ino not in names:
        return None
    parent, name = names[ino]
    pp = path_of(parent, depth + 1)
    return None if pp is None else (pp + "/" + name if pp else name)


live = {}
for line in open(listing, encoding="utf-8"):
    _sz, rel = line.rstrip("\n").split("\t", 1)
    live[prefix + rel] = rel[:-7]

stats = collections.Counter()
with open(out_path, "w", encoding="utf-8") as out:
    for ino in names:
        p = path_of(ino)
        if p not in live:
            continue
        stats["live_matched"] += 1
        exact = [[e["foff"], e["dbytenr"], e["eoff"], e["nbytes"]] for _f, e in sorted(old_ext.get(ino, {}).items())]
        synth, seen = [], set()
        for e in cur_ext.get(ino, []) + list(old_ext.get(ino, {}).values()):
            if e["egen"] >= cutoff:
                continue
            key = (e["foff"] - e["eoff"], e["dbytenr"])
            if key in seen or key[0] < 0:
                continue
            seen.add(key)
            synth.append([key[0], e["dbytenr"], 0, e["dnum"]])
        if not exact and not synth:
            stats["no_old_extent"] += 1
            continue
        stats["with_exact"] += bool(exact)
        stats["with_synth"] += bool(synth)
        out.write(json.dumps({"rel": live[p], "ino": ino, "extents": exact + synth}, ensure_ascii=False) + "\n")
stats["live_total"] = len(live)
print(dict(stats))
