# 素材契约、任务分流与增量续作

使用统一入口 `scripts/toolkit.py create-task --workspace ABS --file TASK_JSON --mode test|live`。外部运行配置可选 `default_task_route`；原模板保持只读。业务任务 JSON 可声明 task_route=file_processing/open_research/mixed，混合项必须有 route_reason。显式 open_research 默认启用独立复核；任务显式 review_required=false 时保持其声明，但风险或 semantic_judgment 仍可强制复核。旧任务未声明 task_route 时保留原默认。显式 assigned_provider 仍须属于本轮角色成员，不改变全局绑定。文件加工有实质事实或适用性判断时声明 semantic_judgment=true 与非空 semantic_scope，强制保持独立复核。记录 content_contributors，不自审；跨供应商复核必须使用实际另一成员，内置工作者不能冒充外援。

## 原件和范围

先 create-task，再通过 ctl source 登记原件及范围依据，控制器产生准确 Artifact ID、路径、SHA256。在 CREATED 阶段调用 material-contract TASK --file CONTRACT_JSON。合同模板结构见 templates/material-contract.example.json；示例 ID 为占位符，必须替换为本任务已登记 ID。先冻结完成标准和依据，不得在失败后把总数改小放行。

范围 basis JSON 必须含 scope、scope_authority、origin。origin 为 site_snapshot/frozen_original/user_declared_list/test_fixture；test_fixture 只能 test 模式。按来源选择准确适配器：

- static_html_v1：单份原始HTML，required_tables 检查明确动态组件数量，allow_empty 仅适用于明确合法空内容；错误标题与普通教材出现 login 字样分开。
- catalog_v1：原始 {listLength,data:[...]}；basis 提供 id_field、fields、expected_count、expected_ids，可用明确 selected_ids 表示子集。子集不得称全部原始目录。
- eth_table_v1：每份 {caption,thead:{rows:[{cells:[{content}]}]},tbody:同结构}；basis.tables 提供原行宽和准确 {row,col,text}。多组件、条件、单位、脚注与关系按位置比对；跨行/跨列暂不支持，不静默降级。
- cursor_v1：原始 GraphQL data.node.ratings.edges 与 pageInfo；basis 提供 entity、query、expected_count、fields、page_requests（cursor/query/source_sha256）。记录唯一ID、游标链、终点和快照关系，失败不当终点。网络重新采集仍由获准GUI完成，适配器只读已登记冻结输入。

源的 observed_contexts 数组记录 {source_sha256,context}，context 是公开材料观察到的对象/版次/学位/届别等。合同 target_context 与其对照。来源版本未知或不匹配阻断；即使版本匹配，也只证明材料版本归属，事实适用性默认 not_claimed。作业务判断须由必要独立复核按准确范围核验。

required_attachments 按逻辑文件名和真实hash核对；实质图片按源hash、实体、当前候选图引用与图片字节检查，装饰图片须明确排除。图片PDF转换当前不支持；文字通过不能替代图片观察。未知结构返回 unsupported，不生成成功空结果。

## 同一控制器命令

所有以下命令通过 `python3 scripts/toolkit.py ctl --workspace ABS --mode test|live -- COMMAND` 调用；当前实例已有 active profile，创建/续作均用 create-task，不用旧 create/new-work 绕过。

```
material-contract TASK --file CONTRACT_JSON
package TASK
local-start TASK
material-restore TASK --key KEY --output ABS_NEW_READING
material-convert TASK --reading ABS_READING --output ABS_NEW_PDF --renderer reportlab
material-render TASK --artifact FROZEN_PDF_ID --directory ABS_EMPTY_DIR --renderer ABS_PDFTOPPM --dpi 144
material-visual TASK --file CURRENT_OBSERVATION_JSON
material-check TASK
material-impact TASK --previous PREVIOUS_DELIVERED_TASK
```

local-start / local-output / freeze-files 的参数以 ctl help 为准。转换前需运行本地工作项并恢复准确已声明阅读件；恢复和转换只写新输出，不修改原件。冻结候选文件后渲染/观察精确PDF，观测JSON包括pdf_artifact_id、pdf_sha256、observer=codex、kind=actual_page_observation、pages、render_artifacts、dpi、scope、notes、passed，记录必须对应控制器实际生成并登记的PNG。不得用作者自报或没有查看的图片冒充视觉验收。完整视觉合同要求 all_pages 与全部当前页。

PDF检查需要当前环境已有 pypdf/pdfplumber；ReportLab文字/表格转换另需已有reportlab及Arial字体，本机实际用系统字体并登记hash。Poppler渲染传当前准确可执行路径，依赖不具备则明确未验，不安装软件补齐。显式Edge离线打印路径也可传入；本轮其沙箱打印失败已保留，不声称可用。附件下载仍只由Codex操作本机Edge Computer Use，以上转换不接入网络下载。

## 接受和发布

六维分别报告 access、collection、version、applicability、format、delivery。格式能解析不表示完整；full表示相对于合同冻结范围齐全；未作事实判断为not_claimed。通过检查但尚未权威接受时为pending_authoritative_acceptance；finalize/deliver 后才能 accepted_current_candidate。受限交付 limited_delivery 必须有真实授权、明确missing_scope、claim、user_impact、attempts、allowed_errors，仅允许明确范围/版本缺口；重复ID、符号、错误页或格式问题不能豁免。当前用户要求必须取全时不能擅设例外。

最终恢复件和PDF作为接受候选冻结文件，发布绑定使用 identity_v1 精确当前字节。原件→派生件→观察→接受→发布树关系可审计。不得将 HTML/PDF 转换装作引用映射或通用机械变换；另一PDF即使自洽清单也不能旁路。release-register仅登记结构，release-bind仍为接受门禁。发布选项 required_directories 明确中文空目录，ZIP须保留对应唯一目录项；当前额外Word/CSV仍按实际交付合同纳入，不能擅删或沿用旧ZIP hash。

## 依赖复用

新轮次引用合法已结束前轮、同父任务/工作项、递增轮次和新目标，保留未知发送保护。材料合同的 reuse_from 仅可指已DELIVERED且audit通过任务。控制器根据源/范围/恢复规则/内容合同/PDF/排版/检查器/视觉/链接索引生成阶段键；仅匹配阶段记录可复用。仅改字体会使PDF和视觉重新检查；源、范围或恢复逻辑改变重验内容与下游；未知检查器版本也不能沿旧回执。material-impact 只计算计划，不打印、不替代接受。

输出仍需 local-output、freeze-files、validate、必要review、finalize、release-register/release-bind、deliver、audit 的已有真实流程。来源适用性有变化时只对影响范围按原研究角色补证，不全量重跑业务。
