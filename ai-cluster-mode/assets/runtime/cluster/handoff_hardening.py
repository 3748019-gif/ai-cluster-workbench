"""Local preflight, complete contracts, per-consumer receipts and session budgets.

No provider calls/GUI actions. These checks establish identity and availability,
not truth, entailment, actual member consumption, or hidden-model isolation.
"""
import copy
import csv
import fcntl
import hashlib
import io
import json
import math
import os
import re
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree

CONTRACT_VERSION = 'handoff_contract_v1'
PREFLIGHT_VERSION = 'material_preflight_v1'
CONTROLLER_OWNED = ('task_id', 'submission_id', 'work_item_id', 'round_id', 'provider',
                    'collected_by', 'source_namespace', 'producer', 'candidate_artifact_id',
                    'candidate_sha256', 'claim_sha256')


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _safe_file(path, root):
    """Never follow a symlink or parent escape in a task material declaration."""
    root = Path(root).resolve()
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('Parent traversal is forbidden')
    absolute = path if path.is_absolute() else root / path
    if not absolute.resolve().is_relative_to(root):
        raise ValueError('Material path escapes the authorized project')
    for component in (absolute, *absolute.parents):
        if component.is_symlink():
            raise ValueError('Symbolic links are forbidden')
        if component.resolve() == root:
            break
    relative = absolute.resolve().relative_to(root)
    if any(part in ('private', 'control', '.git', '.aws') for part in relative.parts):
        raise ValueError('Private/control material is not permitted in member preflight')
    if not absolute.is_file():
        raise ValueError('Material file does not exist')
    return absolute


