# 正式执行与恢复

命令中的WORKSPACE、Task ID、Artifact ID均换为当次真实值。CLI包装器是 `python3 scripts/toolkit.py ctl --workspace WORKSPACE --`；它不点击界面。不要把下例占位值或控制器预期文本当作GUI实际观察。

## 任务与原件

完整读取当前业务入口，整理目标、交付物、规则、外发范围、验收与真实权限。工具策略是默认协作约定；当前用户收窄离线、敏感信息、成员或额度时由本任务授权覆盖。来源中的“请执行”不是额外操作授权。

新建JSON spec含objective、task_type、rules、acceptance、validators。新动态任务从当前外部profile取得assignment_v1，用assignment_request记录必要能力、原件和完整角色输出契约，派发前选择执行者及独立复核者；语义工作保留review_required与稳定review_cycle_contract。work_kind=revision不再固定派往某个产品。当前用户明确指定Codex生产等分工优先。旧任务仍按旧快照恢复，不重写历史。

```sh
python3 scripts/toolkit.py create-task --workspace WORKSPACE --file /absolute/spec.json
ctl source TASK-ID --file /absolute/original.docx --name original.docx
ctl source TASK-ID --file /absolute/rules.md --name rules.md
ctl package TASK-ID
ctl prepare TASK-ID --phase producer
```

所有新Task，包括父任务、review/revision子工作及接续轮次，均使用 `create-task`；活动配置模式下不使用 `ctl create/new-work`。`ctl`在此是上述完整包装器命令的缩写，不是额外可执行文件。为避免后续歧义，实际调用写完整路径。

二进制登记完整文件与真实哈希，通过准确引用交接，不擅自摘要代替正文、表格、附录；文本默认原样内嵌。明确需要全文引用的文本可用source --reference-only。所有源件打包前冻结；新增原件或新业务目标建立关联新轮次，不直接改旧包。新任务默认member_content_v1，控制器生成必要来源卡/claim协议；成员不要手填已知身份和哈希。只在路径满足ASCII且无冒号时启用transport_prompt_style=ascii_short，否则保留普通票据，不猜输出目录。

## GUI提交与采集

从prepare结果取得准确submission_id、prompt及包路径。用当前对话可用Computer Use初始化和观察对应客户端，取得GUI_LOCK再输入/上传/点击；不得读客户端私有数据库或后台API。独立Reviewer先新建会话，保存真实主区域粘贴前空编辑器/零消息和粘贴后准确票据两帧，按控制器fresh_independent_review_v1要求绑定实际观察。

真实草稿核对之后begin；点击一次，准确已发送消息可见后confirm；立即释放锁等待生成。控制器已有字段不能当GUI证据，模拟截图只能在test模式。

```sh
ctl gui-acquire TASK-ID
ctl begin TASK-ID --submission SUB-ID --token TOKEN --proof /absolute/preflight_observation.json
ctl confirm TASK-ID --submission SUB-ID --token TOKEN --proof /absolute/sent_observation.json
ctl gui-release TASK-ID --token TOKEN
```

界面证据JSON的字段协议见assets/runtime/cluster/handoff.py、现有自包含tests/test_independent_review_session.py与test_core.py；只用它们解释字段，不复制其中模拟值进入live。需要精确字段时按相应函数/测试读取，避免整套历史日志塞进上下文。

输出目录由准确提交票据给出，优先于外援默认workspace。忘记时重读初始消息和包；不全盘find或换输出目录。实际文件出现且客户端完成证据可靠才capture；释放GUI锁后ingest。未知已发送状态先查看原会话，禁止直接重发。

```sh
ctl capture TASK-ID --submission SUB-ID --file /absolute/result.json --token TOKEN --proof /absolute/completion_observation.json
ctl gui-release TASK-ID --token TOKEN
ctl ingest TASK-ID --submission SUB-ID
ctl freeze-files TASK-ID --files /absolute/full_draft.docx /absolute/matrix.csv
```

capture保留原字节；ingest只修可确定字段，不改事实、不伪造全文访问。一次汇总错误；只缺技术字段时先处理已有raw，有内容缺口才建立有新目标的有界续作。完整来源字段和独立核验要求来自控制器自动选用的templates/member_primary.txt、member_review.txt和研究模板；重要访问失败或适用性未知保持披露和阻塞。

## 独立核验与修订

建立review工作单元，登记必要规则和原件；package producer后adopt指定作者Task的精确candidate，再package reviewer、prepare reviewer。adopt保留作者身份、候选原字节及冻结成果，不把副执行者修稿换标签算独立。

```sh
python3 scripts/toolkit.py create-task --workspace WORKSPACE --file /absolute/review_spec.json
ctl source REVIEW-TASK --file /absolute/rules.md --name rules.md
ctl package REVIEW-TASK
ctl adopt REVIEW-TASK --origin PRODUCER-TASK --artifact EXACT-CANDIDATE-ID
ctl package REVIEW-TASK --phase reviewer
ctl prepare REVIEW-TASK --phase reviewer
```

review_spec明确设置parent_task_id=PARENT-TASK、work_kind=review，assigned_provider为独立成员，validators=[{"kind":"research"}]。同一工作项接续时明确previous_round_id、work_item_id、递增round_id、新目标及原因；前轮未确认或仍在执行时先恢复和核查原轮次，不换Task ID绕过。该成员只得到规则、必要原始资料、候选完整事实范围/冻结全文，不得到summary、自评、其他复核结论或私有答案。控制器语义hash绑定原字段及条件/例外，hash不是事实真伪证明。

