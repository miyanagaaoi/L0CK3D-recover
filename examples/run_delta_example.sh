#!/bin/sh
# 示例：delta 法恢复（只读源盘）
# 使用前：
#   export NAS_HOST=192.168.1.10
#   export NAS_USER=<NAS_USER>
#   export NAS_PASSWORD=<NAS_PASSWORD>
#   export SRC_DEV=/dev/mapper/cachedev_0
#   export SHARE_ROOT=/volume1/<SHARE>
#   export TARGET_ROOT=/volume1/<TARGET_SHARE>/recovered
set -eu

: "${NAS_HOST:?set NAS_HOST}"
: "${NAS_USER:?set NAS_USER}"
: "${NAS_PASSWORD:?set NAS_PASSWORD}"
: "${SRC_DEV:=/dev/mapper/cachedev_0}"
: "${SHARE_ROOT:=/volume1/<SHARE>}"
: "${TARGET_ROOT:=/volume1/<TARGET_SHARE>/recovered}"

echo "== 1) 普查（生成候选清单） =="
python3 toolkit/scan_recoverable.py --root "$SHARE_ROOT" --out /dev/shm/scan.json

echo "== 2) 抽样验证（10 个文件） =="
python3 - <<PY
import json
d = json.load(open('/dev/shm/scan.json'))
items = d.get('recoverable_files', [])[:10]
out = {'root': d.get('root', ''), 'stats': {}, 'recoverable_files': items}
json.dump(out, open('/dev/shm/scan_sample.json', 'w'), ensure_ascii=False)
print('sample', len(items))
PY
python3 toolkit/recover_batch3.py --scan /dev/shm/scan_sample.json --target "$TARGET_ROOT/_sample"

echo "== 3) 人工核对样本文件头/大小后，再全量 =="
# python3 toolkit/recover_batch3.py --scan /dev/shm/scan.json --target "$TARGET_ROOT"
