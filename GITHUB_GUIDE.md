# C3RB3R Btrfs 恢复 · GitHub 开源指引

> 本文件是本仓库的**开源使用指引**：怎么开始、怎么把技能引入 AI 工具、怎么选工具与模型、每一步注意什么、中文怎么写、参考哪些资料。
> 面向：数据恢复工程师、运维/SRE、安全应急响应人员，以及用 AI Agent 执行恢复任务的团队。

---

## 0. 这是给谁看的

| 读者 | 建议阅读顺序 |
|---|---|
| 第一次接触本仓库 | 本文件第 3 章（快速开始）→ `README.md` |
| 用 AI 执行恢复 | 第 4 章（引入技能）→ `AGENT_RUNBOOK.md` |
| 评估是否适用 | 第 1 章（定位）→ `04_lessons.md`（边界） |
| 写恢复报告 | `05_report_template.md` + 第 7 章（中文表达） |
| 二次开发 | `toolkit/README.md` + `toolkit/tools_manifest.json` |

---

## 1. 项目定位与免责声明

**适用**：Synology DSM（或 Linux）上的 Btrfs 卷被 C3RB3R 家族（`.L0CK3D` 后缀）加密后的**无密钥恢复**。

**不适用/无效**：
- 加密进程仍在运行时（应先走内存密钥路线，见第 5 章）；
- `+C`（NoCOW）共享中快照后写入的数据（原地覆盖，基本无解）；
- `< 1MB` 整文件加密的文件（只能靠空闲空间雕刻救少量图片/文档）。

**免责声明**：本项目是**恢复方法参考与工具集**，不保证任何现场、任何文件能恢复；恢复结果取决于现场条件（是否停写、卷类型、时间窗口）。请先在少数样本上验证，再决定是否投入全量。

**安全红线**（详见 `AGENT_RUNBOOK.md` 第 1 章）：
1. 绝不写源盘；
2. 绝不运行 `btrfs balance/scrub/defrag`、`check --repair`、`fsck`；
3. 绝不在源卷落地恢复产物。

---

## 2. 仓库结构

```
.
├── GITHUB_GUIDE.md（本文件）
├── README.md                 总览与快速导航
├── AGENT_RUNBOOK.md          AI 执行手册（阶段机/判据/失败分支）
├── case_summary.json         机器可读摘要
├── manifest.json             文件与工具清单
├── 01_incident.md            事件经过
├── 02_forensics.md           C3RB3R 格式 + Btrfs 原理
├── 03_recovery_methods.md    五类方法细节与效果
├── 04_lessons.md             经验/边界/决策表/常见坑
├── 05_report_template.md     报告模板
├── SECURITY.md               安全与负责任披露说明
├── LICENSE                   MIT
└── toolkit/                  脱敏后的可复用脚本 + 工具清单
```

---

## 3. 怎么开始（快速上手）

### 3.1 前置条件

- 源端：能被 SSH 访问的 Btrfs NAS（root/sudo），推荐先只读挂载到分析机；
- 目标端：另一台有足够空间的 NAS/服务器（**不是源卷**）；
- 工具：`btrfs-progs`（含 `btrfs-debug-tree`/`btrfs-find-root`）、Python ≥ 3.8；
- 分析机（可选）：Python + Pillow（图片校验）、ffmpeg（视频校验）、7-Zip（压缩包校验）。

### 3.2 第一步：现场保全（最优先）

| 动作 | 说明 |
|---|---|
| 立即停写 | 不重启、不重装、不删除 `.L0CK3D`、不清理磁盘 |
| 记录现场 | 进程状态、`/proc/mounts`、卷布局、加密开始/结束时间 |
| 判断进程 | 加密进程还活着？→ `kill -STOP` + 内存密钥路线（见第 5 章） |

> **注意**：恢复窗口越早越好。本案例因延迟 1~2 天才开始，部分旧 extent 已被覆盖。

### 3.3 第二步：环境自检（脚本：`toolkit/scan_recoverable.py` 前置）

```bash
# 确认工具
which btrfs btrfs-debug-tree btrfs-find-root; python3 -V

# 确认卷与 CoW/NoCOW
cat /proc/mounts | grep btrfs
lsattr -d /volume1/<SHARE> /volume2/<SHARE>   # 有 C 标志 = NoCOW
```

> **注意**：`+C`（NoCOW）共享的恢复率会大幅下降；先确认，避免全量投入后才发现。

### 3.4 第三步：普查（生成候选清单）

```bash
# NAS 端（只读）
sudo python3 toolkit/scan_recoverable.py --root /volume1/<SHARE> --out /dev/shm/scan.json
```

产出：候选清单（`path/orig/delta` 或 `rel/N/fmt`），以及模式分布（0x24/0x25/0x26）。

> **注意**：先只扫 1~2 个共享评估，不要一上来全盘扫描。

### 3.5 第四步：抽样验证（关键步骤）

