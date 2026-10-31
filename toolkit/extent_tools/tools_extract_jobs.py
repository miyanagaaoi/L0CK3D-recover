#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名  : tools_extract_jobs.py
# 用途    : 精确 extent 恢复。按 jobs.jsonl 给出的攻击前 extent，把仍被加密的字节
#           用盘上旧数据填回，明文区保留现存 .L0CK3D 的内容，拼接出原始文件。
#           规则：① 只在页脚声明的加密段内填充；② 读回内容与现存密文相同则拒绝
#           （说明旧 extent 已被覆盖，填了也是密文）；③ 完整覆盖才写 ok/，
#           部分覆盖且格式头合理写 partial/（其余蜜文区清零，避免播放器看到噪声）。
# 运行位置: 源归档 NAS（root），读块设备 + 现存加密文件。
# 参数    : DEVICE CHUNKMAP TARGET_ROOT JOBS OUT_DIR PARTIAL_DIR（位置参数）
#           DEVICE       底层 LV，如 /dev/mapper/vg1-volume_1（O_RDONLY 打开）
#           CHUNKMAP     tools_chunkmap.py 输出
#           TARGET_ROOT  现存 .L0CK3D 所在目录（只读）
#           JOBS         tools_make_jobs2.py 输出的 jobs.jsonl
#           OUT_DIR / PARTIAL_DIR  必须位于**另一个卷**（如 /volume2/rescue_extent/{ok,partial}）
# 环境变量: MIN_PARTIAL_COVER  部分恢复写出门槛（默认 0.5）
#           MAX_EXTRACT_SIZE   单文件上限，超过报 ERR BIG 需换流式版本（默认 1.5 GiB）
# 用法示例: python3 - DEV CHUNKMAP TARGET JOBS OUT PARTIAL < tools_extract_jobs.py > report.jsonl
# 输出    : stdout 每文件一行 JSON：status(OK/OK_UNVERIFIED/PARTIAL/FAIL/EXISTS/ERR)、
#           cover、magic、same_as_cipher。
# 只读保证: 块设备 O_RDONLY；现存 .L0CK3D 只读；新文件用 O_CREAT|O_EXCL 新建，
#           绝不覆盖已有文件；输出目录由操作者保证在另一个卷上。
# 依赖    : Python 3.8+ 标准库。
# 脱敏说明: 示例路径为泛化目录名（USER/Photos、rescue_extent）；无 IP/账号/公司名。
# 来源    : 案例 check21/extent_tools/tools_extract_jobs.py（加中文头与前缀注释）。
# =============================================================================
"""Rebuild original files from pre-attack extents + plaintext ranges of the live .L0CK3D file.

usage (root on NAS): python3 - DEVICE CHUNKMAP TARGET_ROOT JOBS OUT_DIR PARTIAL_DIR < tools_extract_jobs.py > report.jsonl
  DEVICE      underlying LV, e.g. /dev/mapper/vg1-volume_1 (opened O_RDONLY)
  TARGET_ROOT live directory holding the *.L0CK3D files, e.g. /volume1/homes/USER/Photos (read only)
  OUT_DIR / PARTIAL_DIR must be on ANOTHER volume, e.g. /volume2/rescue_extent/{ok,partial}
Reads the raw device and live files only; writes new files under OUT_DIR / PARTIAL_DIR with O_EXCL,
never overwriting. Files larger than MAX_EXTRACT_SIZE are reported as ERR "BIG" (needs a streaming pass).
"""
import bisect, json, os, struct, sys

dev, chunkmap, target_root, jobs, out_dir, partial_dir = sys.argv[1:7]
FOOTER = 588
FIRST_LIMIT = 1 << 20
PARTIAL_FACTORS = {10: 4, 15: 5, 20: 7, 25: 9, 30: 10, 35: 12, 40: 14, 50: 10, 60: 20, 70: 23, 80: 27}
MIN_PARTIAL_COVER = float(os.environ.get("MIN_PARTIAL_COVER", "0.5"))
MAX_SIZE = int(os.environ.get("MAX_EXTRACT_SIZE", str(1536 << 20)))  # 更大文件需要流式版本

chunks = sorted((int(a), int(b), int(c)) for a, b, c, _t in (l.split() for l in open(chunkmap)))
starts = [c[0] for c in chunks]


def read_logical(fd, logical, length):
    out = bytearray()
    while length > 0:
        i = bisect.bisect_right(starts, logical) - 1
        if i < 0:
            raise ValueError("unmapped logical %d" % logical)
        lg, ln, ph = chunks[i]
        if logical >= lg + ln:
            raise ValueError("unmapped logical %d" % logical)
        n = min(length, lg + ln - logical)
        out += os.pread(fd, n, ph + logical - lg)
        logical += n
        length -= n
    return bytes(out)


