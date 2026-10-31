# 贡献指南 / Contributing

感谢你愿意改进本仓库。请遵循以下约定：

## 可以贡献什么

- 恢复方法改进、脚本修复、性能优化；
- 新的有效案例（**必须脱敏**：无真实 IP/账号/密码/主机名/组织名/文件名）；
- 文档修正（错别字、表述、翻译）。

## 禁止提交什么

- 任何真实客户数据、文件内容、磁盘镜像、勒索样本、密钥；
- 任何未脱敏的截图/日志/路径；
- 任何可能诱导破坏源盘的脚本（写操作、`btrfs balance/scrub/defrag`、`dd` 写入等）。

## 流程

1. Fork 本仓库并新建分支（`feature/xxx` 或 `fix/xxx`）；
2. 修改后运行自检：
   ```bash
   # Python 语法
   find toolkit -name '*.py' -exec python -m py_compile {} \;
   # JSON 合法
   python -c "import json,glob; [json.load(open(f,encoding='utf-8')) for f in glob.glob('**/*.json',recursive=True)]"
   # 脱敏自查（不要出现真实 IP/账号/密码/主机名）
   grep -rEn '192\.168\.[0-9]+\.[0-9]+|password\s*=\s*[^"]' --include='*.md' --include='*.py' . || true
   ```
3. 提交 PR，说明：改了什么、为什么、如何验证（附脱敏后的证据）。

## 代码风格

- Python 3.8+ 标准库优先（Pillow/zipfile 作为可选依赖）；
- 脚本文件头写清：用途、运行位置、参数、只读保证、脱敏说明；
- 只读保证：打开设备用 `O_RDONLY`，不写源盘。
