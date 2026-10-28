# 统一底稿、复核周期与原生文档

本版本只对明确启用的新任务生效；旧任务按旧快照恢复。完整字段见 [底稿契约](canonical.md) 与 [复核契约](review-cycle.md)。所有命令沿用 `scripts/toolkit.py ctl --workspace ABS_WORKSPACE -- COMMAND`；业务任务通过 `create-task`，不绕开 active profile。

## 配置与身份

新动态任务先复制 `templates/run-profile-dynamic.json` 到包外；需要统一底稿与原生文档时，将旧兼容模板 `templates/run-profile-canonical.json` 的 `workflow_v1` 字段合入该外部副本，保留完整assignment_v1候选与当前模型、推理、速度及费用要求，再校验导入。两个冻结模板均不编辑。`workflow_v1` 严格识别 `canonical_required`、`native_documents_required`、`automatic_review_rounds:2` 和 `hard_timeout_minutes:null`；未知字段拒绝。此版本不启用硬超时。角色配置可显式设置`network_access:offline`限定本轮离线核验；省略时保留原公开联网权限，不通过Task字段绕过运行配置。

Task声明 `review_cycle_contract` 的稳定业务单元、范围及条目ID，必要的源文件在登记后以 `review-inputs --file ABS_JSON` 冻结；更改任务号、父任务、文件名、会话或缩范围都保留原周期。真正新增范围需另有明确授权证据，原问题和轮次保留。业务两轮上限与供应商调用、搜索和预算上限分开计量，Codex调度与最终验收不变，执行者和独立复核者由新任务assignment_v1配对，旧任务保持旧快照。

## 从材料到送审

1. 登记原件，`canonical-inventory`保存已读范围和每个具名条目的去向；待核可以登记，不能冒充完整。
2. `canonical-freeze`保存事实候选，`canonical-views`使用`canonical_table_v1`生成同底稿两视图；这一步不代表内容已接受。
3. 读取`canonical-candidate TASK`返回的JSON，保存为本地候选并通过正常`local-output`登记。该候选含完整条目、共享组、两视图和真实哈希。自行修改一版的内容会被送审/接受门禁拒绝。
4. `review-inputs`列全必需原件及读取权限；`package --phase reviewer`和`prepare --phase reviewer`生成自包含契约。首轮全范围，次轮变化及依赖；实际包中不得排除必需原件。按既有GUI_LOCK、preflight→begin→单次发送→confirm执行。
5. `capture/ingest`保留原始回包和最早完成观察。控制器可本地修正格式，真实消息如实计数。原报告的CE/blocking不改，局部结果和有效问题另存。`review-cycle`和`review-packet`可查看周期/中性变化信息；包内不能包含私有评分答案。

`review-adjudicate --file ABS_JSON`仅支持精确候选位置的客观误报，要求原报告、候选哈希及原句对照；不能据此确认其他未知事实。`review-authorize`必须登记新增实质轮次的人类授权证据。两轮后仍有真阻塞时保存待决请求，不自动第三轮；未受依赖影响的单元可以继续。底层unknown仍为unknown，漏读、冲突和已确认错误不能改名为限制披露。

## 定稿与文档阶段

`content-freeze`要求当前底稿和两视图、精确候选、完整独立核验和有效问题均满足条件。此前`document-export`仅允许一次`template_trial`试排，不能循环全套正式导出。

正式`document-export`请求字段为`key`、`view:full|simple`、`purpose:final`、`title`。控制器不自动操作办公软件。可用`cluster.document_stage.write_view_docx`生成可编辑表格模板，然后由Codex操作现有Word/WPS保存并导出对应PDF。`document-register`登记`request_id`和当前`docx_path`；文件必须位于本Task的exchange目录，正文逐格匹配同源视图，保留横向、重复表头及Normal标题无边框。

`document-qa`字段：`key/application/actor/kind/docx_sha256/pdf_path/native_evidence/pages`。只有Codex可登记原生观察；live需`kind:observed_gui`，test只能`simulated`。原生观察记录绑定当前DOCX/PDF哈希、应用、时间和真实UI文字；每个最终页有单独观察证据和字体、列宽、截断、空页、页码、脚注、组边界检查。结构或脚本生成空白PDF不是视觉验收。缺GUI如实列未验。

改一份列宽时，当前候选内容与同源视图保持不变；重新导出、登记、原生检查该文件全部页面即可。其他已验文件记录可复用。隐藏文字、脚注、合并关系或共享额度改变会转回语义分支。更新后使用原`freeze-files`、`validate`、`finalize`、`release-register`、`release-bind`、`deliver`和`audit`精确绑定当前字节；原件及旧报告、旧失败不覆盖。

## 可观测边界

轮次、发送意图、确认消息、格式适配、待用户、导出请求和成功有独立记录。供应商token、搜索次数、任务准确计费或人工修改量无法观察时留null，不承诺提速或省额比例。工具包提供控制器和证据流程，不包含客户端，也不会自动充值、换模型、安装依赖或强制退出应用。
