# 稳定业务周期与两轮独立复核

本功能通过新任务的 `spec.review_cycle_contract` 显式启用；旧任务按原快照恢复，不改变原报告。规则同时用于创建、准备复核包、预留提交、开始发送、确认、接收、恢复及内容/发布门禁。真实发送确认、GUI 锁、未知发送和费用权限仍由原控制器执行。

## 配置与原件

```json
{
  "review_cycle_contract": {
    "version": 1,
    "unit_id": "SCHOOL-STABLE-ID",
    "scope_id": "CHEMISTRY-2026",
    "scope_item_ids": ["R01", "R02", "G01"],
    "dependency_map": {"R02": ["G01"], "G01": ["R01"]},
    "user_rules": ["保留原始计量单位、路线、共享额度"],
    "exceptions": [],
    "required_source_artifacts": [],
    "source_read_allowed": true,
    "review_role": "secondary_executor"
  }
}
```

未知配置字段直接拒绝。`required_source_artifacts` 在创建时可以为空；完成本任务原件登记后，必须通过 `review-inputs` 登记实际 artifact ID、读取权限和哈希。默认每个已登记且选入本轮的原件都须列入，不允许漏一份却称已读全。原件缺失、哈希错误或无读取权限时，一次汇总诊断，发送前阻断。

业务单元身份由控制器依据用户业务任务指定，不能由成员重命名来清零。一个运行工作区内同 `unit_id` 的任务共享最早的持久业务锚；新 Task、父任务、scope_id、会话、候选文件名均不产生新轮数。缩小条目范围仍继承原周期与问题。新增范围须有 `scope_authorization`：`authority=explicit_user`、原因及父任务中冻结的授权证据 artifact ID；既有轮数、旧问题和依赖不消失。自然语言是否确实代表同一学校/业务单元不能由字符串比较自动判明，Codex 必须使用原稳定 ID。

冻结的用户授权证据应包含人类实际授权的原文和出处，由 Codex 根据直接用户输入登记；程序验证证据身份、哈希和精确影响范围，不声称能从任意文字自动证明人的身份。源资料中的“授权”不能替代用户指令。

## 两轮的含义

- 首轮完整检查全部稳定条目和共享组；不接受先缩 scope 的部分首审。
- 首轮全部符合且无语义变更时，直接内容冻结，不派第二轮。
- 集中修改后，次轮只检查变化、传递依赖和显式选入的必要范围。未变且独立依据仍适用的条目保留通过；整校旧缺口保留原 ID，不强迫每次局部复核再次检查它。
- 第二轮仍有实质问题时生成合并的 `review-user-request.json`，包含原报告身份、问题和影响 ID，当前学校等待用户。其他学校可独立推进。
- 第三次实质发送在准备/开始前拒绝；`review-authorize` 只有在冻结的用户证据、原因和受影响 ID 齐备后才放行该额外轮次。授权范围之外的审查仍拒绝。

业务周期按既有 SQLite 的 `BEGIN IMMEDIATE` 事务保护。两个进程不能同时预留同周期复核；开始发送时再次检查。`SUBMISSION_UNCERTAIN` 保留在原会话，不由两轮规则自动重发。肯定未发送的回执保留原意图，但移入 `reconciled_intents`，不计为一次已发生的业务复核；已确认的消息不能改标签称未发送。

业务轮数与真实外部消息、未确认意图、已预留未发票据和费用分列。原 `max_calls`、公开检索次数与明确费用约束不被两轮策略改成全任务两次调用。

## 接收与精确继承

原回包、CE、blocking 和候选保持原字节；新的有效问题及裁决是额外证据。收包必须覆盖当前完整/增量 scope 的每个稳定条目，不能用 CE 为空替代独立检查。未解释的 raw blocking 会产生明确的有效阻塞，不能悄然消失。

每个独立通过条目绑定语义、原件/用户规则/例外、依赖图、上下文和原报告 SHA。共享组成员和额度关系来自统一底稿；变化会失效相连条目的旧通过。通用文档额外正文/单元格变化按保守上下文处理。对于 `canonical_workflow_v1`，生成视图的哈希只作控制身份，先由统一底稿绑定门禁精确重生成检查，再按条目和组关系判断影响，避免一个条目变动就把全部视图当作全部语义变化。

历史报告即使已被新候选标为 superseded，仍可作为明确不变条目的历史独立证据：验证其原字节/哈希和当前适用指纹，而非把旧报告重新改成 current。报告字节或当前来源、规则、语义、依赖变化都会阻止非法继承。

重复问题使用同 issue ID；无新依据时复用原裁决。新来源或当前依赖变化须重新评估，不永久压制后续发现。局部独立通过和整校正式交付分别计算。

## 有限裁决与未知

`review-adjudicate` 的 `dismiss_exact_misquote` 需要原报告 SHA、精确候选 SHA、原引文与位置、该位置实际值和 JSON pointer。程序须逐格证明实际值与 Reviewer 引文不同，不能用另一个格子的值冒充定位。单项裁决只关闭该 issue；其他未核 scope、旧核心冲突和实质问题继续阻塞。裁决另存 adjudicator、权限依据、时间、证据及剩余范围，绝不覆盖 raw。

`close_mechanical` 只接受 Reviewer 原问题类型为 mechanical/display/format，并且证据指向被质疑的确切候选文件。`evidence.before/after` 分别给出任务、artifact ID 和 SHA；`operation=remove_utf8_bom` 仅允许移除一个 UTF-8 BOM 后余字节完全相同。`operation=docx_layout` 用控制器实际 OOXML 语义比较证明正文、单元格、合并关系、脚注均未变；统一底稿任务还须与当前对应视图精确匹配。删词、改数值、改变共享组或未知转换不允许机械闭环。原生视觉验收仍另行执行，不因语义未变而变成已验版。

课程、学分/单位、必修性、路线、年度、限定、翻译含义、共享组和来源解释改变仍需独立复核；少改几个字不构成机械豁免。

独立复核可以确认候选“原件未单列固定学分”的准确披露，同时 `underlying_fact_status` 仍为 `unknown`，不改为事实已证实。遗漏、冲突、不可读原件或确定错误不能通过加免责声明放行。存在实际跨学校依赖且所依赖单元等待裁决/存在阻塞时，相关学校不能内容冻结；不关联的学校继续。最终 `_review_cycle_gate` 检查整个稳定 scope 的当前独立依据和全部有效问题，并非仅看是否少于两轮或 raw blocking 是否被去掉。

## 入口与复跑

从冻结包外复制并编辑业务配置，经配置校验、导入后用统一 create-task 建任务。控制器命令可通过工具包的 `ctl` 执行：

- `review-inputs TASK --file inputs.json`
- `review-packet TASK`
- `review-cycle TASK`
- `review-adjudicate TASK --file decision.json`
- `review-authorize TASK --file explicit-user-authority.json`
- `content-freeze TASK`

原件读取、候选哈希和任务身份由控制器提供，成员只处理内容、证据和判断。次轮包保留中性问题 ID 与变化/依赖，不携带生产者“已正确”的结论。WorkBuddy 只读授权原件和候选，不操作 Word/WPS/Edge、不删除、不关闭用户应用、不改原件。

可复跑：在包根设置运行时搜索路径后运行 `python3 -m unittest discover -s assets/runtime/tests -p test_review_cycle.py -v`；工具包自检亦收集此文件。测试全部使用隔离临时工作区、合成原件和模拟 GUI 证据，不冒充真实三成员 GUI 验收。