- 从候选中抽 10~30 个文件，跑对应方法（delta / 精确 extent / 雕刻）；
- 检查：文件头/结构正确 + 长度与 footer 一致；
- **成功率 ≥ 70% 才值得全量**；< 30% 换方法或停止。

### 3.6 第五步：全量执行

```bash
# delta 法（主方法）
python3 toolkit/recover_batch3.py --scan /dev/shm/scan.json --target <目标目录>
# 大文件
python3 toolkit/stream_recover.py --scan /dev/shm/scan.json --target <目标目录>
```

> **注意**：产物写目标端；大文件必须分块流式（单次 pread > 2GB 会溢出）。

### 3.7 第六步：校验与报告

- 结构/解码校验（PIL/zipfile/ffmpeg，或最高标准 Btrfs CRC32C csum → `toolkit/csum_check.py`）；
- 生成 SHA256 清单 + 失败清单 + 分维度统计（`05_report_template.md`）。

---

## 4. 如何引入技能（AI Skill 接入）

本仓库同时是**一份 Agent Skill**（`AGENT_RUNBOOK.md` + `toolkit/`）。引入方式三选一：

### 方式 A：作为 AI 工具技能包（推荐）

把本仓库放入 AI 工具的 skills 目录（不同工具路径不同，常见约定）：

```bash
# 以 Claude Code 为例（路径按工具文档调整）
git clone <repo> ~/.claude/skills/c3rb3r-btrfs-recovery
# 或使用 skills 包管理器（若工具支持）
npx skills add <owner>/<repo>
```

在对话中直接说：
> 「用 c3rb3r-btrfs-recovery 技能，按 AGENT_RUNBOOK 帮我在这个 NAS 上做只读恢复，先抽样验证再全量。」

### 方式 B：作为项目规则（零安装）

把 `AGENT_RUNBOOK.md` 复制为项目根目录的 `AGENTS.md`（或工具对应的规则文件，如 `CLAUDE.md`、`.cursor/rules`），AI 在该项目内会自动遵循阶段机与红线。

### 方式 C：人工对照执行

不接 AI 时，人类按 `AGENT_RUNBOOK.md` 阶段机与 `toolkit/README.md` 手动执行。

### 引入技能后的提示词模板

```text
角色：你是数据恢复工程师。
现场：<NAS 地址>，Btrfs 卷，被 C3RB3R（.L0CK3D）加密，进程已退出。
任务：按 c3rb3r-btrfs-recovery 的 AGENT_RUNBOOK 执行：
1) 只读检查（禁止写源盘、禁止 balance/scrub）
2) 普查并输出候选清单
3) 抽样 20 个文件验证 delta 法成功率
4) 成功率 >70% 再全量，产物写目标端
5) 每文件校验，最后给统计报告与失败清单
约束：不确定的命令先问我，不要执行任何写操作。
```

> **注意**：技能只是"作业指导"，AI 仍可能写错命令——每次执行前复核关键命令（尤其 `rm`、`dd`、`btrfs` 写操作）。

---

## 5. 工具与 AI 选择

### 5.1 恢复路线选择（先判断，再动手）