def inspect_material(path, expected_format, *, required_pages=(), required_text=()):
    """Deterministic parse/coverage checks; required_text is an explicit locator check."""
    path = Path(path)
    if path.suffix.lower() in ('.partial', '.crdownload', '.download', '.tmp'):
        raise ValueError('Download is incomplete or temporary')
    data = path.read_bytes()
    if not data:
        raise ValueError('Material is empty')
    fmt = expected_format.lower().lstrip('.')
    text, pages = None, None
    if fmt == 'pdf':
        if not data.startswith(b'%PDF-'):
            raise ValueError('PDF signature mismatch (HTML/login pages are not PDFs)')
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            pages = len(reader.pages)
            if not pages:
                raise ValueError('PDF has no readable pages')
            if any(type(p) is not int or p < 1 or p > pages for p in required_pages):
                raise ValueError('Required page is missing')
            if required_text:
                text = '\n'.join(p.extract_text() or '' for p in reader.pages)
        except PyPdfError as exc:
            raise ValueError('Unreadable/truncated PDF: ' + str(exc)) from exc
    elif fmt in ('docx', 'xlsx', 'zip'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.testzip():
                raise ValueError('Archive CRC failed')
            names = archive.namelist()
            if fmt in ('docx', 'xlsx'):
                required = 'word/document.xml' if fmt == 'docx' else 'xl/workbook.xml'
                if required not in names:
                    raise ValueError('Office document part missing')
                ElementTree.fromstring(archive.read(required))
                text = archive.read(required).decode('utf-8')
            for name in names:
                p = Path(name)
                if p.is_absolute() or '..' in p.parts:
                    raise ValueError('Unsafe archive member path')
    elif fmt in ('txt', 'md', 'html', 'json', 'csv', 'tsv'):
        text = data.decode('utf-8-sig')
        if fmt == 'json':
            json.loads(text)
        elif fmt in ('csv', 'tsv'):
            if not list(csv.reader(io.StringIO(text), delimiter='\t' if fmt == 'tsv' else ',')):
                raise ValueError('Empty delimited material')
    else:
        raise ValueError('Unsupported material format: ' + fmt)
    if required_pages and fmt != 'pdf':
        raise ValueError('Page requirements only supported for PDF')
    if required_text and (text is None or any(s not in text for s in required_text)):
        raise ValueError('Required declared text/qualification not present')
    return {'sha256': _hash(data), 'bytes': len(data), 'format': fmt,
            'format_readable': True, 'pages': pages, 'fact_verified': False}


def material_preflight(root, requirements):
    """Collect all independent material errors in one pass; no identity guessing."""
    checks, errors, identities = [], [], {}
    if not isinstance(requirements, list):
        raise ValueError('Required materials must be a list')
    for index, item in enumerate(requirements):
        if not isinstance(item, dict):
            errors.append({'source_id': None, 'location': f'materials[{index}]',
                           'reason': 'Material declaration must be an object', 'blocking': True,
                           'owner': 'orchestrator'})
            continue
        sid = item.get('source_id') or item.get('artifact_id')
        record = {key: item.get(key) for key in ('source_id', 'artifact_id', 'purpose', 'owner')}
        record['blocking'] = item.get('blocking', True) is not False
        record['location'] = f'materials[{index}]'
        try:
            if not isinstance(sid, str) or not sid:
                raise ValueError('Material needs an exact source/artifact identity')
            if item.get('scope_status') == 'partial' and item.get('require_complete') is True:
                raise ValueError('Required complete context is explicitly partial')
            if not item.get('path') or not item.get('sha256') or not item.get('expected_format'):
                raise ValueError('Material declaration needs exact path/hash/format')
            path = _safe_file(item['path'], root)
            if item.get('identity_only') is True and item['expected_format'] not in ('pdf','docx','xlsx','zip','txt','md','html','json','csv','tsv'):
                raw = path.read_bytes()
                check = {'sha256': _hash(raw), 'bytes': len(raw), 'format': item['expected_format'],
                         'format_readable': None, 'format_validation': 'unsupported; identity only', 'fact_verified': False}
            else:
                check = inspect_material(path, item['expected_format'],
                                     required_pages=item.get('required_pages', ()),
                                     required_text=item.get('required_text', ()))
            if check['sha256'] != item['sha256']:
                raise ValueError('Material hash/version mismatch')
            ident = (str(path), check['sha256'], item['expected_format'])
            if sid in identities and identities[sid] != ident:
                raise ValueError('Source identity has conflicting files or versions')
            identities[sid] = ident
            checks.append({**record, **check, 'path': str(path), 'availability': 'ready',
                           'scope_status': item.get('scope_status', 'unknown')})
        except (OSError, ValueError, ImportError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            errors.append({**record, 'reason': str(exc), 'availability': 'unavailable',
                           'failure_kind': 'dependency_unavailable' if isinstance(exc, ImportError) else 'material_invalid',
                           'fact_verified': False})
    return {'version': PREFLIGHT_VERSION, 'passed': not any(e['blocking'] for e in errors),
            'checks': checks, 'errors': errors, 'automatic_semantic_proof': False}


def content_contract(kind):
    """Parser constants are the source of truth; four contracts never inherit prose."""
    from .research import SOURCE_FIELDS, CLAIM_FIELDS, RESULTS
    source_fields = [f for f in SOURCE_FIELDS if f not in CONTROLLER_OWNED]
    source = {k: None for k in source_fields}
    source.update(source_id='S1', url='https://example.org/original', title='Actual source title',
                  accessed_at=None, access_time_note='Exact time unknown; read supplied original',
                  access_status='partial_text', level='undergraduate', scope='Actual declared scope',
                  locator='Actual section/page', quote='Verbatim original excerpt',
                  excerpt_is_complete=False, omissions_marked=True, supports_claim_ids=['C1'],
                  limits=['Only the supplied section was read'], raw_artifact_id=None)
    claim = {k: None for k in CLAIM_FIELDS if k not in CONTROLLER_OWNED}
    claim.update(claim_id='C1', text='Actual limited claim', scope='Actual declared scope',
                 source_ids=['S1'], location='Draft section')
    descriptions = {
        'research': {'required': ['draft_markdown', 'sources', 'claims'],
                     'example': {'draft_markdown': 'Full requested draft.', 'sources': [source], 'claims': [claim]}},
        'review': {'required': ['sources', 'claim_checks', 'confirmed_errors', 'suspicions', 'unverified',
                                'wording_suggestions', 'passed_checks', 'semantic_findings', 'blocking'],
                   'example': {'sources': [source], 'claim_checks': [{'claim_id': 'C1', 'result': 'evidence_gap',
                       'blocking': True, 'reason': 'Original applicability not established', 'source_ids': ['S1'],
                       'applicability': {'status': 'unresolved', 'reason': 'Scope not established',
                                        'context_complete': False, 'comparisons': []}}],
                       'confirmed_errors': [], 'suspicions': [], 'unverified': ['Scope unknown'],
                       'wording_suggestions': [], 'passed_checks': [], 'semantic_findings': [], 'blocking': True}},
        'download': {'required': ['url', 'version', 'scope', 'expected_format', 'requester', 'purpose'],
                     'batch_required': ['source_id', 'original_page', 'priority', 'work_item_id'],
                     'example': {'source_id': 'S1', 'original_page': 'https://example.org/index',
                         'url': 'https://example.org/original.pdf', 'version': 'Declared version',
                         'scope': 'Actual declared scope', 'expected_format': 'pdf', 'requester': 'kimi',
                         'purpose': 'Original evidence for C1', 'priority': 1, 'work_item_id': 'Exact work item'}},
        'receipt': {'required': ['receipt_acknowledgements'], 'example': {'receipt_acknowledgements': [
            {'receipt_sha256': 'Exact supplied receipt hash', 'request_id': 'Exact supplied request ID',
             'consumer_id': 'Exact supplied consumer ID', 'status': 'consumed', 'consumed': True}]}}
    }
    if kind not in descriptions:
        raise ValueError('Unknown content contract')
    contract = {'version': CONTRACT_VERSION, 'kind': kind, **copy.deepcopy(descriptions[kind]),
                'controller_owned_fields': list(CONTROLLER_OWNED),
                'unknowns': 'Retain null/explicit reason; never infer access, dates, applicability or truth.',
                'automatic_semantic_proof': False}
    if kind in ('research', 'review'):
        contract.update(source_required_fields=source_fields,
                        source_enums={'access_status': ['full_text', 'partial_text', 'search_snippet',
                                                        'access_failed', 'reported_access']},
                        source_boolean_fields=['excerpt_is_complete', 'omissions_marked'],
                        source_array_fields=['supports_claim_ids', 'limits'])
    if kind == 'research':
        contract['claim_required_fields'] = [f for f in CLAIM_FIELDS if f not in CONTROLLER_OWNED]
    if kind == 'review':
        contract['review_result_enum'] = list(RESULTS)
    contract['sha256'] = _hash(_json(contract).encode())
    return contract


def contract_text(kind):
    return '完整输出约定（示例仅说明结构，不是事实或预置裁决）:\n' + _json(content_contract(kind))


def diagnose_root_causes(report):
    """Retain every error while grouping missing source cascades by exact ID."""
    data = report.get('data') if isinstance(report.get('data'), dict) else {}
    cards = data.get('sources', [])
    ids = {s['source_id'] for s in cards if isinstance(s, dict) and isinstance(s.get('source_id'), str)} if isinstance(cards, list) else set()
    groups = {}
    for error in report.get('errors', []):
        location, code = error.get('location', ''), error.get('code', 'unknown')
        match = re.match(r'(claims|claim_checks)\[(\d+)\]\.source_ids$', location)
        missing = []
        if code == 'unknown_source' and match:
            entries = data.get(match[1], [])
            if isinstance(entries, list) and int(match[2]) < len(entries):
                refs = entries[int(match[2])].get('source_ids', [])
                if isinstance(refs, list):
                    missing = [ref for ref in refs if isinstance(ref, str) and ref not in ids]
        keys = ['missing_source:' + ref for ref in missing] or [
            code + ':' + re.sub(r'\[\d+\]', '[*]', location)]
        for key in keys:
            group = groups.setdefault(key, {'root_cause_id': key, 'category': error.get('category', 'structure'),
                'owner': error.get('owner', 'member'), 'impacts': [], 'automatic_fact_verdict': False})
            group['impacts'].append(copy.deepcopy(error))
    return {'root_causes': list(groups.values()), 'root_cause_count': len(groups),
            'error_count': len(report.get('errors', [])), 'raw_research_needed': False,
            'semantic_review_still_required': True}


class SessionBudget:
    """One session ledger, atomic under flock+replace; no production DB migration.

    Guaranteed bounds or explicitly uncertain planning reservations block competing tasks while cost is unsettled. Confirmed spend
    only grows. Observed account resets never change session limits or spend.
    This protects controller calls that use its guard, not arbitrary user GUI.
    """
    def __init__(self, root):
        project = Path(root).resolve()
        self.root = project / '.cluster' / 'session_budgets'
        for part in (project / '.cluster', self.root):
            if part.is_symlink(): raise ValueError('Budget directory symbolic link is forbidden')
            part.mkdir(exist_ok=True)

    @contextmanager
    def _locked(self, session_id):
        if session_id in ('.', '..') or not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', session_id):
            raise ValueError('Session ID must be short ASCII')
        path = self.root / (session_id + '.json')
        if self.root.is_symlink() or self.root.parent.is_symlink() or path.is_symlink():
            raise ValueError('Budget storage symbolic link is forbidden')
        lock_path = self.root / (session_id + '.lock')
        if lock_path.is_symlink(): raise ValueError('Budget lock symbolic link is forbidden')
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield path
            fcntl.flock(lock, fcntl.LOCK_UN)

    def read(self, session_id):
        with self._locked(session_id) as path:
            return self._read(path)

    def _read(self, path):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'r') as stream:
            return json.load(stream)

    def _write(self, path, value):
        fd, name = tempfile.mkstemp(prefix=path.stem + '.', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(_json(value)); stream.flush(); os.fsync(stream.fileno())
            os.replace(name, path)
            directory = os.open(self.root, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(name): os.unlink(name)

    def init(self, session_id, limit, reassess_at, unit, provider='workbuddy'):
        if (not isinstance(limit, (int, float)) or isinstance(limit, bool) or not math.isfinite(limit) or limit <= 0
                or not isinstance(reassess_at, (int, float)) or isinstance(reassess_at, bool) or not math.isfinite(reassess_at)
                or not 0 < reassess_at <= limit or not isinstance(unit, str) or not unit.strip()
                or not isinstance(provider, str) or not provider.strip()):
            raise ValueError('Budget needs positive finite thresholds and observed native unit')
        with self._locked(session_id) as path:
            if path.exists():
                previous = self._read(path)
                if (previous['limit'], previous['reassess_at'], previous['unit'], previous['provider']) != (limit, reassess_at, unit, provider):
                    raise ValueError('Existing session authority cannot be replaced or reset')
                return previous
            value = {'session_id': session_id, 'provider': provider, 'limit': limit, 'reassess_at': reassess_at,
                     'unit': unit, 'spent': 0, 'revision': 1, 'events': {}, 'calls': {}, 'observations': [],
                     'reassessment_due': False}
            self._write(path, value)
            return value

    def apply(self, session_id, event_id, expected_revision, operation, payload):
        if not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', event_id) or not isinstance(payload, dict):
            raise ValueError('Budget event needs exact identity and object payload')
        fingerprint = _hash(_json({'operation': operation, 'payload': payload}).encode())
        with self._locked(session_id) as path:
            value = self._read(path)
            prior = value['events'].get(event_id)
            if prior:
                if prior != fingerprint: raise ValueError('Budget event identity conflict')
                return value  # Exact replay is idempotent even with its old revision.
            if type(expected_revision) is not int or value['revision'] != expected_revision:
                raise ValueError('Stale session budget revision; reload current ledger')
            call_id = payload.get('call_id')
            if operation == 'reserve':
                bound = payload.get('upper_bound')
                estimated = payload.get('estimated_cost')
                margin = payload.get('uncertainty_margin')
                guaranteed = isinstance(bound, (int, float)) and not isinstance(bound, bool) and math.isfinite(bound) and bound > 0
                if guaranteed:
                    if not payload.get('bound_evidence'):
                        raise ValueError('Guaranteed cost bound requires evidence')
                    reserved = bound
                else:
                    # The task permits cautious estimated dispatch. Never label
                    # the estimate or margin as a guaranteed fee upper bound.
                    if (bound is not None or not isinstance(estimated, (int, float)) or isinstance(estimated, bool)
                            or not math.isfinite(estimated) or estimated <= 0
                            or not isinstance(margin, (int, float)) or isinstance(margin, bool)
                            or not math.isfinite(margin) or margin <= 0
                            or not payload.get('estimate_evidence') or not payload.get('balance_observation')):
                        raise ValueError('Reliable bound unavailable: cautious estimate, margin and balance observation required')
                    reserved = estimated + margin
                if (not isinstance(call_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', call_id)
                        or call_id in value['calls'] or not isinstance(payload.get('task_id'), str) or not payload['task_id']):
                    raise ValueError('Call identity missing or already reserved')
                if any(call['status'] == 'reserved' for call in value['calls'].values()):
                    raise ValueError('Prior call unsettled; do not dispatch concurrently')
                if value['spent'] + reserved > value['limit']:
                    raise ValueError('Session dispatch limit would be exceeded')
                if value['reassessment_due']:
                    raise ValueError('Reassessment required before new dispatch')
                value['calls'][call_id] = {'status': 'reserved', 'upper_bound': bound if guaranteed else None,
                    'guaranteed_upper_bound': bound if guaranteed else None, 'planning_reserve': reserved,
                    'estimated_cost': estimated if not guaranteed else None,
                    'uncertainty_margin': margin if not guaranteed else None,
                    'estimate_evidence': payload.get('estimate_evidence'),
                    'balance_observation': payload.get('balance_observation'),
                    'bound_evidence': payload.get('bound_evidence'), 'task_id': payload.get('task_id'),
                    'model': payload.get('model'), 'spent_before': value['spent']}
            elif operation == 'settle':
                call = value['calls'].get(call_id)
                if not call or call['status'] != 'reserved':
                    raise ValueError('Exact unsettled call required')
                actual = payload.get('actual_cost')
                if (not isinstance(actual, (int, float)) or isinstance(actual, bool) or not math.isfinite(actual) or actual < 0
                        or not payload.get('cost_evidence')):
                    raise ValueError('Cost remains unknown; keep reservation and stop new dispatch')
                # Real cost above a predicted bound must be preserved, not erased
                # merely to make a budget report look within limit.
                value['spent'] += actual
                call.update(status='settled', actual_cost=actual, cost_evidence=payload['cost_evidence'],
                            upper_bound_exceeded=(actual > call['upper_bound']) if call['upper_bound'] is not None else None,
                            planning_reserve_exceeded=actual > call['planning_reserve'],
                            cost_kind=payload.get('cost_kind', 'actual_bill'),
                            exact_task_cost=actual if payload.get('cost_kind', 'actual_bill') == 'actual_bill' else None)
                value['reassessment_due'] = value['spent'] >= value['reassess_at']
            elif operation == 'reassess':
                if not payload.get('reason') or any(c['status'] == 'reserved' for c in value['calls'].values()):
                    raise ValueError('Reassessment needs rationale and settled costs')
                value['reassessment_due'] = False
                value['last_reassessment'] = copy.deepcopy(payload)
            elif operation == 'observe':
                if not payload.get('observed_at') or payload.get('unit') != value['unit']:
                    raise ValueError('Observation needs timestamp and matching native unit')
                value['observations'].append(copy.deepcopy(payload))
                # Shared balance increases/reset are observations, never refunds.
            else:
                raise ValueError('Unknown budget operation')
            value['events'][event_id] = fingerprint
            value['revision'] += 1
            self._write(path, value)
            return value

    def guard(self, session_id, call_id, provider):
        value = self.read(session_id)
        if value['provider'] != provider: raise ValueError('Session provider mismatch')
        call = value['calls'].get(call_id)
        if not call or call['status'] != 'reserved':
            raise ValueError('Exact unsettled session-budget reservation required')
        if value['spent'] + call['planning_reserve'] > value['limit'] or value['reassessment_due']:
            raise ValueError('Session budget does not authorize a new dispatch')
        return {'session_id': session_id, 'call_id': call_id, 'revision': value['revision'],
                'upper_bound': call['upper_bound'], 'guaranteed_upper_bound': call['guaranteed_upper_bound'],
                'planning_reserve': call['planning_reserve'], 'hard_limit_guaranteed': call['guaranteed_upper_bound'] is not None,
                'observed_native_unit': value['unit']}


class HandoffHardeningMixin:
    def material_preflight(self, task, phase='producer'):
        """Registered immutable inputs plus explicit task-local material requirements."""
        m = self.get(task)
        inputs = m['spec'].get('input_artifact_ids', m['source_files'])
        requirements = []
        for aid in inputs:
            a = self.artifact(m, aid)
            name = a.get('logical_name', '')
            requirements.append({'artifact_id': aid, 'source_id': a.get('source_global_id', aid),
                'path': str(self.task_path(task) / a['filename']), 'sha256': a['hash'],
                'expected_format': Path(name).suffix.lstrip('.').lower() or 'txt',
                'purpose': 'Frozen registered input', 'owner': 'orchestrator', 'blocking': True, 'identity_only': True})
        if phase == 'reviewer':
            if m.get('candidate'):
                a = self.artifact(m, m['candidate'])
                requirements.append({'artifact_id': a['artifact_id'], 'source_id': a['artifact_id'],
                    'path': str(self.task_path(task) / a['filename']), 'sha256': a['hash'],
                    'expected_format': Path(a['logical_name']).suffix.lstrip('.') or 'json',
                    'purpose': 'Exact review candidate', 'owner': 'orchestrator', 'blocking': True})
            for rec in m.get('candidate_files', []):
                requirements.append({'artifact_id': rec['artifact_id'], 'source_id': rec['artifact_id'],
                    'path': rec['frozen_path'], 'sha256': rec['sha256'],
                    'expected_format': Path(rec['logical_filename']).suffix.lstrip('.'),
                    'purpose': 'Exact frozen deliverable', 'owner': 'orchestrator', 'blocking': True, 'identity_only': True})
        explicit = m['spec'].get('required_materials', [])
        if not isinstance(explicit, list): raise ValueError('required_materials must be a list')
        for item in explicit:
            if not isinstance(item, dict):
                requirements.append(item)
            elif item.get('phase') not in (None,'producer','reviewer'):
                requirements.append({**item,'path':None})
            elif item.get('phase') in (None,phase):
                requirements.append(item)
        report = material_preflight(self.root, requirements)
        expected = m['spec'].get('handoff_contract_version', CONTRACT_VERSION)
        if expected != CONTRACT_VERSION:
            report['errors'].append({'source_id': None, 'location': 'handoff_contract_version',
                'reason': 'Template/parser contract version mismatch', 'owner': 'orchestrator', 'blocking': True})
            report['passed'] = False
        return report

    def assert_material_preflight(self, task, phase='producer'):
        from .core import GuardError
        report = self.material_preflight(task, phase)
        if not report['passed']:
            raise GuardError('Material preflight failed before dispatch: ' + _json(report))
        return report

    def handoff_contract(self, kind):
        return content_contract(kind)

    def session_budget_guard(self, task, phase='producer'):
        m = self.get(task); settings = m['spec'].get('session_budget')
        if not settings: return None
        provider = m['producer'] if phase == 'producer' else m['reviewer']
        provider = m.get('member_profiles',{}).get(provider,{}).get('platform',provider)
        budget_provider = settings.get('provider', 'workbuddy')
        if provider != budget_provider:
            raise ValueError('Session budget scope mismatch: routed provider '+str(provider)
                             +' does not match budget provider '+str(budget_provider)
                             +'; create an explicitly scoped work unit before dispatch')
        ledger = SessionBudget(self.root)
        result = ledger.guard(settings['session_id'], settings['call_id'], provider)
        if ledger.read(settings['session_id'])['calls'][settings['call_id']].get('task_id') != task:
            raise ValueError('Session reservation belongs to a different task')
        return result

    def captured_output_diagnostics(self, task):
        """Re-use retained adaptation/error evidence; never ask a member to re-research."""
        m = self.get(task)
        report_id = m.get('last_ingestion_report')
        if not report_id:
            return {'status': 'not_available', 'root_causes': [], 'root_cause_count': 0,
                    'raw_preserved': True, 'new_external_submission_required': False}
        report = json.loads(self.read_artifact(m, report_id))
        data = {}
        if report.get('adapted_artifact'):
            data = json.loads(self.read_artifact(m, report['adapted_artifact']))
        diagnosed = diagnose_root_causes({'data': data, 'errors': report.get('errors', [])})
        return {**diagnosed, 'status': 'diagnosed', 'report_artifact': report_id,
                'raw_artifact': report.get('raw_response'), 'raw_sha256': report.get('raw_sha256'),
                'raw_preserved': True, 'new_external_submission_required': False}

    def decision_packet(self, task):
        """Compact independent consumer and root-cause state, backed by raw evidence."""
        packet = super().decision_packet(task)
        m = self.get(task)
        for entry in packet.get('attachment_queue', []):
            item = m['attachment_queue'][entry['request_id']]
            entry['consumers'] = [{k: c.get(k) for k in ('consumer_id','member','work_item_id','status','file_sha256')}
                                  for c in item.get('consumers', {}).values()]
        diagnosed = self.captured_output_diagnostics(task)
        if m.get('provider_execution_status') == 'completed':
            ingestion = m.get('artifact_ingestion_status')
            if ingestion in ('parse_error','identity_mismatch'):
                packet['blocked'] = True
                packet['next_action'] = ('reconcile_captured_identity_do_not_resend' if ingestion=='identity_mismatch'
                                         else 'repair_captured_output_locally_do_not_resend')
            elif ingestion == 'captured':
                packet['next_action'] = 'ingest_captured_output_do_not_resend'
        packet['handoff_diagnostics'] = {
            'report_artifact': diagnosed.get('report_artifact'), 'raw_artifact': diagnosed.get('raw_artifact'),
            'root_cause_count': diagnosed['root_cause_count'],
            'root_causes': [{'root_cause_id': r['root_cause_id'], 'owner': r['owner'],
                             'category': r['category'], 'impact_count': len(r['impacts'])}
                            for r in diagnosed['root_causes']]}
        return packet

    def request_attachment(self, task, request):
        """Keep legacy request authority; bind exact dependent consumer separately."""
        item = super().request_attachment(task, request)
        consumer_id = request.get('consumer_id') or ('CON-' + _hash(_json({
            'work_item_id': request.get('work_item_id', self.get(task)['work_item_id']),
            'member': request['requester'], 'purpose': request['purpose']}).encode())[:16])
        with self._mutate(task) as m:
            current = m['attachment_queue'][item['request_id']]
            consumers = current.setdefault('consumers', {})
            consumer = {'consumer_id': consumer_id, 'member': request['requester'],
                        'work_item_id': request.get('work_item_id', m['work_item_id']),
                        'purpose': request['purpose'], 'status': 'pending'}
            if consumer_id in consumers:
                prior = consumers[consumer_id]
                if any(prior[k] != consumer[k] for k in ('member', 'work_item_id', 'purpose')):
                    raise ValueError('Attachment consumer identity conflict')
            else: consumers[consumer_id] = consumer
            context = {key:request[key] for key in ('source_id','original_page','priority') if key in request}
            if context:
                context['consumer_id'] = consumer_id
                contexts = current.setdefault('request_contexts',[])
                if context not in contexts: contexts.append(context)
        return self.get(task)['attachment_queue'][item['request_id']]

    def request_attachment_batch(self, task, requests):
        from .permissions import public_url
        if not isinstance(requests, list) or not requests: raise ValueError('Nonempty request batch required')
        errors = []
        known_members = set(self.get(task).get('role_bindings', {}).values())
        for i, request in enumerate(requests):
            if not isinstance(request, dict): errors.append({'index': i, 'reason': 'Object required'}); continue
            missing = [f for f in content_contract('download')['required'] + content_contract('download')['batch_required']
                       if request.get(f) is None or request.get(f) == '']
            if missing: errors.append({'index': i, 'missing': missing})
            bad_text = [f for f in ('source_id','work_item_id','version','scope','purpose','expected_format')
                        if not isinstance(request.get(f), str) or not request[f].strip()]
            if bad_text: errors.append({'index': i, 'invalid_text_fields': bad_text})
            if request.get('requester') not in known_members:
                errors.append({'index': i, 'reason': 'Unknown attachment requester'})
            if not isinstance(request.get('url'), str) or not isinstance(request.get('original_page'), str) or not public_url(request.get('url', '')) or not public_url(request.get('original_page', '')):
                errors.append({'index': i, 'reason': 'Public original page and exact attachment URL required'})
            if type(request.get('priority')) is not int or request['priority'] < 0:
                errors.append({'index': i, 'reason': 'Nonnegative integer priority required'})
        if errors: raise ValueError('Batch request preflight: ' + _json(errors))
        return [self.request_attachment(task, r) for r in sorted(requests, key=lambda r: r['priority'])]

    def attachment_result(self, task, request_id, result):
        """Exact event replay is idempotent; changed bytes invalidate prior consumption."""
        fingerprint = _hash(_json(result).encode())
        m = self.get(task); item = m.get('attachment_queue', {}).get(request_id)
        if item and fingerprint in item.get('result_event_hashes', []):
            current = item.get('current')
            if current and _hash(Path(current['path']).read_bytes()) != current['sha256']:
                raise ValueError('Previously completed download bytes changed')
            return item
        if result.get('status') in ('downloaded', 'reused'):
            try: inspect_material(result.get('path', ''), item['expected_format'] if item else '')
            except (ValueError, OSError, ImportError) as exc:
                from .core import GuardError
                raise GuardError(str(exc)) from exc
        old_sha = item.get('current', {}).get('sha256') if item and item.get('current') else None
        completed = super().attachment_result(task, request_id, result)
        with self._mutate(task) as m:
            current = m['attachment_queue'][request_id]
            current.setdefault('result_event_hashes', []).append(fingerprint)
            new_sha = current.get('current', {}).get('sha256') if current.get('current') else None
            if new_sha != old_sha:
                for consumer in current.get('consumers', {}).values():
                    if consumer['status'] != 'pending':
                        current.setdefault('consumption_history', []).append(copy.deepcopy(consumer))
                    consumer['status'] = 'pending'
                    consumer.pop('evidence_artifact', None)
                    consumer.pop('receipt_sha256', None)
                    consumer.pop('file_sha256', None)
        return self.get(task)['attachment_queue'][request_id]

    def attachment_result_batch(self, task, results):
        """One failed item blocks its dependencies, not every successful neighbor."""
        records = []
        for value in results:
            try:
                item = self.attachment_result(task, value['request_id'], value['result'])
                records.append({'request_id': value['request_id'], 'status': item['status'],
                                'consumers': copy.deepcopy(item.get('consumers', {}))})
            except (OSError, ValueError, KeyError) as exc:
                records.append({'request_id': value.get('request_id'), 'status': 'rejected', 'reason': str(exc)})
        return {'items': records, 'all_ready': all(r['status'] in ('downloaded', 'reused') for r in records),
                'automatic_member_wakeup': False}

    def acknowledge_attachment_consumption(self, task, receipt_artifact, request_id, consumer_id,
                                          evidence_task, evidence_artifact):
        """Only captured, confirmed, exact-version raw member acknowledgements count."""
        from .core import GuardError
        m = self.get(task); receipt = self._fresh(m, receipt_artifact)
        item = m.get('attachment_queue', {}).get(request_id)
        consumer = item.get('consumers', {}).get(consumer_id) if item else None
        if not consumer or not item.get('current'): raise GuardError('Exact ready consumer/request required')
        evidence_m = self.get(evidence_task); evidence = self._fresh(evidence_m, evidence_artifact)
        if (evidence['provider'] != consumer['member'] or evidence_m['work_item_id'] != consumer['work_item_id']
                or not evidence.get('logical_name', '').startswith('raw_output-')):
            raise GuardError('Acknowledgement member/work item/captured identity mismatch')
        submission = next((s for s in evidence_m['submissions'] if s.get('captured_artifact') == evidence_artifact
                           and s['submission_id'] == evidence.get('submission_id')
                           and s['provider'] == evidence['provider'] and s.get('confirmed_at')
                           and s.get('first_completion_observed_at')
                           and s['status'] in ('confirmed', 'running', 'completed')), None)
        if not submission: raise GuardError('Confirmed captured member response required')
        raw = self.read_artifact(evidence_m, evidence_artifact).decode('utf-8')
        try: payload = json.loads(raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip())
        except ValueError: raise GuardError('Structured exact receipt acknowledgement required')
        acks = payload.get('receipt_acknowledgements', []) if isinstance(payload, dict) else []
        valid = any(isinstance(a, dict) and a.get('receipt_sha256') == receipt['hash']
                    and a.get('request_id') == request_id and a.get('consumer_id') == consumer_id
                    and a.get('status') == 'consumed' and a.get('consumed') is True for a in acks) if isinstance(acks, list) else False
        snapshot = json.loads(self.read_artifact(m, receipt_artifact))
        old = next((i for i in snapshot.get('items', []) if i.get('request_id') == request_id), {})
        old_consumer = old.get('consumers', {}).get(consumer_id)
        if (not valid or not old_consumer or any(old_consumer.get(k)!=consumer[k] for k in ('member','work_item_id','purpose'))
                or not old.get('current') or old['current']['sha256'] != item['current']['sha256']):
            raise GuardError('Member did not consume this exact current receipt/file version')
        with self._mutate(task) as current:
            target = current['attachment_queue'][request_id]['consumers'][consumer_id]
            if current['attachment_queue'][request_id]['current']['sha256'] != item['current']['sha256']:
                raise GuardError('Attachment changed during acknowledgement')
            if (target.get('status')=='acknowledged' and target.get('evidence_artifact')==evidence_artifact
                    and target.get('receipt_sha256')==receipt['hash']):
                return target
            target.update(status='acknowledged', receipt_sha256=receipt['hash'],
                          file_sha256=item['current']['sha256'], evidence_task=evidence_task,
                          evidence_artifact=evidence_artifact)
            self._event(current, 'ATTACHMENT_CONSUMER_ACKNOWLEDGED', 'orchestrator',
                        'Exact member/work-item consumption; other consumers remain pending',
                        extra={'request_id': request_id, 'consumer_id': consumer_id, 'evidence_task': evidence_task,
                               'evidence_artifact': evidence_artifact})
        return self.get(task)['attachment_queue'][request_id]['consumers'][consumer_id]