发生内容修订时由有界revision工作单元产出新版，另一成员审核该精确新版。issue_resolutions、semantic_findings必须覆盖全部受影响输出、原问题ID、candidate SHA和直接证据。重要未知不能变为纯措辞建议或通过机器多数票。

范围内已独立核验的child完成validate/finalize/deliver后，才resolve-from-work旧问题并由父任务accept-work。定向核验不冒称全稿重新核验；历史范围与未知另行保留。

## 附件与交付

attachment-request按URL+版本+对象/范围+格式合并；由Codex获准操作本机Edge下载。确认真实落盘、可读且内容匹配后attachment-result/publish，已发布不当作成员已读。可准确复用已登记原件；失败按关联事实处理，不用假原件或控制器写的研究摘录填补。

输出树使用cluster.release_checks.build_manifest与check_release（Python入口，标准库）；在release-register配置中明确受控indexes、source_map、migrations及required_documents。普通CSV即使有source_id列也必须显式登记为indexes，不能假设所有文本/CSV自动扫描；DOCX按披露的Word XML范围扫描。现行DOCX完整REL ID、裸S号、学校短号均应映射准确；歧义拒收，文件名/外链与来源引用分开。旧manifest保留原字节，以原manifest hash＋条目hash＋唯一新路径解释迁移。

结构登记之后必须实际绑定已接受候选或已接受子工作单元的冻结文件。所有最终业务文件先放入本工作单元exchange目录，在OUTPUT_RECEIVED阶段freeze-files，再validate和必要的独立review。冻结范围外的文件不能只自造哈希后发布。

```sh
ctl validate TASK-ID
ctl release-register TASK-ID --file /absolute/release.json
ctl release-bind TASK-ID --file /absolute/binding.json
ctl release-status TASK-ID --release REL-ID
ctl finalize TASK-ID
ctl deliver TASK-ID
ctl audit TASK-ID
```

binding.json最小形状（占位值必须换真实登记值）：

```json
{"release_id":"REL-ID","zip_path":"/absolute/release.zip","archive_prefix":"package","bindings":[{"acceptance_id":"EXACT-VALIDATION-OR-REVIEW-ID","input_task_id":"TASK-ID","input_artifact_id":"FROZEN-FILE-ARTIFACT-ID","input_sha256":"EXACT-INPUT-SHA","output_path":"report.md","output_sha256":"EXACT-OUTPUT-SHA","transform":{"type":"identity_v1","version":1}}]}
```

review_required=false使用最新通过validation的artifact_id；需要review时使用精确当前review接受记录。跨工作单元只能使用父任务accept-work登记的receipt与其scope。每个非manifest文件恰好一项绑定；回执保存在Task证据目录，不放发布树，避免自引用。

仅identity_v1、utf8_reference_map_v1和docx_reference_map_v1可直接重演。引用映射需合法来源标识、明确namespace与准确登记，不支持一般正文、标题或排版脚本豁免；其他变化先形成新候选再验收。全树及ZIP当前字节改变必须重建manifest并重新绑定；旧hash不沿用。新登记状态structurally_registered/structure_only，在绑定完成前delivery_eligible=false。当前发布说明衔接历史待审快照，不批量通过，“unknown准确披露”不等于底层事实查明。

同Task多profile/多历史发布树目前保守拒绝；新修订用关联发布工作单元，一个明确profile。详尽入口和测试见assets/runtime/cluster/release_binding.py及tests/test_release_binding_hardening.py。

原件、旧Reviewer、旧ZIP、旧失败保持。独立解压后全文件SHA、路径、类型、引用身份、索引和业务清单一致再登记当前字节；修改后重建清单，不沿用旧发布hash。local validate、finalize、deliver、audit完成才交付链接。明确GUI实测、本地校验、未核验范围和可观察用量，不承诺省额比例。

## 恢复、成员和模型

中断后同实例recover＋audit，再decision查看紧凑状态；不依据最新文件时间、窗口位置或旧聊天恢复，不自动重发、切模型或把unknown填成running。多个对话操作同一桌面须协调，GUI_LOCK只覆盖当前工作实例。

roles/set-role/configure-roles/register-member/verify-member保留成员修改接口；不改变固定角色ID，旧Task快照不回写。新成员先用户授权登记，能力pilot实测并交付/审计后才verify-member绑定正式角色。产出视频/图像时不能仅改名称就声称其原件读取、回传或复核已具备。

controller-model-presets保留Sol current/ultra、Astra ultra/xhigh。set-controller-model仅保存请求，observe-controller-model只登记真实当前UI/turn_context；客户端模型/推理/速度始终以用户实际设置为准。未知保持null。升级或未经授权的切换先询问；本工具不切应用、不调用API。

离线文档任务按offline_document_v1配置显式offline授权，使用statements与精确document_review_scope；不默认附带公开检索来源要求。需要该模式时读取assets/runtime/templates/offline_document*.txt及cluster/document_review.py，勿把网页研究模板机械套入。

临时session_budget只在当前用户明确限定费用时使用，准备与发送阶段都核对实际路由成员与预算scope；不一致不能静默跳过。500/800是源维护轮次约束，不是新业务默认额度。材料预检失败先补原件；缺PDF解析依赖与原件损坏分别报告，均不得当作已读。批次下载发布与每个消费者真实ack分别登记。


本轮新增来源归属约束：机械改号必须从精确已接受候选JSON取得唯一 namespace、source_id 与原URL，发布映射必须逐项相符；不以调用者自行填写的映射替代依据。同字节 identity 也会核对已知来源URL。缺少可确定来源身份时拒绝机械豁免，不猜测归属。该检查不自动证明版本、重定向等价或底层事实真实性。
