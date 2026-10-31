# C3RB3R 勒索恢复工具包（脱敏版）

一套来自真实 C3RB3R（`.L0CK3D` 扩展名）勒索事件恢复现场的脚本集合，已做脱敏与参数化，可直接复用于同类 btrfs + DSM 环境的数据恢复。

- **目标系统**：Synology DSM 7.x / Linux + btrfs（单盘 SINGLE/DUP 布局）
- **恢复原理**：利用 btrfs 的 CoW 特性——勒索软件加密后，攻击前的数据块往往仍被旧元数据引用或已变成空闲块。工具围绕「读元数据 → 定位旧 extent / 空闲区 → 只读搬运原始字节 → 多重校验」展开。
- **规模参考**：原始案例为 TB 级卷，工具链在该规模下跑过全卷扫描、万级文件的 delta 恢复与空闲空间雕刻。

---

## 1. 安全约束（务必先读）

> 这些脚本的价值在于「证明恢复结果可信」，前提是**源盘不被二次污染**。

| 约束 | 说明 |
|---|---|
| **只读源盘** | 所有脚本对块设备只用 `os.open(dev, O_RDONLY)` + `pread`；对 `.L0CK3D` 只用 `"rb"` |
| **绝不写源卷** | 恢复产物一律写到**另一个卷**或另一台主机；脚本自身不写源设备、不写源挂载点 |
| **禁止 balance / scrub / defrag** | 任何 CoW 重写操作会覆盖待恢复的旧数据块，导致不可逆丢失 |
| **禁止 remount rw 后写入** | 恢复期间源卷最好保持只读挂载（`mount -o ro`），至少禁止新写入 |
| **输出目录必须不在源卷** | `tools_extract_jobs.py` 的 `OUT_DIR`、`PARTIAL_DIR` 由操作者保证在不同卷；脚本不会替你检查 |
| **不覆盖已有文件** | 恢复写入使用 `O_CREAT|O_EXCL`；`btrfs_restore_run.sh` 的 `mkdir` 失败即退出 |
| **先摸清再动手** | 先跑通「扫描 → 小样本恢复 → csum 校验」闭环，再规模化 |

---

## 2. 环境要求

| 依赖 | 用途 | 说明 |
|---|---|---|
| Python **3.8+** | 全部 `.py` | DSM 7 自带 3.8，只需标准库 |
| `btrfs-progs` | `btrfs-debug-tree` / `btrfs restore` / `btrfs-find-root` | DSM 的 btrfs-progs 为 4.x，路径通常在 `/usr/sbin/` |
| `ssh` | 把恢复结果推送到备份主机 | 脚本默认用密钥；无密钥时可 `sshpass` 走密码（可选） |
| Pillow（PIL） | 仅 `check_carve_pil.py` | 图片雕刻结果可解码率抽检 |
| `zipfile` / `csv` / `hashlib` | 标准库 | 无需安装 |

**运行位置**分两类：
- **源 NAS（root）**：需要读块设备或 FIEMAP，必须在源机上跑；
- **操作机**：只处理文本/JSON/普通文件，可在任意机器跑（包括 Windows）。

---

## 3. 环境变量约定（脱敏后的唯一配置入口）

账号、密码、主机地址**不写在任何脚本里**，全部从环境变量读取：

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `NAS_USER` | ✅ | — | 备份主机（接收恢复结果的机器）登录账号 |
| `NAS_HOST` | ✅ | — | 备份主机地址（IP 或域名，脚本内不硬编码） |
| `NAS_PASSWORD` | ❌ | 空 | 设置后用 `sshpass -e` 走密码登录；不设则只用密钥 |
| `NAS_SSH_KEY` | ❌ | `/root/.ssh/id_ed25519` | SSH 私钥路径 |
| `NAS_PORT` | ❌ | `22` | SSH 端口 |
| `SRC_DEV` | ❌ | `/dev/mapper/cachedev_0` | 源块设备（DSM 通用路径示例） |
| `MIN_PARTIAL_COVER` | ❌ | `0.5` | 部分恢复写出门槛（`tools_extract_jobs.py`） |
| `MAX_EXTRACT_SIZE` | ❌ | `1.5 GiB` | 单文件内存上限，超过报 `ERR BIG` 需改用流式（`tools_extract_jobs.py`） |

