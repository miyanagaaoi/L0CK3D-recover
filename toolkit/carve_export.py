#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : carve_export.py
# 用途    : 把 carve_all.py 的候选按「(格式, 长度) → 文件清单」一对一配对后导出。
#           配对完全唯一（候选数=1 且清单数=1）的进 certain/，其余进 maybe/，
#           用 tar 流直接推送到备份主机，不落本地临时文件。
# 运行位置: 源归档 NAS（root）；ssh 远程为备份主机。
# 参数    : --list    加密文件清单 TSV：root<TAB>相对路径<TAB>原始大小<TAB>格式
#           --cands   carve_all.py 输出的候选 JSON
#           --target  备份主机上的输出根目录（在其下建 certain/ 与 maybe/）
#           --dev     源块设备（默认 /dev/mapper/cachedev_0）
# 环境变量: NAS_USER / NAS_HOST（必填）、NAS_PASSWORD（可选）、
#           NAS_SSH_KEY（默认 /root/.ssh/id_ed25519）、NAS_PORT（默认 22）
# 输出    : 备份主机 certain/ 与 maybe/ 下的文件；stdout 统计。
# 只读保证: 源设备只读打开 + os.pread；只写备份主机目标目录。
# 依赖    : Python 3.8+ 标准库、ssh（可选 sshpass）。
# 脱敏说明: 原脚本硬编码账号/IP/共享目录路径，现改为环境变量与参数。
# 来源    : 案例 check21/carve_export.py（脱敏改写，逻辑保持等价）。
# =============================================================================
"""Export carved candidates to the backup host via tar stream (certain/maybe)."""
import argparse
import io
import json
import os
import subprocess
import sys
import tarfile

SSH_KEY = os.environ.get("NAS_SSH_KEY", "/root/.ssh/id_ed25519")
SSH_PORT = os.environ.get("NAS_PORT", "22")
SSH_PASS = os.environ.get("NAS_PASSWORD")
USER = os.environ.get("NAS_USER")
HOST = os.environ.get("NAS_HOST")


def ssh_base():
    if not USER or not HOST:
        sys.exit("ERROR: 请先设置环境变量 NAS_USER 与 NAS_HOST")
    cmd = []
    if SSH_PASS:
        cmd += ["sshpass", "-e"]
        os.environ["SSHPASS"] = SSH_PASS
    cmd += ["ssh", "-i", SSH_KEY, "-p", SSH_PORT,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "%s@%s" % (USER, HOST)]
    return cmd


def main():
    ap = argparse.ArgumentParser(description="雕刻候选导出到备份主机（只读源盘）")
    ap.add_argument("--list", required=True)
    ap.add_argument("--cands", required=True)
    ap.add_argument("--target", required=True, help="备份主机输出根目录")
    ap.add_argument("--dev", default=os.environ.get("SRC_DEV", "/dev/mapper/cachedev_0"))
    args = ap.parse_args()

    files = {}
    for line in open(args.list, encoding="utf-8"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) != 4:
            continue
        root, rel, n, fmt = parts
        files.setdefault((fmt, int(n)), []).append((root, rel))
    cands = json.load(open(args.cands))
    by = {}
    for item in cands:
        fmt, phys, ln = item[0], item[1], item[2]
        by.setdefault((fmt, ln), []).append(phys)

    pairs = []
    for key, rels in files.items():
        cps = sorted(by.get(key, []))
        if not cps:
            continue
        rels = sorted(rels)
        m = min(len(cps), len(rels))
        for k in range(m):
            certain = (len(cps) == 1 and len(rels) == 1)
            pairs.append((certain, cps[k], key[1], rels[k][1]))

    print("pairs", len(pairs), "certain", sum(1 for p in pairs if p[0]))

    tar_cmd = 'mkdir -p %s/certain %s/maybe && tar -C %s -xf -' % (args.target, args.target, args.target)
    p = subprocess.Popen(ssh_base() + [tar_cmd], stdin=subprocess.PIPE)
    tf = tarfile.open(fileobj=p.stdin, mode="w|")
    dev = os.open(args.dev, os.O_RDONLY)
    written = 0
    for certain, phys, ln, rel in pairs:
        out_rel = rel[:-7] if rel.endswith(".L0CK3D") else rel
        arc = ("certain/" if certain else "maybe/") + out_rel
        data = os.pread(dev, ln, phys)
        if len(data) != ln:
            continue
        ti = tarfile.TarInfo(arc)
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
        written += 1
        if written % 500 == 0:
            print("written", written, flush=True)
    tf.close()
    p.stdin.close()
    rc = p.wait()
    print("done written", written, "rc", rc, flush=True)


if __name__ == "__main__":
    main()
