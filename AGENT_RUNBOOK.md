# C3RB3R Btrfs 恢复 · AI 执行手册（AGENT_RUNBOOK）

> 给 AI Agent / 自动化流程使用的阶段机。人类读者请先看 `README.md` 与 `02_forensics.md`。
> **核心原则：只读源盘、先验证再全量、每文件必须校验、失败即记录并继续。**

---

## 0. 角色与总目标

你是数据恢复工程师。给定：
- 源端：一台装有被 C3RB3R 加密（`.L0CK3D`）数据的 Btrfs 卷 NAS（DSM/Linux），可 SSH（root/sudo）；
- 目标端：另一台有足够空间的 NAS/服务器（恢复产物落地处，**必须不是源卷**）。

目标：在**只读源盘**前提下尽可能多地恢复**可打开的正确文件**，并输出可核验的统计与失败清单。

---

## 1. 硬约束（违反即任务失败）

1. **绝不写源盘**：不创建/删除/改名/改权限/写入任何字节；读文件尽量带 `O_NOATIME`。
2. **绝不运行**：`btrfs balance`、`scrub`、`defrag`、`subvolume delete`、`check --repair`、`fsck`。
3. **绝不在源卷落地**恢复产物；产品写目标端（如另一台 NAS 的共享目录）。
4. 所有恢复产物必须**逐文件校验**（格式/结构/解码；最高标准是 Btrfs CRC32C csum）。
5. 不确定是否只读的命令不要执行；先问。

---

## 2. 阶段 0：现场保全判断（最先做）

| 判断项 | 命令 | 分支 |
|---|---|---|
| 加密进程是否还在 | `ls -l /proc/*/exe 2>/dev/null \| grep -i <sample>`；`ps aux \| grep -v grep \| grep <sample>` | **还在** → 立即 `kill -STOP <pid>`，转「内存密钥提取」路线（见 `02_forensics.md` 第 6 节）；**已退出** → 走 Btrfs 路线（本手册） |
| 系统是否重启过 | `uptime`、核验内存现场 | 重启过 → 内存密钥路线不可用 |
| 卷是否被写过 | 检查 `find <SHARE> -newermt '-24 hours'` 等 | 有大量新写入 → 旧 extent 被复用，恢复率显著下降（尽早停写） |

**要求**：把结果记录到 `state.json`（`{"phase":0, ...}`）。

---

## 3. 阶段 1：环境自检与普查

1. 确认工具：`btrfs`、`btrfs-debug-tree`、`btrfs-find-root`、`python3 >= 3.8`。
2. 确认卷与设备：`/proc/mounts` 中的 btrfs 行、`/dev/mapper/*` 下的 cachedev/vgN-volumeN。
3. **CoW/NoCOW 判定**：`lsattr -d <SHARE>` — 带 `C` 标志为 NoCOW（恢复率会大幅降低，原因见 principles）。
4. 普查（工具：`scan_recoverable.py`）：
   - 统计各目录 `.L0CK3D` 数量、按 footer 模式（0x24/0x25/0x26）分布、总字节；
   - 0x25/0x26 = 可 CoW 恢复候选；0x24 = 只能雕刻（图片/文档）；
   - 输出 `scan_<name>.json`（含每个候选的 `path/orig/delta`）。
5. 阈值：若 0x25+0x26 占比为 0 → 直接跳「雕刻」与「其他来源」路线。

---

## 4. 阶段 2：方法选择决策树

```
有 .L0CK3D 加密文件？
├─ 进程还活着 → 内存密钥路线（kill -STOP → 内存扫描 → 解密）
└─ 进程已退出 → Btrfs 路线
    ├─ 0x25/0x26 文件（部分加密）
    │   ├─ 卷/共享是 CoW（无 C 标志）→ delta 法（阶段 3）；失败样本再走精确 EXTENT_DATA 法（阶段 4）
    │   └─ 是 NoCOW（有 C 标志）→ 仅"last_snapshot 前写入"的少量文件可能可救（抽样验证，成功率低）
    ├─ 0x24 文件（整文件加密）→ 空闲空间雕刻（阶段 5）：只能救图片/PDF/ZIP/OLE
    └─ 旧根 bookend（阶段 4b）：仅当旧根/旧叶子可解析且旧 extent 未被覆盖时有效
```

**关键判据（务必先抽样 10–30 个文件验证，再全量）**：
- 恢复样本需同时满足：文件头/结构正确 + 长度与 footer 记录一致；
- 样本成功率 < 30% → 停止该方法（换方法或记录为不可恢复）。

---

## 5. 阶段 3：delta 法（CoW 旧 extent，主方法）

1. 原理：C3RB3R 原地改写文件的部分区段；Btrfs CoW 把新数据写到新 extent，未被加密的区段仍引用**旧 extent**。未加密 extent 的 `physical - logical`（delta）可推断原文件逻辑基址 D，从 D 读回原文件完整数据。
2. 工具：`recover_batch3.py --scan <scan.json> --target <目标目录>`（逐文件读设备 → 流式 tar 到目标端）。
3. 成功判据：文件头魔数正确（PDF/JPG/PNG/ZIP/OLE 等）+ 大小 = footer 的 orig。
4. 失败分支：`read_fail`/`format mismatch` → 记入失败清单，交给阶段 4/5 或标记不可恢复。
5. 大文件（>1.5GB）：用 `stream_recover.py`（分块流式，避免内存溢出）。

