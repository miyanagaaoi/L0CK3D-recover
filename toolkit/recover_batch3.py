#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : recover_batch3.py
# 用途    : delta 法批量恢复。读取 scan_recoverable.py 产出的扫描 JSON，对每个
#           可恢复文件用其 delta 直接顺序读取（data = 物理偏移 delta 起的原长字节），
#           在内存中做格式头校验 + sha256，再按批次用 tar 流推送到备份主机，
#           不在本机（NAS）落任何临时镜像，避免占满救援空间。
# 运行位置: 源归档 NAS（root）；ssh 推送到「备份主机」（另一台机器/另一块卷）。
# 参数    : --scan         扫描 JSON（scan_recoverable.py 的输出）
#           --target       备份主机上的目标目录
#           --batch-bytes  单批累计字节上限（默认 400 MiB）
#           --batch-count  单批文件数上限（默认 200）
# 输出    : stdout 每文件一行 JSON（path/out/status/orig/sha256/format），
#           event=progress / done 汇总行；文件写入备份主机。
# 环境变量: NAS_USER     备份主机登录账号（必填）
#           NAS_HOST     备份主机地址（必填，不在脚本中硬编码 IP）
#           NAS_PASSWORD 可选；设置后用 sshpass -e 走密码登录，未设置则仅用密钥
#           NAS_SSH_KEY  SSH 私钥路径（默认 /root/.ssh/id_ed25519）
#           NAS_PORT     SSH 端口（默认 22）
#           SRC_DEV      源块设备（默认 /dev/mapper/cachedev_0，DSM 通用路径示例）
# 只读保证: 源设备只用 os.open(..., O_RDONLY) + os.pread；btrfs-debug-tree 只 print；
#           不读不写源卷的挂载点（除扫描阶段的元数据），不写源设备，不做 balance/scrub。
# 依赖    : Python 3.8+ 标准库；btrfs-progs（/usr/sbin/btrfs-debug-tree）；ssh；
#           可选 sshpass（密码登录时）。
# 脱敏说明: 原脚本硬编码 root 私钥路径、目标账号与目标主机 IP，现已全部改为环境变量。
# 来源    : 案例 check21/recover_batch3.py（脱敏改写，逻辑保持等价）。
# =============================================================================
"""Recover .L0CK3D files via btrfs chunk mapping, stream tar to the backup host."""
import argparse
import bisect
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time

DEV = os.environ.get("SRC_DEV", "/dev/mapper/cachedev_0")
SSH_KEY = os.environ.get("NAS_SSH_KEY", "/root/.ssh/id_ed25519")
SSH_PORT = os.environ.get("NAS_PORT", "22")
SSH_PASS = os.environ.get("NAS_PASSWORD")  # 可选：无密钥时用 sshpass 走密码
USER = os.environ.get("NAS_USER")
HOST = os.environ.get("NAS_HOST")


def ssh_base():
    """构造 ssh 客户端命令前缀；账号/主机缺失时直接报错，避免误连占位符。"""
    if not USER or not HOST:
        sys.exit("ERROR: 请先设置环境变量 NAS_USER 与 NAS_HOST"
                 "（脱敏要求：账号与 IP 不写进脚本）")
    cmd = []
    if SSH_PASS:
        cmd += ["sshpass", "-e"]  # 密码从环境变量 SSHPASS 读取，不出现在命令行
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
                cur["length"] = int(m.group(1))
                continue
        if cur["type"] is None:
            m = re.search(r"type (\S+) num_stripes", line)
            if m:
                cur["type"] = m.group(1)
                continue
        if cur["phys"] is None:
            m = re.search(r"stripe 0 devid \d+ offset (\d+)", line)
            if m:
                cur["phys"] = int(m.group(1))
                chunks.append(cur)
                cur = None
    chunks.sort(key=lambda c: c["logical"])
    return [c for c in chunks if c["length"] and c["phys"] is not None and c["type"] and "DATA" in c["type"]]


class Reader:
    """把「btrfs 逻辑地址」翻译成「物理偏移」的只读随机读取器。"""

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
                return None, "no chunk at %d" % pos
            c = self.chunks[i]
            if pos >= c["logical"] + c["length"]:
                return None, "gap at %d" % pos
            off = pos - c["logical"]
            take = min(end - pos, c["length"] - off)
            data = os.pread(self.fd, take, c["phys"] + off)
            if len(data) != take:
                return None, "short read at %d" % pos
            parts.append(data)
            pos += take
        return b"".join(parts), None


