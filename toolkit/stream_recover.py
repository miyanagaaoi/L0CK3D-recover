#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : stream_recover.py
# 用途    : 大文件「流式」delta 恢复。对单个大文件按固定块（默认 32 MiB）从源盘
#           顺序读取逻辑范围，边读边通过 ssh 写入备份主机，内存占用恒定为一块，
#           适合 GB 级视频/镜像类文件（recover_batch3.py 会把整文件读进内存）。
# 运行位置: 源归档 NAS（root），ssh 远程为备份主机。
# 参数    : --scan    扫描 JSON（scan_recoverable.py / calc_delta.py 的输出）
#           --target  备份主机上的目标根目录（脚本自动创建子目录）
#           --chunk   单次读取块大小（默认 32 MiB）
# 环境变量: NAS_USER / NAS_HOST（必填）、NAS_PASSWORD（可选）、
#           NAS_SSH_KEY（默认 /root/.ssh/id_ed25519）、NAS_PORT（默认 22）、
#           SRC_DEV（默认 /dev/mapper/cachedev_0）
# 输出    : stdout 每文件一行 JSON（path/status/bytes/rc），末行 DONE。
# 只读保证: 源设备只以 O_RDONLY 打开并用 os.pread 读取；btrfs-debug-tree 只读 dump；
#           所有写操作只发生在备份主机（远端 cat > 文件）。
# 依赖    : Python 3.8+ 标准库、btrfs-progs、ssh（可选 sshpass）。
# 脱敏说明: 账号/IP/私钥路径改为环境变量；不含公司名、真实共享目录名。
# 来源    : 案例 check21/stream_recover.py（脱敏改写，逻辑保持等价）。
# =============================================================================
"""Stream-recover large files (delta method) from chunk-mapped device to the backup host."""
import argparse
import bisect
import json
import os
import re
import subprocess
import sys

DEV = os.environ.get("SRC_DEV", "/dev/mapper/cachedev_0")
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


def load_chunks():
    out = subprocess.run(["/usr/sbin/btrfs-debug-tree", "-t", "3", DEV],
                         capture_output=True, text=True).stdout
    chunks = []
    cur = None
    for line in out.splitlines():
        m = re.search(r"key \(FIRST_CHUNK_TREE CHUNK_ITEM (\d+)\)", line)
        if m:
            cur = {"logical": int(m.group(1)), "length": None, "type": None, "phys": None}
            continue
        if cur is None:
            continue
        if cur["length"] is None:
            m = re.search(r"chunk length (\d+)", line)
            if m:
                cur["length"] = int(m.group(1)); continue
        if cur["type"] is None:
            m = re.search(r"type (\S+) num_stripes", line)
            if m:
                cur["type"] = m.group(1); continue
        if cur["phys"] is None:
            m = re.search(r"stripe 0 devid \d+ offset (\d+)", line)
            if m:
                cur["phys"] = int(m.group(1)); chunks.append(cur); cur = None
    chunks.sort(key=lambda c: c["logical"])
    return [c for c in chunks if c["length"] and c["phys"] is not None and c["type"] and "DATA" in c["type"]]


class Reader:
    def __init__(self, chunks):
        self.chunks = chunks
        self.starts = [c["logical"] for c in chunks]
        self.fd = os.open(DEV, os.O_RDONLY)

    def read(self, start, n):
        end = start + n
        pos = start
        parts = []
        while pos < end:
            i = bisect.bisect_right(self.starts, pos) - 1
            if i < 0:
                return None
            c = self.chunks[i]
            if pos >= c["logical"] + c["length"]:
                return None
            off = pos - c["logical"]
            take = min(end - pos, c["length"] - off)
            data = os.pread(self.fd, take, c["phys"] + off)
            if len(data) != take:
                return None
            parts.append(data)
            pos += take
        return b"".join(parts)


def main():
    ap = argparse.ArgumentParser(description="大文件流式 delta 恢复（只读源盘，内存恒定）")
    ap.add_argument("--scan", required=True, help="扫描 JSON")
    ap.add_argument("--target", required=True, help="备份主机目标根目录")
    ap.add_argument("--chunk", type=int, default=32 * 1024 * 1024)
    args = ap.parse_args()
    scan = json.load(open(args.scan, encoding="utf-8"))
    rd = Reader(load_chunks())
    for it in scan["recoverable_files"]:
        rel = it["path"]
        n = int(it["orig"])
        d = int(it["delta"])
        out_rel = rel[:-7] if rel.endswith(".L0CK3D") else rel
        target = args.target.rstrip("/") + "/" + out_rel
        parts = target.split("/")
        target_dir = "/".join(parts[:-1])
        p = subprocess.Popen(
            ssh_base() + ["mkdir -p '%s' && cat > '%s'" % (target_dir, target)],
            stdin=subprocess.PIPE)
        pos = 0
        ok = True
        while pos < n:
            take = min(args.chunk, n - pos)
            data = rd.read(d + pos, take)
            if data is None:
                print(json.dumps({"path": rel, "status": "read_fail", "at": pos}), flush=True)
                ok = False
                break
            p.stdin.write(data)
            pos += take
        p.stdin.close()
        rc = p.wait()
        print(json.dumps({"path": rel, "status": "ok" if (ok and rc == 0) else "fail",
                          "bytes": n, "rc": rc}, ensure_ascii=False), flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
