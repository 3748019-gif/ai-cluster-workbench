# 每次运行配置入口

v2.4新任务以assignment_v1按任务配对执行/复核实例；roles保留为兼容平台权限和额度接口，不固定哪家生产或复核。旧run-profile.json仍可读取既有配置，但不代表新动态任务默认。详见dynamic-assignment.md。

每次使用先把 `templates/run-profile-dynamic.json` 复制到工具包外，编辑副本，再校验并初始化或导入。冻结包内模板和清单保持原字节；不能因为用户编辑冻结文件而自动重建清单放行。配置及活动指针保存在实例 `inputs/run_profiles/`，本次配置完整冻结在新 Task 的 `spec.run_profile_snapshot`；原 Task 的角色、有效策略、模型观察、历史记录不改。

```sh
cp templates/run-profile-dynamic.json /absolute/run-profile.json
# 编辑包外的 /absolute/run-profile.json 后再执行以下命令。
python3 scripts/toolkit.py profile-validate --config /absolute/run-profile.json
python3 scripts/toolkit.py init --workspace /absolute/new-instance --authority '用户本轮启动授权' --config /absolute/run-profile.json
python3 scripts/toolkit.py profile-show --workspace /absolute/new-instance
python3 scripts/toolkit.py import-business --workspace /absolute/new-instance --file /absolute/business.zip
python3 scripts/toolkit.py create-task --workspace /absolute/new-instance --file /absolute/task-spec.json
python3 scripts/toolkit.py ctl --workspace /absolute/new-instance -- prepare TASK-ID
```

已有实例的新一轮使用 `profile-import --workspace ABS --config JSON`，必须换唯一 `run_id`，不能使用 active 或以 .bindings 结尾的保留名称。同 ID 内容不能更改，旧 ID 不能重新激活。已建旧 Task 继续自身冻结策略。profile存在时禁止从 `ctl create/new-work` 绕过冻结、调度计数和前轮校验；新任务、关联子工作单元和接续轮次均由 `create-task` 创建。Task spec 明确填写 parent_task_id、work_kind、work_item_id、round_id、previous_round_id、input_artifact_ids 等适用字段；接续必须有新的目标和原因。错误父任务、工作项、轮次以及未确认或仍在执行的前轮会被拒绝，派发前再次检查。真正独立工作项可以并行；不得换Task ID伪装成独立工作绕过保护。旧 `ctl create/new-work` 仅适用于没有active profile的实例，不用于本启动流程。

固定 role 名映射到现有 `roles.json` 成员；替换须先走已有 register-member/能力pilot入口，未经核验成员不会借配置自动放行。模型/推理/速度是 requested，实际模型保持 unknown，配置不操作客户端。Sol Ultra、Astra Ultra/xhigh 会映射现有 controller_model_preset；Astra high 可在 requested 中选择并完整冻结在 requested_member_settings 与 run_profile_snapshot，当前内核没有其 preset，故内核 controller_model 的旧默认不能当成该请求的落实证据。实际切换仍需当前用户授权和客户端核实。

`max_calls:null` 为开放，`0` 禁止对应成员新的提交。本轮外部计数跨所有具有同 run_id 的 Task，持久 started 未确认也占位，明确 not_submitted 且未 confirmed 才释放；confirmed 后 cancelled 仍占位。重复 prepare 不消耗次数。派发 guard 与核心命令在实例锁内执行，避免并发 begin 越过上限。控制器原有每phase/工作单元限制同时生效。

Codex 的 max_calls 只控制此入口创建 Task 的数量，不是内部模型调用次数；Token、真实模型调用数为 null，禁止冒称已精确控制。Codex 原生额度预算目前没有可靠程序硬限适配，有限预算会明确拒绝；当前对话应核实 Usage 并按用户要求停止/调整。直接绕过启动器执行 `python -m cluster` 不属于这些附加入口门禁的保证范围。

外部预算支持 `max_observed_spend`、`minimum_remaining`、`baseline`、`unit`。设有限额必须提供实际原生单位及观察基线，prepare前仍须记录真实余额；已有派发后要有比该次派发更新的同单位余额观察，否则新派发被阻断。该预算是账号余额差的派发间停止线，不能保证单次费用、Token或后台真实检索次数。

百分比入口为 `budget.max_percent` 与 `budget.quota_total`：例如总月额度5000、基线剩余3400、max_percent=10，会转换原生 max_observed_spend=500。分母必须是已观察到的总额度，缺失拒绝导入；不能把余额3400当成总额度，也不能把账号窗口百分比与积分混算。Kimi quota_period 固定 monthly，重启/任务结束不清零月使用量。例：仅观察账号“剩余85%”且明确该窗口总值100，可用 percentage_point、quota_total=100、baseline=85，10%转换为10个百分点。

fallback默认false；授权为true时须明确授权来源、触发条件与目标requested。仅保存操作计划，不自动切模型、充值、升级或启用付费。真实额度耗尽后的fallback也不扩展付费权限。

本地测试入口：`python3 -B -S scripts/toolkit.py self-test`、`python3 -B -S scripts/test_launcher.py`、`python3 -B -S scripts/test_run_profile.py`及`python3 -B -S scripts/test_launcher_closeout.py`。自检可从工具包根目录执行，也可从任意外部目录用脚本绝对路径执行，无需用户设置PYTHONPATH。本次分发验证见仓库根目录docs/验证结果.md；历史原始日志仅保留在维护者本地。全部为本地模拟，不能替代真实三成员GUI验收。

接续spec在完整业务字段之外，使用 `parent_task_id`、`previous_round_id` 指向准确前轮，并明确 `new_work: true` 与非空 `followup_reason`（写出新增查询、证据或修订目标）。`work_item_id`和`round_id`可由前轮准确继承；显式提供时必须一致且连续。`create-task`的返回Task ID位于 `task.task_id`。

## v2.2 可选任务默认分流

工具包外的运行配置副本可添加顶层 `default_task_route`，允许 null、file_processing、open_research、mixed。没有此字段时旧配置及其哈希兼容。任务JSON可显式覆盖；mixed须在任务中给route_reason。此项只冻结路由选择，不修改角色成员、模型请求、额度或调用许可。open_research默认独立复核，显式review_required=false依旧受风险与semantic_judgment约束。重要语义判断不可凭file_processing绕过独立核验。