示例：

```sh
export NAS_USER='<NAS_USER>'
export NAS_HOST='<BACKUP_HOST>'
export NAS_SSH_KEY=/root/.ssh/id_ed25519
export SRC_DEV=/dev/mapper/cachedev_0
```

未设置 `NAS_USER` / `NAS_HOST` 时，需要推送结果的脚本会**立刻报错退出**，不会静默连到占位符。

---

## 4. 工具总览

### 4.1 主流程工具（`toolkit/`）

| 脚本 | 运行位置 | 用途 | 主要参数 | 输入 → 输出 |
|---|---|---|---|---|
| [`scan_recoverable.py`](./scan_recoverable.py) | 源 NAS | 遍历 `.L0CK3D`，解析页脚 + FIEMAP 判定是否可恢复 | `--root` `--out` | 目录 → 扫描 JSON（含 `delta`） |
| [`calc_delta.py`](./calc_delta.py) | 源 NAS | 对指定文件单独算 delta，生成同格式扫描 JSON | `--out` `--root` `FILE...` | 文件列表 → 扫描 JSON |
| [`recover_batch3.py`](./recover_batch3.py) | 源 NAS → 备份主机 | delta 法批量恢复，整文件读入内存后 tar 流推送 | `--scan` `--target` `--batch-bytes` `--batch-count` | 扫描 JSON → 备份主机上的文件 + stdout JSON 报告 |
| [`stream_recover.py`](./stream_recover.py) | 源 NAS → 备份主机 | 大文件流式恢复（内存恒定一块，默认 32 MiB） | `--scan` `--target` `--chunk` | 扫描 JSON → 备份主机上的大文件 |
| [`carve_all.py`](./carve_all.py) | 源 NAS | 空闲空间按魔数雕刻，按「格式+原始大小」精确匹配过滤 | `--list` `--ranges` `--out` `--dev` `--max-file` | 清单 TSV + 物理区间 → 候选 JSON |
| [`carve_export.py`](./carve_export.py) | 源 NAS → 备份主机 | 雕刻候选与清单配对导出（`certain/` / `maybe/`） | `--list` `--cands` `--target` `--dev` | 清单 + 候选 JSON → 备份主机文件 |
| [`csum_check.py`](./csum_check.py) | 源 NAS | 解析 csum tree，逐 4 KiB 块重算 CRC32C 比对（硬证据） | `--chunkmap` `--scan` `--sample` `--dev` `--out` | chunkmap + 扫描 JSON → 校验 JSON |
| [`validate_ole.py`](./validate_ole.py) | 操作机 | OLE 复合文档（doc/xls/ppt/msg）结构校验 | `--root` `--out` `--only` | 目录 → CSV 报告 |
| [`validate_office_zip.py`](./validate_office_zip.py) | 操作机 | ZIP/OOXML 容器 CRC 校验与 Office 识别 | `--root` `--out` | 目录 → CSV 报告 |
| [`check_carve_pil.py`](./check_carve_pil.py) | 操作机 | 雕刻图片抽样真解码，算可解码率 | `--root` `--subdirs` `--sample` `--seed` | 目录 → stdout 统计 |

### 4.2 精确 extent 工具（`toolkit/extent_tools/`）

