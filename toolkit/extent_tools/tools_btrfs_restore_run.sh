#!/bin/sh
# =============================================================================
# 脚本名  : tools_btrfs_restore_run.sh
# 用途    : 用 btrfs restore 从**攻击前的旧 fs-tree root**把一个子卷里的指定目录
#           恢复到**另一个卷**上。适用于 btrfs CoW 保留了大量旧元数据、但旧数据块
#           尚未被覆盖的情况，是「整目录级」恢复手段（与 extent 精确恢复互补）。
# 运行位置: 源归档 NAS（root）。
# 参数    : LV_DEVICE OUT_DIR PATH_REGEX ROOT_BYTENR [ROOT_BYTENR ...]
#           LV_DEVICE  底层 LV（/dev/mapper/vgN-volume_N），**不是** 挂载点的
#                      /dev/mapper/cachedev_N：btrfs restore 拒绝已挂载设备，
#                      且它只读 LV_DEVICE
#           OUT_DIR    输出目录，必须不存在、且位于另一个卷
#           PATH_REGEX 恢复哪些路径的正则，如 '^/(|USER(|/Photos(|/.*)))$'
#           ROOT_BYTENR 旧 fs-tree root 的 bytenr，来自
#                      `btrfs-find-root -a -o SUBVOL_ID LV_DEVICE`
#                      （建议先对每个 root 用 -D 做 dry-run）
# 用法示例: sh tools_btrfs_restore_run.sh /dev/mapper/vg1-volume_1 /volume2/rescue_restore \
#               '^/(|USER(|/Photos(|/.*)))$' ROOT_BYTENR_1 ROOT_BYTENR_2
# 输出    : OUT_DIR 下恢复出的文件；日志写入 $OUT_DIR.log。
# 只读保证: btrfs restore 只读源设备；输出目录用 mkdir -m 700 新建，失败即退出，
#           不覆盖已有目录；本脚本不含任何写源设备的命令。
# 安全约束: 禁止对源卷做 balance / scrub / defrag / remount rw；只允许只读挂载。
# 脱敏说明: 示例路径为泛化目录名；无 IP/账号/主机名/公司名。
# 来源    : 案例 check21/extent_tools/tools_btrfs_restore_run.sh（加中文头）。
# =============================================================================
# Restore one directory from OLD fs-tree roots of a btrfs subvolume into ANOTHER volume.
#
# usage (root on NAS):
#   sh tools_btrfs_restore_run.sh LV_DEVICE OUT_DIR PATH_REGEX ROOT_BYTENR [ROOT_BYTENR ...]
# example:
#   sh tools_btrfs_restore_run.sh /dev/mapper/vg1-volume_1 /volume2/rescue_restore \
#       '^/(|USER(|/Photos(|/.*)))$' ROOT_BYTENR_1 ROOT_BYTENR_2 ROOT_BYTENR_3
#
# - LV_DEVICE is the underlying LV (/dev/mapper/vgN-volume_N), NOT the mounted
#   /dev/mapper/cachedev_N: btrfs restore refuses a mounted device, and it only
#   reads from LV_DEVICE.
# - ROOT_BYTENR are fs-tree root blocks of the subvolume found with
#   `btrfs-find-root -a -o SUBVOL_ID LV_DEVICE` (dry-run each with -D first).
# - OUT_DIR must be on a different volume and must not exist yet.
# - -i ignores errors, -m restores owner/mode/times. Files already present in OUT_DIR
#   are not overwritten by later roots (no -o), so list the best root first.
set -u
[ $# -ge 4 ] || { echo "usage: tools_btrfs_restore_run.sh LV_DEVICE OUT_DIR PATH_REGEX ROOT_BYTENR [ROOT_BYTENR ...]" >&2; exit 2; }
DEV=$1; OUT=$2; REGEX=$3; shift 3
LOG="$OUT.log"
mkdir -m 700 "$OUT" || exit 1
df -h "$OUT" > "$LOG"
for b in "$@"; do
  echo "=== root $b start $(date)" >> "$LOG"
  nice -n 10 btrfs restore -v -i -m -f "$b" --path-regex "$REGEX" \
    "$DEV" "$OUT" 2>&1 | grep -v 'parent transid verify failed' >> "$LOG"
  echo "=== root $b done $(date)" >> "$LOG"
done
echo ALL_DONE >> "$LOG"
