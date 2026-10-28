"""One evidence-bound curriculum draft, coverage ledger and deterministic views.

This verifies the declared, transcribed inventory. It cannot establish that a
human/LLM has extracted every occurrence from a natural-language original, or
replace independent applicability and semantic review. No parallel state store.
"""
import copy
import hashlib
import json
import math

INVENTORY = 'canonical_inventory_v1'
DRAFT = 'canonical_draft_v1'
VIEW = 'canonical_view_v1'
TEMPLATE = 'canonical_table_v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def source_context(inv):
    """Compare evidence bytes and scope across tasks, never task-local UUIDs."""
    return sha({'school': inv['school'], 'scope_id': inv['scope_id'],
                'sources': [{k: v for k, v in source.items() if k not in ('artifact_id', 'original_path')}
                            for source in inv['sources']],
                'occurrences': inv['occurrences'],
                'mappings': [{**mapping, 'evidence': [{k: v for k, v in e.items() if k != 'artifact_id'}
                             for e in mapping['evidence']]} for mapping in inv['mappings']]})


def _error(message):
    from .core import GuardError
    raise GuardError('CANONICAL: ' + message)


def _object(value, fields, required, label):
    if not isinstance(value, dict):
        _error(label + ' must be an object')
    extra = set(value) - set(fields)
    missing = set(required) - set(value)
    if extra or missing:
        _error(label + ' unknown fields=' + str(sorted(extra)) +
               ' missing fields=' + str(sorted(missing)))