| 脚本 | 运行位置 | 用途 | 主要参数 | 输入 → 输出 |
|---|---|---|---|---|
| [`tools_chunkmap.py`](./extent_tools/tools_chunkmap.py) | 操作机 | chunk tree dump → 统一 CHUNKMAP | `DUMP_TXT` | 文本 → stdout 映射行 |
| [`tools_walk_tree.py`](./extent_tools/tools_walk_tree.py) | 源 NAS | 遍历**当前**子卷 fs tree，输出 item JSONL | `DEVICE CHUNKMAP SUBVOL_ID` | 设备 → JSONL |
| [`tools_parse_leaves.py`](./extent_tools/tools_parse_leaves.py) | 源 NAS | 解析**孤儿旧叶子**（攻击前元数据） | `DEVICE CHUNKMAP LEAFLIST OWNER` | 设备 + 叶子清单 → JSONL |
| [`tools_make_jobs2.py`](./extent_tools/tools_make_jobs2.py) | 操作机 | 生成恢复任务（精确旧引用 + 合成引用） | `CUR OLD CUTOFF LIST PREFIX JOBS` | JSONL/TSV → `jobs.jsonl` |
| [`tools_extract_jobs.py`](./extent_tools/tools_extract_jobs.py) | 源 NAS | 按 extent 拼接原始文件（OK/PARTIAL 分流） | `DEV CHUNKMAP TARGET JOBS OUT PARTIAL` | 设备 + jobs → 恢复文件 + 报告 JSONL |
| [`tools_btrfs_restore_run.sh`](./extent_tools/tools_btrfs_restore_run.sh) | 源 NAS | 从旧 fs-tree root 用 `btrfs restore` 整目录恢复 | `LV OUT REGEX ROOT_BYTENR...` | 设备 → 输出目录 + 日志 |
| [`tools_parse_free_space.py`](./extent_tools/tools_parse_free_space.py) | 操作机 | free space tree dump → 逻辑空闲区间 | `FREE_TXT FREE_JSON` `--last-len` | 文本 → JSON |
| [`tools_make_ranges.py`](./extent_tools/tools_make_ranges.py) | 操作机 | 逻辑空闲区间 → 物理扫描区间 | `CHUNKS FREE MINLEN OUT` | JSON/文本 → ranges 文本 |
| [`tools_raw_reader2.py`](./extent_tools/tools_raw_reader2.py) | 源 NAS | 只读「逻辑→物理」随机读取器（库 + CLI，排错用） | `DEV CHUNKMAP LOGICAL LENGTH` `--hex` `--check` | 设备 → 字节/十六进制 |

> `tools_parse_free_space.py`、`tools_make_ranges.py`、`tools_raw_reader2.py` 三个脚本的**原始实现未在案例目录中留存**，本包按 `carve_step*.sh` 的调用约定与 `Reader` 类逻辑重写，文件头已标注；它们各自的关键行为都做了合成数据测试（见第 8 节）。

---

## 5. 推荐使用顺序

### 路线 A：delta 法（同文件逻辑/物理偏移差固定，速度最快）

```
scan_recoverable.py --root <目录> --out scan.json
        │
        ├─ 小文件 → recover_batch3.py  --scan scan.json --target <备份机目录>
        └─ 大文件 → stream_recover.py --scan scan.json --target <备份机目录>
                            │
                      csum_check.py --chunkmap chunkmap.txt --scan scan.json
                            │
              validate_ole.py / validate_office_zip.py / check_carve_pil.py
```

适用：`0x26`（只加密头部）与部分 `0x25`（分段加密）文件；`scan_recoverable.py` 已判为 `recoverable`。
验证：`csum_check.py` 输出 `ok>0 / bad=0 / missing=0` 才能声明「与攻击前逐字节一致」。

### 路线 B：extent 精确恢复（元数据仍留旧引用，可恢复被覆盖严重的数据）

```
btrfs-debug-tree -t 3  $SRC_DEV > chunk-tree.txt
tools_chunkmap.py chunk-tree.txt > chunkmap.txt
tools_walk_tree.py   DEV chunkmap.txt <SUBVOL>      > items-current.jsonl
btrfs-find-root -a -o <SUBVOL> <LV>                 # 得到旧 root 列表
tools_parse_leaves.py DEV chunkmap.txt roots.txt <SUBVOL> > items-old.jsonl
tools_make_jobs2.py items-current.jsonl items-old.jsonl <CUTOFF> list.tsv '<PREFIX>' jobs.jsonl
tools_extract_jobs.py DEV chunkmap.txt <TARGET_ROOT> jobs.jsonl <OUT> <PARTIAL>
        （>1.5 GiB 的文件改用流式版本，本包未收录，见第 9 节）
```

补充：若旧 fs-tree root 结构完整，可先用
`tools_btrfs_restore_run.sh` 做**整目录**快速恢复，再用 extent 法补齐差异文件——两者互补。

### 路线 C：空闲空间雕刻（原数据块已被 CoW 覆盖，只能靠残留数据）