---

## 6. 阶段 4：精确 EXTENT_DATA 法（碎片文件/复杂情形）

1. 用 `walk_tree.py` 遍历当前 fs tree，导出各 inode 的 `EXTENT_DATA`（foff/disk_bytenr/eoff/nbytes/comp）。
2. `make_jobs2.py` 生成恢复任务（只取攻击前 generation 的旧 extent 引用 + 整 extent 合成）。
3. `extract_jobs.py` 按 jobs 读旧 extent 的加密区间、与现存明文拼接，输出文件（读回=密文则拒绝）。
4. 适用：delta 法失败但旧 extent 引用仍可从当前树/旧叶子获取的文件。
5. **4b. 旧根 bookend**：`btrfs-find-root -a <DEV>` 找旧根 → `parse_leaves.py`/`tree_walk.py` 从旧根取 EXTENT_DATA。**前提是旧根/旧叶子仍可解析、旧 extent 未被覆盖**；否则放弃（本案例 volume1 旧叶子不可解析、volume2 NoCOW 覆盖，故失效）。

---

## 7. 阶段 5：空闲空间雕刻（0x24 小文件的唯一出路）

1. 只读导出空闲空间树：`btrfs-debug-tree -t 10 <DEV>` → `parse_free_space.py` → `make_ranges.py`（逻辑→物理区间）。
2. 扫描空闲区间（`carve_all.py`）：按「格式魔数 + 原始长度 N」精确匹配：
   - JPEG（FFD8FF→FFD9）、PNG（签名→IEND）、PDF（%PDF→%%EOF）、ZIP（PK0304→EOCD）、OLE（D0CF11E0 + 目录项推算长度）。
3. 导出候选（`carve_export.py` 流式 tar）→ 目标端。
4. 校验：图片 PIL、ZIP zipfile（含 `[Content_Types].xml` 判 Office）、PDF 头尾、OLE 结构。
5. 去重/归并：按原始清单的 (root, rel, size) 把候选归入对应原目录；无法归属的保留在统一雕刻目录。
6. 阈值：候选校验通过率 < 30% → 检查清单/雕刻匹配逻辑。

---

## 8. 阶段 6：Btrfs csum 校验（交付质量证明）

1. 遍历 csum 树（tree 7 的 `EXTENT_CSUM` 项：key offset=逻辑地址，data=每 4K 块 CRC32C）。
2. 对抽样文件的逻辑区间逐 4K 块计算 CRC32C 与 csum 比对。
3. 通过标准：匹配率 100%（0 不一致、0 缺失）→ 数据可信；出现不一致 → 该文件可能是拼接错误或已被覆盖。

---

## 9. 阶段 7：回收站与目录归并

1. 交付目录中 `#recycle/` 下的文件：与原路径比较——原路径不存在则还原；原目录版本更新则跳过；`#recycle` 更新才覆盖。
2. 雕刻产物按原始清单（root+rel+size）归入 `back/<原目录名>/`。

---

## 10. 阶段 8：报告与交付

交付物：恢复目录（按原结构）+ SHA256 清单（`sha256sum -c` 可校验）+ 失败清单 + 统计报告（大小档/目录维度/方法维度）。

---

## 11. 阈值总表

| 指标 | 正常 | 低于下限的动作 |
|---|---|---|
| 抽样文件结构正确率 | ≥ 80% | < 30% 停止该方法 |
| delta 法抽样成功率 | 70–100% | < 50% 检查 CoW/delta 推断 |
| 雕刻候选校验通过率 | ≥ 70% | < 30% 检查清单/匹配 |
| csum 块匹配率 | 100% | 出现不一致 → 单独复核该文件 |
| 解码失败率 | < 1% | > 5% 停止并复查方法 |

## 12. 错误签名 → 处理

| 现象 | 含义 | 处理 |
|---|---|---|
| `read_fail` / `unmapped logical` | 逻辑地址不在 chunk 映射 | 跳过该文件并记录 |
| `same_as_cipher` | 读回=当前密文 | 旧 extent 被覆盖/引用是密文段 → 换方法或标记不可恢复 |
| `OverflowError: Python int too large` | 单次 pread 超 2GB | 改分块流式（stream_recover） |
| `tar: time stamp ... in the future` | 源/目标时钟偏差 | 无害，忽略 |
| 写入目标 `/tmp` 满 | 临时目录空间不足 | 用流式、不落本地盘（/dev/shm 或直接 ssh 管道） |
| 文件名 `.pdf`→`.pd` | 去后缀长度算错（`.L0CK3D` 是 7 字符） | 检查 `rel[:-7]` |

## 13. 停止与上报条件

1. 发现源盘有新写入（`find <SHARE> -newermt` 非空）→ 立即停写并上报；
2. 需要执行任何可能写源盘的操作；
3. 抽样恢复率持续为 0 且两种方法均已试 → 记录结论，转"其他来源"建议（客户端/备份/邮件/网盘）。
