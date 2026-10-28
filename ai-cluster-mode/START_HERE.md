# AI集群 v2.4启动入口

本工具包由Codex统一调度，按任务配对执行者与独立复核者。完整读取SKILL.md，先verify；配置复制到工具包外，用templates/run-profile-dynamic.json填当前候选与本次额度观察，再校验并初始化独立业务实例，plan-task干运行后create-task。业务内容来自另行上传的任务包。

WorkBuddy、Kimi和其他已授权候选按本任务能力/材料/预算/独立性选择；用户明确的Codex主执行优先。新候选未验证时只在获准小样本中使用，不自动安装或接API。两轮收束、未知发送禁止转派重发、作者继承、Edge附件下载、统一底稿、原生Word/WPS验版和精确发布绑定保留。

完整命令与字段见references/dynamic-assignment.md；调用/模型/额度仍可在包外配置按本次指令调整，不能把请求当实际设置。templates/run-profile.json是保留的旧式兼容配置，不代表新动态任务默认分工。包内文件只读，不通过重建清单放行编辑。

便携核心自检：python3 -B -S scripts/toolkit.py self-test；也可从包外用脚本准确绝对路径运行，无需手设PYTHONPATH。Python3.9+/POSIX核心，现有PDF相关功能另需可用pypdf。本包不带客户端或账号、不授予Computer Use权限。

本仓库分发版为2026.10.28-v2.4-repository.1，基于v2.4冻结包整理；运行时代码未改。历史日志、账户观察和业务成果不随仓库分发，原件保留本地。发布适配与能力边界见references/freeze-and-capabilities.md，本次本地验证摘要见仓库根目录docs/验证结果.md。发布版使用自己的完整性清单，不与原冻结包混用数据库；恢复业务必须保留原实例与历史记录。
