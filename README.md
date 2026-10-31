# C3RB3R（.L0CK3D）勒索事件 · Btrfs 数据恢复复盘案例

**语言**：[简体中文](README.md) | [English](README_EN.md)

> 🚀 **GitHub 开源使用指引见 [GITHUB_GUIDE.md](GITHUB_GUIDE.md)**：怎么开始、如何把本仓库作为 AI 技能引入、工具/模型选择、每一步注意事项、中文表达规范、参考文献。

> 本文件夹是一次真实勒索事件（Synology DSM + Btrfs 卷，C3RB3R 家族，`.L0CK3D` 后缀）的**脱敏复盘**，供其他运维/安全/数据恢复人员参考，并配可直接复用的脚本工具包。
>
> 所有 IP、账号、密码、主机名、组织与个人名称均已替换为占位符；统计数字与工具保持原始结构。

---

## 一、案例速览

| 项目 | 内容 |
|---|---|
| 系统 | Synology DSM 7.x（Btrfs 卷，含一个 `+C`/NoCOW 卷） |
| 勒索家族 | C3RB3R（`.L0CK3D` 扩展名、`read-me3.txt` 勒索信） |
| 加密情况 | 全盘业务数据 **1,295,238 个文件 / 2,981 GB** 被加密（排除 @docker 镜像层重复副本与回收站） |
| 加密后状态 | 加密进程已退出（无法从内存提取密钥），系统未重启 |
| 恢复窗口 | 攻击结束后 1~2 天才开始恢复（现场未再写入） |
| **最终恢复** | **73,809 个文件 / 约 351.7 GB**（交付目录，约 349 GB 实占） |
| 文件数恢复率 | **9.42%**（不含 @synologydrive；含则 7.88%） |
| 字节恢复率 | **21.37%**（不含 @synologydrive；含则 13.70%） |
| 核心方法 | CoW 旧 extent 恢复（delta 法 + 精确 EXTENT_DATA 法）、空闲空间雕刻、Btrfs csum 校验 |

## 二、目录结构

```
c3rb3r_case_report/
├── README.md                 ← 本文件（总览、快速导航）
├── README_EN.md              ← 英文总览
├── GITHUB_GUIDE.md           ← GitHub 开源使用指引（快速开始/技能引入/工具与AI选择/注意事项）
├── AGENT_RUNBOOK.md          ← 给 AI/自动化执行的作业手册（阶段机、判据、失败分支）
├── case_summary.json         ← 机器可读摘要（时间线、统计、方法、工具）
├── manifest.json             ← 文档与工具清单（AI 读取入口）
├── 01_incident.md            ← 事件经过与时间线
├── 02_forensics.md           ← 现场取证与原理（C3RB3R 格式、Btrfs CoW/NoCOW）
├── 03_recovery_methods.md    ← 五类恢复方法与实测效果
├── 04_lessons.md             ← 经验教训、失败边界、决策表、常见坑
├── 05_report_template.md     ← 恢复报告模板（可直接套用）
├── SECURITY.md / LICENSE / CONTRIBUTING.md / CHANGELOG.md
├── examples/                 ← 示例配置与命令
├── .github/                  ← CI 自检、Issue/PR 模板
└── toolkit/                  ← 脱敏后的可复用脚本 + 工具清单
    ├── README.md
    ├── tools_manifest.json
    └── （脚本，见工具清单）
```

## 三、AI 读取建议（按顺序）

1. `manifest.json` — 了解本文件夹全部文件与工具的用途；
2. `AGENT_RUNBOOK.md` — 按阶段执行恢复作业（环境自检 → 普查 → 选方法 → 执行 → 校验 → 报告）；
3. `02_forensics.md` — 理解为什么这些方法有效/无效；
4. `03_recovery_methods.md` + `04_lessons.md` — 方法选择与边界；
5. `toolkit/tools_manifest.json` + `toolkit/README.md` — 具体脚本用法。

## 四、脱敏说明

- **已替换**：内网 IP → `192.168.1.10/192.168.1.20`；账号/密码 → 环境变量（`NAS_USER`/`NAS_PASSWORD`，脚本内不落明文）；主机名 → `<NAS_HOSTNAME>`；组织/人名 → `<ORG>`/`<PERSON>`；敏感共享目录 → `<SHARE>`。
- **保留**：泛化目录名（如 shared_A/shared_B）、统计数字、脚本结构、Btrfs/C3RB3R 技术细节（无敏感信息）。
- 工具包内任何连接参数均改为**环境变量或命令行参数**，使用前请自行设置。

## 五、重要边界（先读再动手）

1. **停止写入**是恢复成功的第一前提：不删除 `.L0CK3D`、不重装系统、不 `balance/scrub/defrag`、不在源卷上落地任何产物；
2. **无密钥时只有三条路**：① 从内存提取密钥（进程还活着时）；② Btrfs CoW 旧 extent 恢复（0x25/0x26）；③ 空闲空间雕刻（0x24 小文件中的图片/文档）；
3. **以下情况基本不可恢复**：`< 1MB` 整文件加密（0x24）且数据已被覆盖；`+C`（NoCOW）共享中"快照后写入"的数据；数据库文件；
4. 恢复结果必须**逐文件校验**（SHA/文件头/PIL/zipfile/ffmpeg，最高标准是 Btrfs CRC32C csum 校验），不可用"文件存在"代替"内容正确"。

> 本案例恢复率约 9.4%（文件数）/ 21.4%（字节），其中 1–128MB 档达到 32%（文件数）/ 39%（字节）——这是 Btrfs CoW 恢复的合理上限区间，请据此建立预期。
