# 03 恢复方法与实测效果

## 方法总览

| 方法 | 对象 | 原理 | 适用条件 | 本案例效果 |
|---|---|---|---|---|
| **A. delta 法** | 0x25/0x26 | 未加密 extent 的 `physical - logical` 推断原文件基址 D，从 D 读回完整原文 | CoW 卷、旧 extent 未被覆盖 | 主力：多个 CoW 共享，抽样 csum 100% 匹配 |
| **B. 精确 EXTENT_DATA 法** | 0x25/0x26（碎片化） | 遍历当前 fs tree 的 EXTENT_DATA，逐 extent 读加密区间并与现存明文拼接 | 当前树仍有旧 extent 引用 | 补充 A 的部分失败文件 |
| **C. 空闲空间雕刻** | 0x24（≤1MB 整文件加密） | 从 btrfs 空闲空间按「格式魔数 + 原始长度 N」雕刻完整文件 | 空闲空间未被覆盖 | **约 4.5 万个**图片/PDF/ZIP/OLE 文档 |
| **D. 旧根 bookend** | A/B 失败文件 | 从 `btrfs-find-root` 找到的攻击前旧树取 EXTENT_DATA | 旧根/旧叶子可解析、旧 extent 未覆盖 | 本案例失败（volume1 旧叶子不可解析、volume2 NoCOW 覆盖），仅开源工具案例成功 |
| **E. csum 校验** | 交付前验证 | 遍历 csum 树（tree 7）逐 4K 块 CRC32C 比对 | 有 csum 树且数据来自旧 extent | 抽样 16 个文件 / 9,512 块 **100% 匹配** |
| F. 回收站还原 | `#recycle` | 与原路径比较 mtime，还原较新/原目录缺失文件 | — | 1,707 个还原 |

## A. delta 法（主方法）细节

1. 解析 footer 得 mode/pct/N；
2. 用 FIEMAP 找文件"未加密区段"的 extent，计算 `delta = physical - logical`（多个时取唯一值/最大覆盖）；
3. 原文件逻辑基址 `D = delta`（即原文件与当前加密文件共享同一逻辑起点）；
4. 解析 chunk 树（`btrfs-debug-tree -t 3`），从裸设备按 chunk 映射读 `[D, D+N)`；
5. 校验文件头/大小；成功则流式写到目标端（tar over ssh，不落本地）。
6. 大文件（>1.5GB）必须分块流式，单次 `pread` 超 2GB 会报 `OverflowError`。

**实测**：在 volume1（无 `+C`）抽样 csum 100% 匹配；在 `+C` 卷仅部分文件成功。

## B. 精确 EXTENT_DATA 法细节

1. `walk_tree` 遍历当前 fs tree → 每个 inode 的 EXTENT_DATA（含 `egen`）；
2. 取 `egen < G_ATTACK` 的旧 extent 引用（G_ATTACK 由攻击时间段的世代区间确定，可用 `btrfs subvolume find-new`/ransom note inode 的 transid 辅助）；
3. 逐 extent 读回加密区间，与文件现存明文拼接；若读回内容等于当前密文则拒绝（旧 extent 已被覆盖）；
4. 用 `make_jobs2 + extract_jobs` 批量执行。

**实测**：对 volume1 的碎片化文件有效；对 `+C` 目录基本 FAIL（读回=密文）。

## C. 空闲空间雕刻细节

1. 空闲空间树（tree 10）→ 空闲 logical extent → chunk 映射为物理区间（约 41.85 GB）；
2. 清单：从全盘 `.L0CK3D` 中筛出 0x24 的可雕刻格式（图片/PDF/ZIP/Office/OLE），得到 `(rel, N, fmt)`；
3. 扫描空闲区间，按格式识别完整文件并匹配长度 N：
   - JPEG：FF D8 FF → FF D9
   - PNG：89 50 4E 47 → IEND
   - PDF：%PDF → %%EOF
   - ZIP（xlsx/docx/pptx）：PK\x03\x04 → EOCD（22+注释长度）
   - OLE（xls/doc/ppt）：D0 CF 11 E0 头 + 目录项推算文件长度（无固定尾标记，需结构校验）
4. 导出（tar 流式）→ 目标端；
5. 校验：图片 PIL、ZIP `zipfile.testzip()`（并按是否含 `[Content_Types].xml` 判 Office）、PDF 头尾、OLE 头+目录项；
6. 按原始清单的 `(root, rel, size)` 归入原目录；无法归属的保留在统一雕刻目录。

**实测结果**（本案例）：

| 类型 | 候选 | 校验通过 | 通过率 |
|---|---:|---:|---:|
| 图片（JPEG/PNG） | 25,183 | **15,923** | 63%（合并去重后） |
| Office 文档（docx/xlsx/pptx，ZIP） | — | 7,020 | — |
| 普通 ZIP | — | 308 | — |
| PDF | — | 78 | — |
| OLE（xls/doc） | 23,503 | **22,035** | 93.8% |
| **合计** | — | **约 4.5 万个** | — |

> 注：雕刻文件只能保证"内容完整可打开"，**文件名按长度匹配可能张冠李戴**；唯一匹配（certain）可信度较高。

## D. 旧根 bookend 失败原因（本案例）

- volume1：`btrfs-find-root` 找到 15 个 level-0 旧叶子，但 `parse_leaves` 解析**全部 bad**（块头校验失败）→ 无法取旧 EXTENT_DATA；
- volume2（NoCOW）：旧根引用能取到，但读回数据全为密文（原地覆盖）→ 0 个成功。
- 结论：**bookend 依赖现场条件**（旧根/旧叶子可解析 + 旧 extent 未被覆盖），不是通用方案。

## E. csum 校验（交付质量证明）

- 抽样 16 个文件（体积 4–8MB 文件），逐 4K 块计算 CRC32C 与 csum 树比对：
  - volume1 8 个文件 / 5,008 块：**100% 匹配**
  - volume2 8 个文件 / 4,504 块：**100% 匹配**
- 意义：证明恢复数据是磁盘上未被破坏的原始数据块，而非拼接错误。

## 效果汇总（本案例，已脱敏）

| 大小档 | 源端加密文件 | 恢复文件 | 文件数恢复率 | 字节恢复率 |
|---|---:|---:|---:|---:|
| < 1MB | 686,831 | 44,919 | 6.54% | 4.51% |
| 1–128MB | 87,477 | 27,971 | **31.98%** | **38.88%** |
| ≥ 128MB | 1,016 | 170 | 16.73% | 9.39% |
| 合计（不含 @synologydrive） | 775,324 | 73,060 | **9.42%** | **21.37%** |

> 目录维度：共享A 15.56%、共享B 15.32%、共享C 9.92% 为有效主力；`+C` 的其他共享基本为 0%（仅雕刻少量图片/文档）。