| 现场条件 | 路线 | 工具/参考 |
|---|---|---|
| 加密进程仍存活、内存可读 | **内存密钥 → 全量解密**（最高价值） | [soimy/c3rb3r-rescue](https://github.com/soimy/c3rb3r-rescue)、[Benjamin-zlt baremetal rescue](https://github.com/Benjamin-zlt/c3rb3r-baremetal-rescue) |
| CoW 卷 + 0x25/0x26 | delta 法 / 精确 EXTENT_DATA 法 | 本仓库 `toolkit/` |
| `+C`（NoCOW）共享 | 精确法抽样（成功率低），否则转雕刻/其他来源 | 本仓库 |
| 0x24 小文件（图片/PDF/ZIP/OLE） | 空闲空间雕刻 | 本仓库 `carve_all.py` |
| 需要工程化流程/校验 | 借鉴 crt106 的 Skill 流程（含 csum 校验） | [crt106/c3rb3r-btrfs-recovery](https://github.com/crt106/c3rb3r-btrfs-recovery) |
| 以上都失败 | 从客户端/备份/邮件/网盘找回 | 业务侧 |

### 5.2 AI 工具选择（执行型任务）

| 工具类型 | 适合场景 | 注意 |
|---|---|---|
| Claude Code / Codex / Cursor 等编码 Agent | 按 `AGENT_RUNBOOK` 多步执行、调脚本 | 需要能执行 shell/SSH；上下文要长 |
| 通用对话模型 | 方案讨论、命令生成 | 不要让它直接执行不可逆命令 |
| 本仓库的 `AGENT_RUNBOOK.md` | 任何支持"读文件 + 执行工具"的 Agent | 先让它读手册再动手 |

**选择原则**：
1. 需要**执行**（SSH/脚本/校验）→ 选带工具执行能力的 Agent；
2. 需要**长流程**（普查→恢复→校验→报告）→ 选长上下文 + 可持久化文件（state.json）的；
3. 需要**安全**→ 所有写操作前人工复核；让 AI 先输出"将要执行的命令"再批准。

### 5.3 自动化程度建议

| 阶段 | 建议 |
|---|---|
| 环境自检/普查 | 可全自动（只读） |
| 抽样验证 | 可自动，但结果要人工看 |
| 全量恢复 | 自动执行 + 人工监控进度 |
| 校验/报告 | 自动生成，人工抽检 |
| 任何写/删 | **人工复核后执行** |

---

## 6. 每一步需要注意什么（Checklist）

### 6.1 现场保全
- [ ] 不重启/不重装/不删 `.L0CK3D`/不清理磁盘
- [ ] 记录进程/内存状态（若进程活着立即 `kill -STOP`）
- [ ] 记录卷布局与共享 CoW/NoCOW 属性

### 6.2 只读验证
- [ ] 所有读取命令只读（`O_NOATIME` 更佳）
- [ ] 不在源卷写任何文件（包括临时文件）
- [ ] 不运行 `btrfs balance/scrub/defrag/check --repair/fsck`

### 6.3 抽样验证
- [ ] 先抽 10~30 个文件
- [ ] 校验文件头/结构 + 长度
- [ ] 成功率达标才全量（≥70%）

### 6.4 全量执行
- [ ] 产物写目标端（非源卷）
- [ ] 大文件分块流式（避免内存溢出）
- [ ] 记录失败清单（路径+原因）

### 6.5 校验
- [ ] 逐文件结构/解码校验
- [ ] 关键文件可加 Btrfs csum 校验
- [ ] 生成 SHA256 清单供复验

### 6.6 报告
- [ ] 统计口径写清楚（是否含回收站/镜像层）
- [ ] 区分"已验证/部分恢复/不可恢复"
- [ ] 给出后续建议（客户端/备份找回）

---

## 7. 中文表达规范（对外报告/开源文档）

### 7.1 术语中英对照（首次出现写"中文（English）"）

| 中文 | English |
|---|---|
| 写时复制 | CoW（copy-on-write） |
| 数据块 | extent |
| 校验和 | checksum（CRC32C） |
| 空闲空间雕刻 | free-space carving |
| 分段加密 | partial encryption（0x25/0x26） |
| 整文件加密 | full encryption（0x24） |
| 只读 | read-only |

### 7.2 用语原则

1. **结论先行**：先给结果（恢复了多少），再给依据；
2. **口径透明**：统计注明是否含回收站、镜像层、@synologydrive 等；
3. **不夸大**：用"已验证/部分恢复/不可恢复"三档，不用"百分之百恢复"；
4. **区分事实与推测**：推测性结论标注"可能/推测"；
5. **可追溯**：数字给出来源（脚本/清单/统计文件）。

### 7.3 避免的表述

| 避免 | 建议 |
|---|---|
| "保证恢复 100%" | "抽样 87% 结构正确，可恢复 X 个" |
| "完全修复" | "部分恢复，头部/尾部损坏" |
| "已全部恢复" | "已恢复 X 个，另有 Y 个不可恢复" |

---

## 8. 参考文献

1. **soimy/c3rb3r-rescue** — C3RB3R 格式分析、内存取证、`decrypt_copy.py`（进程存活时的最优路线）
   https://github.com/soimy/c3rb3r-rescue
2. **soimy/c3rb3r-rescue issue #12** — 无密钥独立案例：Btrfs CoW 旧 extent 恢复（0x25/0x26）
   https://github.com/soimy/c3rb3r-rescue/issues/12
3. **sunkangbo121-ship-it/c3rb3r-btrfs-extent-rescue** — 精确 EXTENT_DATA 拼接 + 工具（本书案例思路来源）
   https://github.com/sunkangbo121-ship-it/c3rb3r-btrfs-extent-rescue
4. **crt106/c3rb3r-btrfs-recovery** — AI Skill 化流程（preflight/gens/survey/extract/patch/**csum 校验**/报告），本仓库借鉴其 csum 校验
   https://github.com/crt106/c3rb3r-btrfs-recovery
5. **Btrfs 文档**（kernel.org）— EXTENT_DATA、chunk 树、csum 树、CoW
   https://btrfs.readthedocs.io/
6. **C3RB3R 家族公开分析**（Conti v3 变种、`.L0CK3D` 行为）
   （可引用各安全厂商公开报告，请以原文为准）

> **注意**：参考实现可能与你的样本版本不同（footer magic、pct 表可能不同）；使用前先核对格式（本仓库 `02_forensics.md`）。

---

## 9. 贡献与安全声明

- **贡献**：欢迎提交方法改进/脚本修复/脱敏案例；请先在合成样本或脱敏数据上验证，不要提交真实客户数据、密钥、IP、账号。
- **安全披露**：本仓库不含勒索样本、密钥、真实镜像；若发现安全问题请按 `SECURITY.md` 私下报告。
- **合规**：仅用于你**有权处理**的数据恢复与取证；请遵守当地法律与单位授权。