def _text(value, label, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        _error(label + ' must be a ' + ('possibly empty ' if empty else '') + 'string')


def _list(value, label, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        _error(label + ' must be a ' + ('nonempty ' if nonempty else '') + 'list')


def _strings(value, label, nonempty=False):
    _list(value, label, nonempty)
    for entry in value:
        _text(entry, label)
    if len(value) != len(set(value)):
        _error(label + ' contains duplicate values')


def _index(rows, key, label):
    _list(rows, label)
    out = {}
    for row in rows:
        if not isinstance(row, dict) or key not in row:
            _error(label + ' requires ' + key)
        _text(row[key], label + '.' + key)
        if row[key] in out:
            _error(label + ' duplicate ' + row[key])
        out[row[key]] = row
    return out


def _number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        _error(label + ' must be a nonnegative finite number')


def _credit(c, label):
    keys = {'value', 'unit', 'status', 'original', 'reason'}
    _object(c, keys, keys, label)
    _text(c['unit'], label + '.unit')
    _text(c['original'], label + '.original')
    if c['status'] not in ('known', 'unknown', 'variable', 'not_applicable'):
        _error(label + ' unknown credit status')
    if c['status'] == 'known':
        _number(c['value'], label + '.value')
        if c['reason'] is not None:
            _error(label + ' known value cannot have unknown reason')
    else:
        if c['value'] is not None:
            _error(label + ' unknown/variable value must be null, never zero')
        _text(c['reason'], label + '.reason')


def _constraints(value, label):
    keys = {'min_credits', 'max_credits', 'min_courses', 'max_courses', 'expression'}
    _object(value, keys, keys, label)
    _text(value['expression'], label + '.expression')
    for key in keys - {'expression'}:
        if value[key] is not None:
            _number(value[key], label + '.' + key)
            if key.endswith('courses') and type(value[key]) is not int:
                _error(label + ' course counts must be integers')
    for suffix in ('credits', 'courses'):
        lo, hi = value['min_' + suffix], value['max_' + suffix]
        if lo is not None and hi is not None and lo > hi:
            _error(label + ' lower bound exceeds upper bound')


def _credit_text(credit):
    # Original wording remains visible even with a known number: "at most",
    # variable load and units must not be reduced to a guessed fixed total.
    text = credit['original'] + '（单位：' + credit['unit'] + '）'
    if credit['value'] is None:
        text += '；固定数值未确定：' + credit['reason']
    return text


def _row(row_id, cells):
    return {'item_ids': [row_id], 'cells': cells}


def render_views(draft, draft_artifact, draft_sha256, template_version, parameters):
    """The only supported generator; visible rows and semantic maps are rebuilt.

    No caller-supplied prewritten view or unverified semantic hash is accepted.
    Both projections contain every item and every independent group constraint.
    """
    if template_version != TEMPLATE:
        _error('unsupported generation template')
    _object(parameters, {'locale', 'simple_show_sources'}, (), 'generation parameters')
    if parameters.get('locale', 'zh-CN') != 'zh-CN':
        _error('unsupported locale')
    if type(parameters.get('simple_show_sources', False)) is not bool:
        _error('simple_show_sources must be boolean')
    parameters = {'locale': 'zh-CN', 'simple_show_sources': False, **parameters}
    facts = {'school': draft['school'], 'scope_id': draft['scope_id'],
             'version': draft['version'], 'items': draft['items'], 'groups': draft['groups']}
    result = {}
    for variant in ('full', 'simple'):
        full = variant == 'full'
        cols = ['中文名称', '原文名称', '类型与类别', '适用路线及版本', '原计量与单位',
                '必修或选修性质', '共享组关系', '条件与译名说明', '来源定位'] if full else [
                '中文名称与类别', '适用路线及版本', '原计量与单位', '必修或选修性质', '共享组关系', '条件与译名说明']
        if not full and parameters['simple_show_sources']:
            cols.append('来源定位')
        rows = []
        mapping = {}
        for item in draft['items']:
            common = ['、'.join(item['route_ids']) + '；' + item['applicable_version'],
                      _credit_text(item['credits']), item['requirement'],
                      '、'.join(item['group_ids']) or '无共享组',
                      '；'.join(item['conditions'] + ([item['translation_note']] if item['translation_note'] else [])) or '无另加条件']
            source = '；'.join(x['occurrence_id'] + '：' + x['location'] for x in item['sources'])
            cells = [item['name_zh'], item['name_original'], item['kind'] + '；' + item['category'], *common, source] if full else [
                item['name_zh'] + '；' + item['category'], *common]
            if not full and parameters['simple_show_sources']:
                cells.append(source)
            mapping[item['id']] = {'semantic_sha256': sha(item),
                                   'locations': [{'table_id': 'items', 'row': len(rows)}]}
            rows.append(_row(item['id'], cells))
        groups = []
        for group in draft['groups']:
            c = group['constraints']
            terms = [c['expression']]
            for key, label in [('min_credits', '合计最低学分'), ('max_credits', '合计最高学分'),
                               ('min_courses', '至少选课门数'), ('max_courses', '最多选课门数')]:
                if c[key] is not None:
                    terms.append(label + '：' + str(c[key]))
            members = '；'.join(x['item_id'] + '（' + '、'.join(x['route_ids']) + '）' for x in group['members'])
            source = '；'.join(x['occurrence_id'] + '：' + x['location'] for x in group['sources'])
            cells = [group['name_zh'], '、'.join(group['route_ids']) + '；' + group['applicable_version'],
                     members, '；'.join(terms), '；'.join(group['conditions']) or '无另加条件', source]
            mapping[group['id']] = {'semantic_sha256': sha(group),
                                    'locations': [{'table_id': 'groups', 'row': len(groups)}]}
            groups.append(_row(group['id'], cells))
        result[variant] = {'schema_version': VIEW, 'view': variant,
                           'school': draft['school'], 'scope_id': draft['scope_id'],
                           'draft_artifact': draft_artifact, 'draft_sha256': draft_sha256,
                           'template_version': template_version, 'parameters': parameters,
                           'tables': [{'id': 'items', 'columns': cols, 'rows': rows},
                                      {'id': 'groups', 'columns': ['共享组', '适用路线及版本', '成员及路线', '共同约束', '条件', '来源定位'], 'rows': groups}],
                           'notes': ['学分保留原件计量；共享组额度只适用于该组，不重复计入每个成员。',
                                     '覆盖率仅针对已登记来源条目，不代表全部自然语言提取或全网资料已完整。'],
                           'semantic_mapping': mapping, 'semantic_sha256': sha(facts)}
    return result


class CanonicalMixin:
    def _canonical_enabled(self, m):
        flag = m['spec'].get('canonical_workflow_v1', False)
        if type(flag) is not bool:
            _error('canonical_workflow_v1 must be boolean')
        return flag

    def _canonical_editable(self, m):
        if not self._canonical_enabled(m):
            _error('canonical workflow is not enabled for this task snapshot')
        if m['status'] in ('DELIVERED', 'FINALIZING', 'SUBMITTED', 'SUBMISSION_UNCERTAIN', 'REVIEW_RUNNING', 'READY_TO_SUBMIT'):
            _error('cannot change content while sending, reviewing or finalized')

    def _canonical_evidence(self, m, evidence, label):
        _list(evidence, label, True)
        for e in evidence:
            _object(e, {'artifact_id', 'sha256', 'location', 'quote'}, {'artifact_id', 'sha256', 'location'}, label)
            if e['artifact_id'] not in m['source_files']:
                _error(label + ' must reference registered original evidence')
            a = self._fresh(m, e['artifact_id'])
            if a['hash'] != e['sha256']:
                _error(label + ' evidence hash mismatch')
            _text(e['location'], label + '.location')
            if 'quote' in e:
                _text(e['quote'], label + '.quote')
                try:
                    text = self.read_artifact(m, e['artifact_id']).decode('utf-8')
                except UnicodeDecodeError:
                    _error(label + ' quote needs a registered readable text extraction')
                if e['quote'] not in text:
                    _error(label + ' quoted evidence not found in exact original')

    def _check_inventory(self, m, inv, require_complete=False):
        top = {'schema_version', 'school', 'scope_id', 'sources', 'occurrences', 'mappings'}
        _object(inv, top, top, 'inventory')
        if inv['schema_version'] != INVENTORY:
            _error('unknown inventory schema')
        _text(inv['school'], 'inventory.school'); _text(inv['scope_id'], 'inventory.scope_id')
        source_map = _index(inv['sources'], 'source_id', 'sources')
        occurrence_map = _index(inv['occurrences'], 'occurrence_id', 'occurrences')
        mappings = _index(inv['mappings'], 'occurrence_id', 'mappings')
        if not source_map:
            _error('inventory must declare at least one original source')
        pending = []
        for sid, source in source_map.items():
            keys = {'source_id', 'artifact_id', 'original_path', 'sha256', 'version', 'routes', 'collection',
                    'reading', 'parse_complete', 'expected_occurrence_ids'}
            _object(source, keys, keys, 'source ' + sid)
            if source['artifact_id'] not in m['source_files']:
                _error('inventory source is not a registered original')
            a = self._fresh(m, source['artifact_id'])
            if a['hash'] != source['sha256']:
                _error('original source hash mismatch: ' + sid)
            for key in ('original_path', 'version'):
                _text(source[key], sid + '.' + key)
            _strings(source['routes'], sid + '.routes', True)
            _strings(source['expected_occurrence_ids'], sid + '.expected_occurrence_ids')
            if type(source['parse_complete']) is not bool:
                _error(sid + '.parse_complete must be boolean')
            for kind in ('collection', 'reading'):
                _object(source[kind], {'status', 'scope', 'pending_reason'}, {'status', 'scope', 'pending_reason'}, sid + '.' + kind)
                if source[kind]['status'] not in ('complete', 'partial', 'pending'):
                    _error('unknown ' + kind + ' status')
                _text(source[kind]['scope'], sid + '.' + kind + '.scope')
                if source[kind]['status'] != 'complete':
                    _text(source[kind]['pending_reason'], sid + '.' + kind + '.pending_reason')
                    pending.append(sid + ':' + kind)
                elif source[kind]['pending_reason'] is not None:
                    _error('complete source stage cannot retain a pending reason')
            if not source['parse_complete']:
                pending.append(sid + ':parse_incomplete')
            actual = {oid for oid, row in occurrence_map.items() if row.get('source_id') == sid}
            missing = set(source['expected_occurrence_ids']) - actual
            extra = actual - set(source['expected_occurrence_ids'])
            if extra:
                _error('occurrences absent from source declared scope: ' + str(sorted(extra)))
            pending.extend(sorted(missing))
        unlisted = set(m['source_files']) - {s['artifact_id'] for s in source_map.values()} - {
            e['artifact_id'] for mapping in inv['mappings'] if isinstance(mapping, dict)
            for e in mapping.get('evidence', []) if isinstance(e, dict)}
        if unlisted:
            _error('registered originals absent from source inventory: ' + ', '.join(sorted(unlisted)))
        for oid, occurrence in occurrence_map.items():
            keys = {'occurrence_id', 'source_id', 'location', 'kind', 'identity', 'route', 'required', 'facts'}
            _object(occurrence, keys, keys, 'occurrence ' + oid)
            if occurrence['source_id'] not in source_map:
                _error('unknown source for ' + oid)
            source = source_map[occurrence['source_id']]
            _text(occurrence['location'], oid + '.location')
            if occurrence['kind'] not in ('course', 'requirement', 'open_elective', 'group'):
                _error('unknown occurrence kind')
            if occurrence['route'] not in source['routes']:
                _error('occurrence route absent from source scope')
            if type(occurrence['required']) is not bool:
                _error('required flag must be boolean')
            identity = occurrence['identity']
            _object(identity, {'school', 'code', 'name_original', 'version'}, {'school', 'code', 'name_original', 'version'}, oid + '.identity')
            for key in ('school', 'name_original', 'version'):
                _text(identity[key], oid + '.identity.' + key)
            if identity['code'] is not None:
                _text(identity['code'], oid + '.identity.code')
            if identity['school'] != inv['school'] or identity['version'] != source['version']:
                _error('occurrence school/version differs from exact source')
            facts = occurrence['facts']
            _object(facts, {'credits', 'requirement', 'conditions', 'group_ids', 'category', 'name_zh', 'constraints', 'members'}, (), oid + '.facts')
            if 'credits' in facts:
                _credit(facts['credits'], oid + '.facts.credits')
            if 'constraints' in facts:
                _constraints(facts['constraints'], oid + '.facts.constraints')
            if oid not in mappings:
                pending.append(oid)
        for oid, mapping in mappings.items():
            keys = {'occurrence_id', 'status', 'entity_id', 'route_ids', 'reason', 'evidence', 'duplicate_of', 'exclusion_rule'}
            _object(mapping, keys, {'occurrence_id', 'status', 'entity_id', 'route_ids', 'reason', 'evidence'}, 'mapping ' + oid)
            if oid not in occurrence_map:
                _error('mapping references undeclared occurrence ' + oid)
            occurrence = occurrence_map[oid]
            if mapping['status'] not in ('included', 'duplicate', 'excluded', 'pending'):
                _error('unknown mapping disposition')
            _strings(mapping['route_ids'], oid + '.route_ids')
            if mapping['status'] in ('included', 'duplicate'):
                _text(mapping['entity_id'], oid + '.entity_id')
                if mapping['route_ids'] != [occurrence['route']]:
                    _error('mapping must preserve this occurrence route exactly')
                if mapping['status'] == 'duplicate':
                    original = occurrence_map.get(mapping.get('duplicate_of'))
                    anchor = mappings.get(mapping.get('duplicate_of'))
                    if not original or not anchor or anchor['status'] != 'included' or anchor['entity_id'] != mapping['entity_id']:
                        _error('duplicate requires an included original of the same entity')
                    if original['identity'] != occurrence['identity'] or original['kind'] != occurrence['kind']:
                        _error('name-only/different-code/version merge forbidden')
                    _text(mapping['reason'], oid + '.duplicate reason')
            elif mapping['status'] == 'excluded':
                if mapping['entity_id'] is not None or mapping['route_ids']:
                    _error('excluded occurrence cannot claim an included entity or route')
                _text(mapping['reason'], oid + '.exclusion reason')
                _text(mapping.get('exclusion_rule'), oid + '.exclusion rule')
                self._canonical_evidence(m, mapping['evidence'], oid + '.exclusion evidence')
                if not any(mapping['exclusion_rule'] in e.get('quote', '') for e in mapping['evidence']):
                    _error('exclusion rule must be quoted from exact registered evidence: ' + oid)
            else:
                _text(mapping['reason'], oid + '.pending reason')
                pending.append(oid)
            if mapping['evidence'] and mapping['status'] != 'excluded':
                self._canonical_evidence(m, mapping['evidence'], oid + '.evidence')
        counts = {status: sum(x['status'] == status for x in mappings.values()) for status in ('included', 'duplicate', 'excluded', 'pending')}
        expected = set().union(*(set(x['expected_occurrence_ids']) for x in source_map.values()))
        # An occurrence ID is globally unique even when it appears in two documents.
        if sum(len(x['expected_occurrence_ids']) for x in source_map.values()) != len(expected):
            _error('one occurrence identity cannot belong to several sources')
        pending = sorted(set(pending))
        mapped = len([x for x in mappings.values() if x['status'] != 'pending'])
        report = {'scope': 'declared_original_occurrences_only', 'expected_occurrences': len(expected),
                  'observed_occurrences': len(occurrence_map), 'explained_occurrences': mapped,
                  'unique_entities': len({x['entity_id'] for x in mappings.values() if x['status'] in ('included', 'duplicate')}),
                  'unique_courses': len({sha(occurrence_map[x['occurrence_id']]['identity']) for x in mappings.values()
                                         if x['status'] in ('included', 'duplicate') and occurrence_map[x['occurrence_id']]['kind'] == 'course'}),
                  'route_links': len({(x['entity_id'], r) for x in mappings.values() if x['status'] in ('included', 'duplicate') for r in x['route_ids']}),
                  'dispositions': counts, 'pending': pending,
                  'mapping_rate': mapped / len(expected) if expected else None,
                  'complete': not pending, 'extraction_completeness': 'not_established_by_program'}
        if require_complete and pending:
            _error('coverage freeze refused; unresolved declared occurrences/stages: ' + ', '.join(pending))
        return report

    def _check_draft(self, m, inv, draft):
        self._check_inventory(m, inv, require_complete=True)
        keys = {'schema_version', 'school', 'scope_id', 'version', 'items', 'groups', 'history'}
        _object(draft, keys, keys, 'draft')
        if draft['schema_version'] != DRAFT or draft['school'] != inv['school'] or draft['scope_id'] != inv['scope_id']:
            _error('draft schema/school/scope differs from frozen inventory')
        _text(draft['version'], 'draft.version'); _list(draft['history'], 'draft.history')
        items = _index(draft['items'], 'id', 'items'); groups = _index(draft['groups'], 'id', 'groups')
        if set(items) & set(groups):
            _error('item and group stable IDs must be globally distinct')
        for iid, item in items.items():
            keys = {'id', 'school', 'route_ids', 'applicable_version', 'kind', 'code', 'name_original', 'name_zh',
                    'credits', 'category', 'requirement', 'group_ids', 'conditions', 'sources', 'translation_note', 'semantic_history'}
            _object(item, keys, keys, 'item ' + iid)
            for field in ('school', 'applicable_version', 'name_original', 'name_zh', 'category'):
                _text(item[field], iid + '.' + field)
            if item['school'] != draft['school'] or item['kind'] not in ('course', 'requirement', 'open_elective'):
                _error('item school/kind mismatch')
            if item['code'] is not None:
                _text(item['code'], iid + '.code')
            if item['requirement'] not in ('required', 'restricted_elective', 'open_elective', 'unknown'):
                _error('unknown requirement mode')
            for field in ('route_ids', 'group_ids', 'conditions'):
                _strings(item[field], iid + '.' + field, field == 'route_ids')
            _text(item['translation_note'], iid + '.translation_note', True)
            _list(item['semantic_history'], iid + '.semantic_history')
            _credit(item['credits'], iid + '.credits')
        for gid, group in groups.items():
            keys = {'id', 'school', 'route_ids', 'applicable_version', 'name_zh', 'members', 'constraints', 'conditions', 'sources'}
            _object(group, keys, keys, 'group ' + gid)
            for field in ('school', 'applicable_version', 'name_zh'):
                _text(group[field], gid + '.' + field)
            if group['school'] != draft['school']:
                _error('group school mismatch')
            _strings(group['route_ids'], gid + '.route_ids', True)
            _strings(group['conditions'], gid + '.conditions')
            _constraints(group['constraints'], gid + '.constraints')
            _list(group['members'], gid + '.members', True)
            seen = set()
            for member in group['members']:
                _object(member, {'item_id', 'route_ids'}, {'item_id', 'route_ids'}, gid + '.member')
                if member['item_id'] not in items or member['item_id'] in seen:
                    _error('group requires distinct registered item members')
                seen.add(member['item_id'])
                _strings(member['route_ids'], gid + '.member routes', True)
                item = items[member['item_id']]
                if not set(member['route_ids']) <= set(group['route_ids']) & set(item['route_ids']):
                    _error('group member applicable route mismatch')
                if gid not in item['group_ids']:
                    _error('group relation is not bidirectional')
            if group['constraints']['min_courses'] is not None and group['constraints']['min_courses'] > len(seen):
                _error('minimum course count exceeds finite members')
        for iid, item in items.items():
            for gid in item['group_ids']:
                if gid not in groups or iid not in {x['item_id'] for x in groups[gid]['members']}:
                    _error('item/group membership mismatch')
        entities = {**items, **groups}
        mappings = {x['occurrence_id']: x for x in inv['mappings']}
        occurrences = {x['occurrence_id']: x for x in inv['occurrences']}
        routes = {eid: set() for eid in entities}; evidence = {eid: set() for eid in entities}
        identities = {}
        for oid, mapping in mappings.items():
            if mapping['status'] not in ('included', 'duplicate'):
                continue
            eid = mapping['entity_id']; occurrence = occurrences[oid]
            if eid not in entities:
                _error('mapped occurrence missing from draft: ' + oid + ' → ' + eid)
            entity = entities[eid]
            if (occurrence['kind'] == 'group') != (eid in groups):
                _error('group occurrence cannot be mapped as a course')
            if occurrence['kind'] != 'group':
                identity = {k: entity[v] for k, v in [('school', 'school'), ('code', 'code'), ('name_original', 'name_original'), ('version', 'applicable_version')]}
                if identity != occurrence['identity'] or entity['kind'] != occurrence['kind']:
                    _error('entity identity/name/code/version drift: ' + oid)
            else:
                identity = occurrence['identity']
                if entity['applicable_version'] != identity['version']:
                    _error('group version differs from source')
            if eid in identities and identities[eid] != identity:
                _error('distinct source identities merged into one entity: ' + eid)
            identities[eid] = identity
            for field, value in occurrence['facts'].items():
                if field not in entity or entity[field] != value:
                    _error('draft conflicts with transcribed original fact: ' + oid + '.' + field)
            routes[eid].update(mapping['route_ids']); evidence[eid].add(oid)
        for eid, entity in entities.items():
            if set(entity['route_ids']) != routes[eid] or not evidence[eid]:
                _error('draft entity has omitted/unsupported route or occurrence: ' + eid)
            _list(entity['sources'], eid + '.sources', True)
            cited = set()
            for source in entity['sources']:
                _object(source, {'occurrence_id', 'location'}, {'occurrence_id', 'location'}, eid + '.source')
                oid = source['occurrence_id']
                if oid not in evidence[eid] or source['location'] != occurrences[oid]['location'] or oid in cited:
                    _error('source location or occurrence mapping drift: ' + eid)
                cited.add(oid)
            if cited != evidence[eid]:
                _error('draft drops a mapped source occurrence: ' + eid)
        return {'item_count': len(items), 'group_count': len(groups),
                'semantic_sha256': sha({'school': draft['school'], 'scope_id': draft['scope_id'],
                                        'version': draft['version'], 'items': draft['items'], 'groups': draft['groups']})}

    def register_inventory(self, task, inventory):
        from .core import js
        inventory = copy.deepcopy(inventory)
        with self._mutate(task) as m:
            self._canonical_editable(m)
            report = self._check_inventory(m, inventory)
            inputs = list(dict.fromkeys([s['artifact_id'] for s in inventory['sources']] +
                          [e['artifact_id'] for mapping in inventory['mappings'] for e in mapping['evidence']]))
            a = self._artifact(m, js(inventory).encode(), 'evidence', 'codex', 'canonical-inventory.json', inputs)
            m['canonical'] = {'inventory': a['artifact_id'], 'draft': None, 'views': {}}
            self._event(m, 'CANONICAL_INVENTORY_REGISTERED', 'orchestrator', 'Declared source scope and occurrence dispositions recorded', a['artifact_id'], extra=report)
        return {'artifact_id': a['artifact_id'], 'sha256': a['hash'], 'coverage': report}

    def freeze_canonical(self, task, draft):
        from .core import js
        draft = copy.deepcopy(draft)
        with self._mutate(task) as m:
            self._canonical_editable(m)
            state = m.get('canonical', {})
            if not state.get('inventory'):
                _error('register inventory before freezing a draft')
            self._fresh(m, state['inventory'])
            inventory = json.loads(self.read_artifact(m, state['inventory']))
            report = self._check_draft(m, inventory, draft)
            a = self._artifact(m, js(draft).encode(), 'evidence', 'codex', 'canonical-draft.json', [state['inventory']])
            state.update(draft=a['artifact_id'], draft_sha256=a['hash'], views={}, semantic_sha256=report['semantic_sha256'])
            self._event(m, 'CANONICAL_DRAFT_FROZEN', 'orchestrator', 'One content candidate frozen; independent acceptance remains required', a['artifact_id'], extra=report)
        return {'artifact_id': a['artifact_id'], 'sha256': a['hash'], **report}

    def generate_canonical_views(self, task, template_version=TEMPLATE, parameters=None):
        from .core import js
        with self._mutate(task) as m:
            self._canonical_editable(m)
            current = self._canonical_gate(m, require_views=False)
            state = m['canonical']; draft = json.loads(self.read_artifact(m, state['draft']))
            generated = render_views(draft, state['draft'], current['draft_sha256'], template_version, {} if parameters is None else parameters)
            views = {}
            for variant, body in generated.items():
                a = self._artifact(m, js(body).encode(), 'evidence', 'codex', 'canonical-' + variant + '.json', [state['draft']])
                views[variant] = {'artifact_id': a['artifact_id'], 'sha256': a['hash'], 'semantic_sha256': body['semantic_sha256']}
            state.update(views=views, template_version=template_version, parameters=generated['full']['parameters'])
            self._event(m, 'CANONICAL_VIEWS_GENERATED', 'orchestrator', 'Full and simple views generated from one exact draft', state['draft'], extra={'views': views})
            result = copy.deepcopy(state)
        return result

    def _canonical_gate(self, m, require_views=True):
        if not self._canonical_enabled(m):
            return {'passed': True, 'status': 'legacy_not_enabled', 'views': {}, 'coverage': None}
        state = m.get('canonical', {})
        if not state.get('inventory') or not state.get('draft'):
            _error('inventory and canonical draft required before content processing')
        inventory_artifact = self._fresh(m, state['inventory'])
        draft_artifact = self._fresh(m, state['draft'])
        if inventory_artifact['status'] != 'current' or draft_artifact['status'] != 'current':
            _error('superseded canonical inventory/draft cannot be current')
        if draft_artifact['input_artifacts'] != [inventory_artifact['artifact_id']] or state.get('draft_sha256') != draft_artifact['hash']:
            _error('draft inventory ancestry or hash changed')
        inventory = json.loads(self.read_artifact(m, state['inventory']))
        draft = json.loads(self.read_artifact(m, state['draft']))
        coverage = self._check_inventory(m, inventory, require_complete=True)
        checked = self._check_draft(m, inventory, draft)
        if state.get('semantic_sha256') != checked['semantic_sha256']:
            _error('draft semantic identity changed')
        if require_views:
            if set(state.get('views', {})) != {'full', 'simple'}:
                _error('both canonical views must be generated')
            expected = render_views(draft, state['draft'], draft_artifact['hash'], state.get('template_version'), state.get('parameters', {}))
            for variant in ('full', 'simple'):
                record = state['views'][variant]; a = self._fresh(m, record['artifact_id'])
                if a['status'] != 'current' or a['hash'] != record['sha256'] or a['input_artifacts'] != [state['draft']]:
                    _error('view artifact ancestry/hash mismatch')
                actual = json.loads(self.read_artifact(m, a['artifact_id']))
                if actual != expected[variant] or record['semantic_sha256'] != checked['semantic_sha256']:
                    _error('view differs from deterministic canonical projection')
        return {'passed': True, 'status': 'content_candidate_not_accepted', **copy.deepcopy(state),
                'coverage': coverage, 'draft_sha256': draft_artifact['hash'], **checked,
                'inventory_sha256': inventory_artifact['hash'], 'source_context_sha256': source_context(inventory)}

    def inspect_canonical(self, task):
        from .core import GuardError
        m = self.get(task)
        if not self._canonical_enabled(m):
            return self._canonical_gate(m)
        state = copy.deepcopy(m.get('canonical', {}))
        try:
            if not state.get('draft') and state.get('inventory'):
                self._fresh(m, state['inventory'])
                inventory = json.loads(self.read_artifact(m, state['inventory']))
                return {**state, 'passed': False, 'status': 'inventory_only', 'errors': ['draft_not_frozen'],
                        'coverage': self._check_inventory(m, inventory)}
            return self._canonical_gate(m)
        except GuardError as e:
            return {**state, 'passed': False, 'status': 'blocked', 'errors': [str(e)]}

    def canonical_impact(self, task, previous_task):
        old, new = self.get(previous_task), self.get(task)
        if old['run_mode'] != new['run_mode']:
            _error('cross-mode semantic reuse forbidden')
        before = self._canonical_gate(old); after = self._canonical_gate(new)
        if not old.get('canonical') or not new.get('canonical'):
            _error('canonical impact needs two enabled exact drafts')
        a = json.loads(self.read_artifact(old, old['canonical']['draft']))
        b = json.loads(self.read_artifact(new, new['canonical']['draft']))
        if (a['school'], a['scope_id']) != (b['school'], b['scope_id']):
            _error('impact scope cannot silently cross school or stable scope')
        left = {x['id']: x for x in a['items'] + a['groups']}; right = {x['id']: x for x in b['items'] + b['groups']}
        changed = {key for key in set(left) | set(right) if left.get(key) != right.get(key)}
        impacted = set(changed)
        # Iterate through both versions. Moving a member out of a shared budget
        # affects the old group too, not only the new neighbours.
        while True:
            prior = set(impacted)
            for entity in list(left.values()) + list(right.values()):
                neighbours = set(entity.get('group_ids', [])) | {x['item_id'] for x in entity.get('members', [])}
                if entity['id'] in impacted or neighbours & impacted:
                    impacted.add(entity['id']); impacted.update(neighbours)
            if prior == impacted:
                break
        context_changed = a['version'] != b['version'] or before['source_context_sha256'] != after['source_context_sha256']
        if context_changed:
            impacted.update(set(left) | set(right))
        return {'schema_version': 'canonical_impact_v1', 'task_id': task, 'previous_task_id': previous_task,
                'changed_ids': sorted(changed), 'affected_ids': sorted(impacted),
                'unchanged_ids': sorted((set(left) & set(right)) - impacted),
                'semantic_changed': before['semantic_sha256'] != after['semantic_sha256'],
                'source_context_changed': context_changed,
                'views': {k: {'changed_bytes': before['views'][k]['sha256'] != after['views'][k]['sha256'],
                              'semantic_changed': before['views'][k]['semantic_sha256'] != after['views'][k]['semantic_sha256']} for k in ('full', 'simple')},
                'authority': 'Impact only; no independent review or acceptance automatically inherited'}