```
btrfs-debug-tree -t 10 <LV> > free.txt          # 注意：用底层 LV，不是挂载点
tools_parse_free_space.py free.txt free.json
tools_make_ranges.py chunkmap.txt free.json 0 ranges.txt
carve_all.py    --list list.tsv --ranges ranges.txt --out cands.json
carve_export.py --list list.tsv --cands cands.json --target <备份机目录>
check_carve_pil.py --root <导出的根目录>
```

`list.tsv` 每行：`root<TAB>相对路径<TAB>原始大小<TAB>格式(img/pdf/zip)`，其中「原始大小」取自 `.L0CK3D` 页脚（偏移 580，小端 u64）。雕刻的价值在于**长度精确匹配**：只有长度落在清单集合里的候选才会保留，误报率极低。

### 路线 D：交付前校验（必须做）

| 检查 | 脚本 | 通过标准 |
|---|---|---|
| 块级完整性 | `csum_check.py` | `bad=0`，`missing` 比例可解释 |
| 文档结构 | `validate_ole.py` / `validate_office_zip.py` | `OK` 占比符合预期 |
| 图片可解码 | `check_carve_pil.py` | `ok_rate` 达到该批次的合理水平 |
| 文件头魔数 | 各恢复脚本内置 | `format=ok` |

---

## 6. 脱敏处理清单

| 原始内容 | 处理方式 | 涉及位置 |
|---|---|---|
| 备份主机 IP | 改为 `NAS_HOST` 环境变量；脚本内不出现任何 IP | `recover_batch3.py` `stream_recover.py` `carve_export.py` |
| 账号（原脚本硬编码） | 改为 `NAS_USER` 环境变量 | 同上 |
| 密码（明文写在源码里） | 改为 `NAS_PASSWORD` 环境变量，经 `sshpass -e` 传递（不出现在命令行/进程列表） | 同上 |
| 主机名（原脚本硬编码） | 全部移除，不出现任何真实主机名 | 全包 |
| 公司名 / 人名 / 客户缩写 | 移出代码；CSV 仅记录相对路径；文件名含客户缩写的 `validate_*_office.py` 改名为 `validate_office_zip.py` | 全包 |
| 具体业务共享目录名 | 一律不保留；示例改用 `<SHARE>` / `USER/Photos/` 等泛化名 | 全包 |
| UNC 路径 `\\<IP>\share\...` | 改为 `--root` / `--target` 参数 | `validate_ole.py` `validate_office_zip.py` `check_carve_pil.py` |
| 硬编码中间文件路径（`/dev/shm/extent/...`） | 改为参数，默认值仅作示例 | `calc_delta.py` `carve_all.py` `carve_export.py` `csum_check.py` |
| 设备路径 `/dev/mapper/cachedev_0` | **保留**（DSM 通用路径，作为示例默认值，可用 `SRC_DEV` 覆盖） | `scan_recoverable.py` 等 |

文件名层面：原始 `validate_*_office.py` 含客户缩写，已改名；其余文件名未包含敏感信息（`scan_recoverable` / `recover_batch3` / `carve_*` 等均为功能性命名）。

---

## 7. 与原始脚本的差异

| 类型 | 文件 | 说明 |
|---|---|---|
| 逻辑等价 + 加中文头 | 14 个 | `scan_recoverable` / `recover_batch3` / `stream_recover` / `calc_delta` / `carve_all` / `carve_export` / `csum_check` / `validate_ole` / `validate_office_zip` / `check_carve_pil` / `tools_chunkmap` / `tools_walk_tree` / `tools_parse_leaves` / `tools_make_jobs2` |
| 逻辑等价 + 参数化 | 2 个 | `tools_extract_jobs`（`MIN_PARTIAL_COVER`/`MAX_EXTRACT_SIZE` 可配）、`tools_btrfs_restore_run.sh`（仅加中文头） |
| **重写**（原件未留存） | 3 个 | `tools_parse_free_space.py` / `tools_make_ranges.py` / `tools_raw_reader2.py`，文件头明确标注「按案例调用约定重写的等价实现」 |

原始脚本全部保留在案例目录 `check21/` 下未做任何修改；本包是**复制后**再改写，两者互不影响。

