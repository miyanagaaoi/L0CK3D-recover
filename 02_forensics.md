# 02 现场取证与原理（C3RB3R 格式 + Btrfs CoW/NoCOW）

## 1. C3RB3R（.L0CK3D）文件格式

加密后的文件容器：

```
[ 数据区（= 加密后的文件内容） ][ 304B 加密块 ][ 264B 随机 ][ 20B 尾注 ]  → 末尾 588B 为 footer
```

footer 关键字段（偏移相对 footer 起点）：

| 偏移 | 内容 |
|---:|---|
| 568 | magic（本案例 4 字节，`72 fb 31 9f`，不同样本可能不同） |
| 572 | mode：`0x24` 整文件加密 / `0x25` 分段加密 / `0x26` 前 1MiB 加密 |
| 573 | pct：百分比（决定 0x25 的分段长度） |
| 574 | crypto：`0x20`（真）/`0x21`（诱饵） |
| 580 | 原始文件长度 N（u64 小端） |

**加密区间计算**：
- `0x24`：`[0, N)` 全部加密（≤1MiB 的小文件占绝大多数）；
- `0x26`：`[0, min(N, 1MiB))` 加密，其后为**明文**；
- `0x25`：`ln = (N // 100) * PF[pct]`（PF 表：10→4、15→5、20→7、25→9、30→10、35→12、40→14、50→10、60→20、70→23、80→27），`gap = (N - 3*ln) // 2`，加密 `[0,ln)`、`[ln+gap, 2ln+gap)`、`[N-ln, N)` 三段（约 12% 数据）。

**含义**：0x26/0x25 文件的**大部分数据是明文**，只是头部（或头+尾）在密文区 → 无法直接打开，但可作为 CoW 恢复的依据。

## 2. Btrfs 写时复制（CoW）与 bookend extent

- Btrfs 改文件时把新数据写到**新位置**（新 extent），并把文件的 `EXTENT_DATA` 指向新 extent；
- 旧 extent **不会立即释放**：只要文件里还有任何未被改写的部分引用它，整个旧 extent 就保留；
- 被勒索加密的"部分区段"（0x25/0x26）对应的原始明文因此仍留在磁盘上——这就是 **bookend extent**；
- 只要从当前 fs tree（或旧树）拿到原 extent 引用，按 `disk_bytenr + (offset - eoff)` 读回即可还原原文。

关键结构（`btrfs-debug-tree` 可读）：
- **EXTENT_DATA 项**：`foff`（文件内偏移）、`egen`（数据世代）、`disk_bytenr`（逻辑地址）、`disk_num_bytes`、`eoff`（extent 内偏移）、`nbytes`；
- **chunk 树**（tree 3）：逻辑地址 → 物理地址（`logical length physical`）；
- **csum 树**（tree 7）：每个 4K 块的 CRC32C（key offset=逻辑地址）。

## 3. NoCOW（`+C`）的例外与灾难

- 共享开启"数据校验和/NoCOW"（DSM 中共享属性）后，目录带 `C` 标志（`lsattr -d <SHARE>` 可见）；
- NoCOW 写入**原地覆盖**旧数据 → 旧 extent 数据被密文覆盖 → **无法恢复**；
- **例外**：若 extent 的世代 < 子卷 `last_snapshot`，覆盖必须走 CoW（快照在引用它）→ 这类文件仍可救。因此 NoCOW 共享中"快照前写入、快照后未被改写"的数据有少量可恢复；
- 本案例 volume2 为 `+C` 卷：主要业务共享的恢复率极低（很多目录 0%），部分文件靠该例外恢复。

## 4. 现场检测要点

| 检测 | 方法 |
|---|---|
| 卷与设备 | `cat /proc/mounts \| grep btrfs`、`ls -l /dev/mapper/` |
| CoW/NoCOW | `lsattr -d <SHARE>`（有 `C` = NoCOW） |
| 快速判断加密模式 | 读文件末尾 588B，解析 572/573/574/580 |
| extent 映射 | `btrfs-debug-tree -t 3 <DEV>`（chunk 树）+ EXTENT_DATA（当前树） |
| 空闲空间 | `btrfs-debug-tree -t 10 <DEV>`（0x24 雕刻用） |
| csum | 遍历 tree 7 的 `EXTENT_CSUM` 项 |
| 旧根 | `btrfs-find-root -a <DEV>`（bookend 用；能否解析出旧叶子决定成败） |

**注意**：FIEMAP 只给出 extent 起始（`disk_bytenr`+`eoff`），**拿不到完整 extent 长度**；要完整映射需用 extent 树/EXTENT_DATA。

## 5. 何时"救不回"（本案例确认）

1. `< 1MiB` 整文件加密（0x24）：旧 extent 全部释放，磁盘数据被覆盖后无解（空闲空间雕刻只能救图片/文档）；
2. `+C` 共享中**快照之后写入**的数据：原地覆盖，无解；
3. 数据库文件（SQLite/自研格式）：即使部分数据可得，也无法重建一致性；
4. 文件头在密文区的 0x25/0x26 文档：明文区无文件头，无法组装成可打开文件。

## 6. 内存密钥路线（进程仍存活时）

若加密进程还在运行：
1. `kill -STOP <pid>` 冻结（不要 kill、不要重启）；
2. 只读导出进程内存（`/proc/<pid>/mem` 或 `/proc/kcore`，需 root；NAS 上可能禁止 kcore）；
3. 扫描内存查找 48 字节主材料/会话密钥（参考 soimy/c3rb3r-rescue 与 Benjamin-zlt 的 baremetal rescue 工具）；
4. 拿到密钥后可**完整解密全部文件**（远高于本手册的 CoW 恢复率）。
> 本案例进程已退出，此路线不可用。
