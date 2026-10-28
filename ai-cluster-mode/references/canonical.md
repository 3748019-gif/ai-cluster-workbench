# 来源覆盖、统一底稿与同源视图

仅新任务快照 `canonical_workflow_v1: true` 启用；旧任务不反推覆盖状态。
统一控制器方法是 `register_inventory` → `freeze_canonical` →
`generate_canonical_views` → `inspect_canonical`。这些步骤得到内容候选，不授予
独立复核通过、内容接受、原生文档视觉通过或最终发布权限。

## 盘点格式

`canonical_inventory_v1` 顶层字段：`schema_version, school, scope_id, sources,
occurrences, mappings`。未知字段拒绝，不能靠拼错字段静默降低要求。

每个 `sources` 对象字段：

| 字段 | 含义 |
|---|---|
| source_id / artifact_id / sha256 | 来源稳定ID、控制器登记的原件ID、精确原件哈希 |
| original_path / version / routes | 原路径、原件适用版本、已检查的路线 |
| collection / reading | 各为 `{status, scope, pending_reason}`；status为complete/partial/pending |
| parse_complete | 是否解析完所声明范围，必须为布尔值 |
| expected_occurrence_ids | 声明范围内预期来源出现ID，不能用独立课程数代替 |

来源的 `complete` 只表示所声明范围。控制器验证注册原件可读及哈希，不能
独立证明人员或模型真的逐页读完，也不能证明自然语言提取未漏项。非完整
状态、未解析完和预期条目缺失均保留待核并阻止冻结，不改作“未公开”。
本任务所有登记原件须列入盘点，规则性原件可有空的课程出现列表。

每个 `occurrences` 对象：
`{occurrence_id, source_id, location, kind, identity, route, required, facts}`。
kind为course/requirement/open_elective/group；identity为
`{school, code, name_original, version}`，无原代码时code为null。
`facts` 是从该位置转录的约束，可含credits、requirement、conditions、group_ids、
category、name_zh、constraints、members。底稿必须保持已转录事实，不允许
把原件明确4学分改为unknown，或将共享12、至少选1改为24、全部必修。
转录正确性和适用性仍由独立复核承担。

每个 `mappings` 对象：`{occurrence_id, status, entity_id, route_ids, reason,
evidence}`，status为included/duplicate/excluded/pending。included与duplicate
保留该来源出现的路线。duplicate另需duplicate_of，锚点必须是同身份的included
条目；不同代码、不同版本不能凭同名合并。excluded需exclusion_rule，原句
必须在登记证据quote中出现；evidence各项为
`{artifact_id, sha256, location, quote?}`。控制器校验精确引文确在证据字节中，
但规则的真实授权性和业务含义仍须独立核验。pending保留具体原因。

覆盖报告分开统计原始出现数、唯一实体数和路线关联数；mapping_rate仅是
已盘点范围中可解释去向的比例，不宣传全网、所有课程或自然语言自动100%。

## 唯一底稿格式

`canonical_draft_v1` 顶层字段：`schema_version, school, scope_id, version,
items, groups, history`。

每个items对象字段：
`id, school, route_ids, applicable_version, kind, code, name_original, name_zh,
credits, category, requirement, group_ids, conditions, sources, translation_note,
semantic_history`。

credits为`{value, unit, status, original, reason}`：known才有非负有限数值，
reason为null；unknown/variable/not_applicable的value必须为null且有原因。
不以0代未知，也不从未知研究量倒推毕业总分。original保留“最多”“至少”
“按选课确定”等原计量，两个视图均展示。

requirement为required/restricted_elective/open_elective/unknown。开放选修
记录类别实体和共享组，不虚构完整课程名单；有限池每个出现仍须映射。
sources每项为`{occurrence_id, location}`，必须与覆盖台账精确一致。

每个groups对象字段：`id, school, route_ids, applicable_version, name_zh,
members, constraints, conditions, sources`。members各为`{item_id, route_ids}`；
成员与条目的group_ids必须双向一致。constraints为
`{min_credits, max_credits, min_courses, max_courses, expression}`；未给出的
数值为null，共享约束单独保存并只显示一次，不复制给各成员。

## 生成、变更和发布绑定

支持模板 `canonical_table_v1`；参数仅 `locale: zh-CN` 和
`simple_show_sources: boolean`。两个视图的JSON都来自该底稿，包含
`tables:[{id, columns, rows:[{item_ids, cells}]}]`、notes与semantic_mapping。
可见表格不显示课程代码；映射保留实体与路线，组约束表保留成员ID以便展开。
简版压缩原文和来源展示，保留全部课程、类别、路线、限制、单位和共享约束。

控制器状态位于任务的canonical字段：inventory、draft、draft_sha256、
semantic_sha256、views.full/simple、template_version、parameters。文件哈希、
底稿语义哈希、模板参数和视图映射分别登记。门禁重新运行确定性投影并逐项
比较，不接受作者自报“语义未变”，也不以自填semantic_sha256代替实际视图。
重新登记库存使旧底稿及视图失效；重新冻结底稿使旧视图失效。历史字节不改。

`canonical_impact(new_task, previous_task)`核对两份当前完整底稿，展开新旧
共享组的依赖；来源字节或适用范围改变时保守扩大核验。任务内artifact UUID
变化本身不等于来源变化。该报告只说明影响，不能自动继承独立复核或接受。

## 可复跑定向测试

在工具包的assets/runtime目录用当前解释器执行：

```text
python3 -m unittest tests.test_canonical -v
```

测试使用真实Cluster状态和临时原件，覆盖T01–T06/T19的受控正反例。
`tests.test_canonical.curriculum_fixture(controller)`供集成测试生成
`(task_id, inventory, draft)`，是公开合成协议案例，不是通用检错率或盲测。