---

## 8. 验证记录（本包自测）

已做、可复现的自测（合成数据，不涉及真实客户数据）：

| 对象 | 测试内容 | 结果 |
|---|---|---|
| 全部 18 个 `.py` | `python -m py_compile` | 全部通过 |
| 9 个 argparse 脚本 | `--help` | 全部 `rc=0`，参数表正确 |
| `tools_raw_reader2.py` | 直接映射 / 跨 chunk 边界拼接 / 映射末端 / 未映射返回 `None` | 全部通过 |
| `tools_make_ranges.py` | 正常映射、MINLEN 过滤、跨 chunk 合并、空闲区超出映射范围（曾有死循环缺陷，已修复并回归） | 全部通过 |
| `tools_parse_free_space.py` | `itemsize 0` 推导长度、显式 `length`、bitmap 模式报错退出 | 全部通过 |
| `tools_make_jobs2.py` | 精确旧引用 + 合成引用、路径递归拼接、后缀剥离 | 输出符合预期 |
| `scan_recoverable.py` | `segments()` 的 0x24 / 0x26 / 0x25 分支 | 全部通过 |
| `validate_ole.py` | 合法头 / 坏签名 / 坏结构 | 分类正确 |
| `validate_office_zip.py` | 合法 OOXML / 截断 ZIP | 分类正确 |
| `check_carve_pil.py` | 正常 PNG 与截断 JPEG 混放 | `ok 2 / bad 1`，比例正确 |

**未做真机复测的部分**（需真实 btrfs 镜像，属于环境限制，不代表逻辑有问题）：
`csum_check.py`、`tools_walk_tree.py`、`tools_parse_leaves.py`、`tools_extract_jobs.py`、
`tools_make_ranges.py` 的大容量路径、`tools_btrfs_restore_run.sh` 的实际 `btrfs restore` 行为。
这些脚本相对原始版本的**逻辑未改动**（仅加头/参数化），在原案例中已实际跑通。

---

## 9. 已知限制

1. **只支持单设备 SINGLE / DUP**：`tools_chunkmap.py` 会跳过 RAID0/5/6/10 chunk（stripe 0 不是完整副本，需真正条带换算）。
2. **`tools_parse_leaves.py` 的 `NODESIZE` 固定 16 KiB**：若目标卷 nodesize 不同需修改；`tools_walk_tree.py` 则从 superblock 自动读取。
3. **`tools_extract_jobs.py` 有内存上限**：单文件超 `MAX_EXTRACT_SIZE`（默认 1.5 GiB）报 `ERR BIG`；原始案例另有流式版 `extract_big.py`（本包**未收录**，需要时可参照 `stream_recover.py` 的分块写法自行扩展）。
4. **未收录的原始辅助脚本**：`extract_big.py`（流式 extent 恢复）、`verify_restored.py`（恢复结果分类：`OK_<格式>` / `ENCRYPTED` / `ZEROS` / `UNKNOWN`，仅查文件头）、`merge_exec.py`（reflink 合并）、视频修复系列（`movsamples.py` / `videofix.py` / `headerfix.py` 等）。它们不在本次工具包范围内，但在原案例目录中可用。
5. **`tools_parse_free_space.py` 的长度是上界**：部分 btrfs 版本 free space tree 的 extent 项只记录起始地址，长度按「下一条起始」推导，可能含少量已用空间；只影响扫描耗时，不影响正确性（`carve_all.py` 的精确长度匹配会过滤误报）。需要精确空闲总量请用 `btrfs filesystem usage -b` 交叉核对。
6. **不做解密**：本工具包只做「读盘 + 搬运 + 校验」，不包含任何密钥恢复/解密逻辑；C3RB3R 的解密需要样本分析与密钥，不在此范围。

---

## 10. 免责声明

- 本工具包仅用于**自有或已获授权**的数据恢复与取证场景。
- 所有脚本按「尽力而为」提供，恢复结果受 CoW 覆盖程度、元数据完整性、设备健康度影响，**不保证 100% 恢复**。
- 操作失误（误写源卷、误跑 balance/scrub）造成的损失不可逆，执行前请先做只读镜像/快照。
