# 动态执行与独立复核

v2.4只为新任务启用assignment_v1；旧profile和Task保持原快照。先把templates/run-profile-dynamic.json复制到包外，依据当前GUI/本地实例实证填候选，不能将占位ID当作已观察会话。平台身份与候选ID分开：Task.producer/reviewer为候选实例ID，member_profiles[ID].platform和assignment记录实际WorkBuddy/Kimi/Codex等平台。客户端模型只记可见标签；requested不代表切换成功，后台实际路由未知。

候选记录当前授权、稳定actor_id和可追踪session_id、可用性、能力(已观察/小样本/未核实)、输入与回传通道、实际模型观察和原生额度。GUI会话ID可以是控制器赋予且有空会话观察证明的标识，不能假冒供应商后台UUID。outputs_writable指本次可用的交付通道（文件或完整GUI响应），不证明可写整个磁盘。不同模型/会话不会抹去旧作者；当前实现保守继承整个稳定业务单元作者集合，未宣称自动逐句语义依赖图。

选择依次检查显式指定/禁止、权限与实际材料通道、可用性及预算、独立性、同类已交付记录，再按简单偏好排序；费用/性能未知保持null。默认一个执行者，不同题竞争。same actor或同平台同session不独立；同平台分离会话需要新上下文证据、共享生产上下文被排除，模型区别只由实际标签证明。无法排除时暂停这项。最终主控验收不等于独立复核。Codex实质改稿会登记作者，纯调度不登记成所有正文作者。

运行示例（从工具包根目录；准确绝对路径由当前任务决定）：

```sh
python3 scripts/toolkit.py verify
python3 scripts/toolkit.py profile-validate --config /absolute/external-run-profile.json
python3 scripts/toolkit.py init --workspace /absolute/business --authority "本次实际授权" --config /absolute/external-run-profile.json
python3 scripts/toolkit.py plan-task --workspace /absolute/business --file /absolute/task.json
python3 scripts/toolkit.py create-task --workspace /absolute/business --file /absolute/task.json
python3 scripts/toolkit.py ctl --workspace /absolute/business -- assignment-handoff TASK-ID --phase producer
```

Task例：assignment_request.executor="wb_current"、reviewer="codex_isolated"；或者用assigned_provider="codex"、reviewer_provider="workbuddy"保留明确指定，两套指定冲突会拒绝。forbidden_members=["kimi"]只约束该任务。required_capabilities为本项所需能力，required_inputs列必要逻辑文件名；output_contract.producer/reviewer逐字段写完整定义。字段遗漏一次汇总，身份/来源ID/哈希/路径由控制器绑定，不补造scope或读取事实。

本地简单处理用task_route=file_processing，若无实质语义可review_required=false；涉及重要事实则保留review_cycle_contract。预算角色字段仍按所绑定平台计量；动态实例别名及模型切换不清零同授权的平台调用/未结算。追加平台用现有register-member明确政策、当前用户授权和app_name，先监督capability_pilot；它不安装客户端或购买服务。注册后用profile-validate --workspace验证其运行配置。日常公开检索默认开放，过去某轮Kimi0或500–800不是本轮默认上限。

续作先recover和assignment-status，原Task有在途/未知发送时不得换模型、Task、profile或工作区重发。合法接续用create-task的previous_round_id、parent_task_id、work_item_id和新目标，共享原稳定review_cycle_contract，保留两轮计数。动态历史不能通过旧入口删掉assignment/review契约。跨实例没有自动全局锁或隐藏任务发现；主控必须续用既有业务实例，不得用新的空工作区掩盖原在途记录。

source_item_map可显式按必要原件逻辑名声明稳定条目适用范围；没声明则保守影响全scope。映射变化也重算相关条目的来源依据，依赖沿既有review_cycle传播。这只是已声明范围的精确版本比较，不能自动证明自然语言原件无遗漏，需独立核验。statement_projections仅允许顶层字段与指定statement JSON指针深度完全相等时去重哈希，禁止让指标或正文在statement之外漂移。

同应用GUI串行。handoff_mode=file使用只读任务包短路径；attached_file由Codex把该准确任务包附到客户端，短消息只引用同名附件，回完整JSON，由主控保存。附件包只含本项必要原件和精确候选，禁止上传整个优化包、私有答案、未发布变更或其他原审结论。

isolated-review-prepare/begin/confirm/capture用于当前支持fork_turns=none的真实Codex隔离实例：工具派发前保存intent，工具回执确认后confirm，真实输出文件/哈希与完成回执capture，再普通ingest；不走GUI伪证。未收到派发确认则仍是SUBMISSION_UNCERTAIN，不自动重试。它只证明流程与上下文隔离，不证明后端模型或操作系统沙箱。内部实例与外部GUI调用分别计数；模型token/精确费用仍未知。

两轮、原件留存、统一底稿、内容冻结后的原生Word/WPS验版与精确release-bind保持既有门禁。本轮若未变动DOCX代码，不为展示路由重导十校文档；已有GUI验证仅标历史，不算本轮新验。

合法接续会自动冻结前轮精确业务候选作为previous_candidate修订证据，并纳入包的哈希与父版本依赖。它不进入原始来源清单，不把作者稿当官方依据，也不向独立Reviewer传递旧通过结论。生成时间、已观察成员/会话等已知传输字段只在符合精确身份或明确非权威过程标签时移到入库证据，原字节保留；声明为业务字段或无法识别的字段仍保守参与内容变化检查，不自动丢弃适用范围。

已准备但从未begin的票据可用cancel-prepared TASK --submission SUB --reason REASON释放本地保留，原失败与字节保留；有started_at/confirmed_at或SUBMISSION_UNCERTAIN不能这样取消，须先真实调和。inputs.saved_byte_identity_chain只证明已登记导入链的同字节身份，不证明当前适用性；response_schema明确标准业务字段类型与控制器可绑定的身份字段。