def fmt_ok(path, head):
    """按扩展名做文件头魔数校验；返回 True/False/None(未知类型不判定)。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return head.startswith(b"%PDF")
    if ext in (".zip", ".docx", ".xlsx", ".pptx", ".jar", ".odt", ".apk"):
        return head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return head.startswith(b"\x89PNG")
    if ext == ".gif":
        return head.startswith(b"GIF8")
    if ext == ".rar":
        return head.startswith(b"Rar!")
    if ext == ".7z":
        return head.startswith(b"7z\xbc\xaf")
    if ext in (".doc", ".xls", ".ppt", ".msg"):
        return head.startswith(b"\xd0\xcf\x11\xe0")
    return None


def transfer_stream(entries, target):
    """用 tar 流把一批 (相对路径, 字节) 推到备份主机；不落本地临时文件。"""
    cmd = ssh_base() + ["mkdir -p '%s' && tar -C '%s' -xf -" % (target, target)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        tf = tarfile.open(fileobj=p.stdin, mode="w|", format=tarfile.GNU_FORMAT)
        for out_rel, data in entries:
            ti = tarfile.TarInfo(out_rel)
            ti.size = len(data)
            ti.mtime = int(time.time())
            tf.addfile(ti, io.BytesIO(data))
        tf.close()
        p.stdin.close()
        return p.wait()
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
        return -1


def main():
    ap = argparse.ArgumentParser(description="delta 法批量恢复并流式推送到备份主机（只读源盘）")
    ap.add_argument("--scan", required=True, help="scan_recoverable.py 输出的扫描 JSON")
    ap.add_argument("--target", required=True, help="备份主机上的目标目录")
    ap.add_argument("--batch-bytes", type=int, default=400 * 1024 * 1024)
    ap.add_argument("--batch-count", type=int, default=200)
    args = ap.parse_args()
    if not args.scan or not os.path.exists(args.scan):
        sys.exit("ERROR: --scan 不存在: %r" % args.scan)
    scan = json.load(open(args.scan, encoding="utf-8"))
    items = scan["recoverable_files"]
    chunks = load_chunks()
    rd = Reader(chunks)
    stats = {"attempted": 0, "ok": 0, "fail": 0, "bytes_ok": 0, "xfer": 0, "fmt_mismatch": 0}
    batch = []
    batch_bytes = 0
    t0 = time.time()
    used = set()

    def flush():
        nonlocal batch, batch_bytes
        if not batch:
            return
        rc = transfer_stream(batch, args.target)
        if rc == 0:
            stats["xfer"] += len(batch)
        else:
            print(json.dumps({"event": "transfer_fail", "count": len(batch), "rc": rc}),
                  flush=True)
        batch = []
        batch_bytes = 0

    for idx, it in enumerate(items):
        rel = it["path"]
        n = it["orig"]
        d = it["delta"]
        out_rel = rel[:-7] if rel.endswith(".L0CK3D") else rel
        if out_rel in used:
            base, ext = os.path.splitext(out_rel)
            k = 1
            while "%s_%d%s" % (base, k, ext) in used:
                k += 1
            out_rel = "%s_%d%s" % (base, k, ext)
        stats["attempted"] += 1
        try:
            data, err = rd.read(d, n)
            if data is None:
                stats["fail"] += 1
                print(json.dumps({"path": rel, "status": "read_fail", "msg": err}, ensure_ascii=False), flush=True)
                continue
            ok = fmt_ok(out_rel, data[:64])
            if ok is False:
                stats["fmt_mismatch"] += 1
            h = hashlib.sha256(data).hexdigest()
            used.add(out_rel)
            batch.append((out_rel, data))
            batch_bytes += n
            stats["ok"] += 1
            stats["bytes_ok"] += n
            print(json.dumps({"path": rel, "out": out_rel, "status": "ok", "orig": n,
                              "sha256": h,
                              "format": "ok" if ok is True else ("unknown" if ok is None else "mismatch")},
                             ensure_ascii=False), flush=True)
            if len(batch) >= args.batch_count or batch_bytes >= args.batch_bytes:
                flush()
                print(json.dumps({"event": "progress", "i": idx + 1, "total": len(items),
                                  "ok": stats["ok"], "fail": stats["fail"],
                                  "xfer": stats["xfer"], "elapsed": int(time.time() - t0)}), flush=True)
        except Exception as e:
            stats["fail"] += 1
            print(json.dumps({"path": rel, "status": "exception:" + repr(e)[:100]}, ensure_ascii=False), flush=True)
    flush()
    print(json.dumps({"event": "done", **stats}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
