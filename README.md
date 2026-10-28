# AI Cluster Workbench

由Codex调度桌面AI客户端的工作集群：按任务选择执行者与独立复核者，记录证据、修订和最终交付。适合研究整理、报告制作、文件加工，以及需要独立复核的任务。

这是一个技能包和本地控制器，不是新的模型服务。客户端通过当前环境获准的Computer Use操作，不直连供应商后台API。本仓库按开源发布标准整理，已获用户授权作为独立的Public仓库发布；实际可见性以GitHub标识为准。

## 如何分工

| 工作 | 处理方式 |
| --- | --- |
| 拆解、分配、最终验收 | Codex |
| 确定性文件加工、计数、格式与哈希 | Codex和本地程序优先 |
| 研究、整合、成稿、实质修订 | 按当次能力、材料、权限和额度选择已授权成员 |
| 独立复核 | 派发前与执行者配对，排除作者及共享会话 |

默认候选包括WorkBuddy、Kimi和符合隔离条件的Codex实例。两家外部客户端不固定角色；用户明确指定优先，不默认全员同题竞争。可逐次设置成员、模型请求、调用次数、预算及允许的回退；这些配置不会自动切换客户端模型。

## 开始使用

先完整阅读[启动入口](ai-cluster-mode/START_HERE.md)与[SKILL.md](ai-cluster-mode/SKILL.md)，另行提供业务任务包。

在仓库根目录运行：

```sh
python3 -B -S ai-cluster-mode/scripts/toolkit.py verify
python3 -B -S ai-cluster-mode/scripts/toolkit.py self-test
```

将ai-cluster-mode/templates/run-profile-dynamic.json复制到本仓库以外，按本次要求编辑副本，然后校验：

```sh
python3 -B -S ai-cluster-mode/scripts/toolkit.py profile-validate --config /absolute/path/run-profile.json
```

候选初始为unverified；不能仅将配置文字改成available就声称客户端已验证。实际GUI、权限、材料通道、模型和额度需观察后记录。后续初始化、plan-task、create-task及恢复步骤见[动态分配](ai-cluster-mode/references/dynamic-assignment.md)和[运行配置](ai-cluster-mode/references/run-profile.md)。

在新Codex对话中可直接发送：

```text
请读取本仓库ai-cluster-mode/START_HERE.md和SKILL.md，先核验完整性，再读取我另行提供的业务任务包。Codex统一调度，按当次能力、权限、额度和独立性配对执行者与复核者；保留我当前模型、推理及速度设置。将运行配置和业务实例放在工具包外；未知发送先核查，不直接重发。附件由Codex操作本机Edge下载。没有业务任务时只完成就绪检查，不调用外援。
```

## 运行条件与边界

- 核心需要Python3.9+及POSIX环境；本次在Mac验证，其他系统未全面验收。
- GUI操作需要已安装、已登录的客户端，以及当前Codex环境可用的Computer Use权限；本仓库不提供账号、额度或权限。
- PDF及原生文档功能有额外依赖和验版要求，不自动安装；Word/WPS验版与结构检查分开。
- 保留发送确认、作者继承、同范围两轮收束、原件与失败记录、精确发布绑定。
- 不预设提速或节省比例；分配依据已观察资格和已验收记录，不保证自动找到最强或最便宜模型。

## 仓库内容

- ai-cluster-mode/：可导入的技能、便携控制器、静态模板、测试及完整性清单。
- docs/：发布变更、隐私边界与本次验证摘要。
- REPOSITORY_MANIFEST.json：本次发布文件的路径、大小和SHA-256。

业务数据、账号信息、私有知识库、历史运行日志及原始验收记录不随仓库分发。运行配置和工作实例应放在仓库之外。文件清单与.gitignore是辅助检查，不能代替GitHub实际的仓库可见性设置。

版本：运行时代码基线v2.4；仓库分发版v2.4-repository.1。本项目代码与随附文档采用[MIT许可证](LICENSE)。技能目录内保留相同的[LICENSE](ai-cluster-mode/LICENSE)，单独复制技能包时也应一并保留。此许可不为外部客户端、模型服务或另行提供的业务资料授予权利。

详见[发布说明](docs/发布说明.md)、[隐私与发布边界](docs/隐私与发布边界.md)和[验证结果](docs/验证结果.md)。