def encrypted_ranges(size, mode, pct):
    if mode == 0x24:
        return [(0, size)]
    if mode == 0x26:
        return [(0, min(size, FIRST_LIMIT))]
    if mode == 0x25 and pct in PARTIAL_FACTORS:
        length = (size // 100) * PARTIAL_FACTORS[pct]
        count = 5 if pct == 50 else 3
        gap = length if pct == 50 else (size - 3 * length) // 2
        return [(i * (length + gap), length) for i in range(count)]
    return None


def magic_ok(head):
    return (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n" or head[4:8] in (b"ftyp", b"wide", b"mdat", b"moov", b"free")
            or head[:2] == b"BM" or head[:4] in (b"RIFF", b"\x30\x26\xb2\x75", b"II*\x00", b"MM\x00*", b"\x1aE\xdf\xa3", b"8BPS", b"%PDF")
            or head[:6] in (b"GIF87a", b"GIF89a") or head[:1] == b"\x47" or head[:4] == b"\x00\x00\x01\xba")


def write_new(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


dfd = os.open(dev, os.O_RDONLY)
for line in open(jobs, encoding="utf-8"):
    job = json.loads(line)
    rel = job["rel"]
    rep = {"rel": rel}
    try:
        live = os.path.join(target_root, rel + ".L0CK3D")
        with open(live, "rb") as f:
            st = os.fstat(f.fileno())
            f.seek(-FOOTER, 2)
            foot = f.read(FOOTER)
            size = struct.unpack_from("<Q", foot, 580)[0]
            if size > MAX_SIZE:
                raise ValueError("BIG %d" % size)
            mode, pct, crypto = foot[572], foot[573], foot[574]
            if size != st.st_size - FOOTER:
                raise ValueError("footer size mismatch")
            f.seek(0)
            data = bytearray(f.read(size))
        rep.update(size=size, mode=mode, pct=pct, crypto=crypto)
        ranges = encrypted_ranges(size, mode, pct)
        if ranges is None:
            raise ValueError("unknown mode")
        need = bytearray(size)  # 1 = 该字节仍需从旧 extent 补
        for off, ln in ranges:
            need[off:off + ln] = b"\x01" * ln
        enc_total = sum(ln for _o, ln in ranges)
        same_as_cipher = 0
        filled = 0
        for foff, dbytenr, eoff, nbytes in job["extents"]:
            lo, hi = max(foff, 0), min(foff + nbytes, size)
            for off, ln in ranges:
                a, b = max(lo, off), min(hi, off + ln)
                if a >= b:
                    continue
                # 只补还没被更精确的 extent 填过的字节
                pos = need.find(1, a, b)
                while pos != -1:
                    end = need.find(0, pos, b)
                    end = b if end == -1 else end
                    old = read_logical(dfd, dbytenr + eoff + (pos - foff), end - pos)
                    if old == bytes(data[pos:end]):
                        same_as_cipher += end - pos
                    else:
                        data[pos:end] = old
                        need[pos:end] = bytes(end - pos)
                        filled += end - pos
                    pos = need.find(1, end, b)
        missing = need.count(1)
        cover = 1.0 - missing / enc_total if enc_total else 1.0
        rep.update(cover=round(cover, 4), same_as_cipher=same_as_cipher, magic=magic_ok(bytes(data[:16])))
        if missing == 0 and (rep["magic"] or mode in (0x25, 0x26)):
            # 部分加密模式复用了仍被引用的 extent，magic 缺失只是格式缺口
            write_new(os.path.join(out_dir, rel), bytes(data))
            rep["status"] = "OK" if rep["magic"] else "OK_UNVERIFIED"
        elif cover >= MIN_PARTIAL_COVER and rep["magic"]:
            pos = need.find(1)  # 把仍是密文的区段清零，让查看器看到缺口而不是噪声
            while pos != -1:
                end = need.find(0, pos)
                end = size if end == -1 else end
                data[pos:end] = bytes(end - pos)
                pos = need.find(1, end)
            write_new(os.path.join(partial_dir, rel), bytes(data))
            rep["status"] = "PARTIAL"
        else:
            rep["status"] = "FAIL"
    except FileExistsError:
        rep["status"] = "EXISTS"
    except Exception as e:  # 单文件失败不影响整体
        rep["status"] = "ERR"
        rep["err"] = str(e)[:200]
    print(json.dumps(rep, ensure_ascii=False))
    sys.stdout.flush()
