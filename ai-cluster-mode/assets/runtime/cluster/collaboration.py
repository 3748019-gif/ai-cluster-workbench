"""Task-local rolling handoffs; no provider API, daemon or synthetic GUI evidence."""
import json
from pathlib import Path


class CollaborationMixin:
    def register_existing_original(self,task,request_id,path,index_path,index_sha256):
        """Bind a saved original using one exact public release index identity."""
        import csv
        from .core import GuardError,digest,js
        m=self.get(task);item=m.get('attachment_queue',{}).get(request_id)
        if not item:raise GuardError('Unknown original request')
        path=Path(path).resolve();index=Path(index_path).resolve()
        if not path.is_relative_to(self.root/'outputs') or not index.is_relative_to(self.root/'outputs'):
            raise GuardError('Existing original registration is limited to authorized project release files')
        raw=index.read_bytes()
        if digest(raw)!=index_sha256:raise GuardError('Release index hash changed')
        original_data=path.read_bytes();sha=digest(original_data);release=index.parent.parent
        expected={k:item[k] for k in ('url','version','scope','expected_format')}
        matched=[]
        for row in csv.DictReader(raw.decode('utf-8-sig').splitlines()):
            identity={'url':row.get('url') or row.get('original_url'),'version':row.get('version_or_year') or row.get('applicable_cohort_version'),
                'scope':row.get('degree_scope') or row.get('scope'),'expected_format':path.suffix.lstrip('.').lower()}
            target=release/(row.get('actual_path') or row.get('relative_path') or '')
            if target.resolve()==path and row.get('sha256')==sha and identity==expected:matched.append(row)
        if not matched:raise GuardError('No exact source-index path/hash/URL/version/scope identity')
        with self._mutate(task) as current:
            evidence=self._artifact(current,raw,'evidence','codex','original-index-'+sha[:8]+'.csv',[],{'original_path':str(index)})
            a=self._artifact(current,original_data,'evidence','codex','original-'+sha[:12]+path.suffix,[evidence['artifact_id']],
                {'acquisition_identity':expected,'existing_original':True,'fact_verified':False,'index_rows':matched})
        return a

    def freeze_candidate_files(self, task, paths):
        """Freeze produced files once, alongside the candidate, before independent review."""
        from .core import GuardError, digest
        m=self.get(task)
        native=m['spec'].get('native_document_workflow_v1') is True
        if (m['status']!='OUTPUT_RECEIVED' and not (native and m['status'] in ('REVIEW_COMPLETED','CONFLICT_RESOLUTION','FINALIZING'))) or not m.get('candidate'): raise GuardError('Capture/ingest completed candidate first')
        if native:
            self._document_stage_gate(m)
            expected={str(Path(d[k]).resolve()) for d in m['native_documents'].values() for k in ('docx_path','pdf_path')}
            if {str(Path(x).resolve()) for x in paths}!=expected:raise GuardError('Native release file scope must match current accepted DOCX/PDF views')
        candidate=self._fresh(m,m['candidate']);base=(self.root/'exchange'/task).resolve()
        existing=m.get('candidate_files',[])
        if existing and not native:
            if [str(Path(p).resolve()) for p in paths]!=[x['origin_path'] for x in existing]: raise GuardError('File scope changed; new candidate required')
            for rec in existing:
                if digest(Path(rec['origin_path']).read_bytes())!=rec['sha256']: raise GuardError('Produced file changed after freeze')
                self._fresh(m,rec['artifact_id'])
            return existing
        files=[]
        for value in paths:
            path=Path(value)
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(base): raise GuardError('Produced file outside exact task exchange')
            files.append((path,path.read_bytes()))
        with self._mutate(task) as current:
            if current['candidate']!=candidate['artifact_id']: raise GuardError('Candidate changed during freeze')
            if native:self._document_stage_gate(current)
            if current.get('candidate_files'):current.setdefault('candidate_file_history',[]).append(current['candidate_files'])
            records=[]
            for index,(path,data) in enumerate(files):
                a=self._artifact(current,data,'evidence',candidate['provider'],f'candidate-file-{index:02d}'+path.suffix,[candidate['artifact_id']],{'candidate_payload_file':True})
                records.append({'artifact_id':a['artifact_id'],'sha256':a['hash'],'origin_path':str(path.resolve()),
                    'frozen_path':str(self.task_path(task)/a['filename']),'logical_filename':path.name,
                    'candidate_artifact_id':candidate['artifact_id']})
            current['candidate_files']=records
            current.setdefault('candidate_file_sets',{})[candidate['artifact_id']]=records
            self._event(current,'CANDIDATE_FILES_FROZEN','orchestrator','Exact deliverable bytes frozen for independent review',candidate['artifact_id'],extra={'files':records})
        return records

    def request_attachment(self, task, request):
        from .core import GuardError, digest, js
        from .permissions import public_url
        required = ('url', 'version', 'scope', 'expected_format', 'requester', 'purpose')
        if any(not request.get(k) for k in required) or not public_url(request.get('url', '')):
            raise GuardError('Attachment needs public URL, version, scope, format, requester and purpose')
        if request['requester'] not in set(self.get(task).get('role_bindings', {}).values()):
            raise GuardError('Unknown attachment requester')
        identity = {k: request[k] for k in ('url', 'version', 'scope', 'expected_format')}
        rid = 'ATT-' + digest(js(identity).encode())[:20]
        with self._mutate(task) as m:
            if m['status'] == 'DELIVERED': raise GuardError('Delivered task is immutable')
            queue = m.setdefault('attachment_queue', {})
            item = queue.setdefault(rid, {'request_id': rid, **identity, 'status': 'requested', 'subscriptions': [], 'attempts': []})
            subscription = {k: request[k] for k in ('requester', 'purpose')}
            if subscription not in item['subscriptions']: item['subscriptions'].append(subscription)
            self._event(m, 'ATTACHMENT_REQUESTED', 'orchestrator', 'Version-scoped request, subscriptions retained', extra={'request_id': rid})
        return item

    def attachment_result(self, task, request_id, result):
        """Caller supplies actual Edge observation or a registered pre-existing original."""
        from .core import GuardError, digest, js
        status = result.get('status')
        if status not in ('downloaded', 'reused', 'failed', 'uncertain'): raise GuardError('Unknown acquisition status')
        checked = None
        observation_data = None
        if status in ('downloaded', 'reused'):
            path = Path(result.get('path', '')).resolve()
            if not path.is_file() or Path(result['path']).is_symlink(): raise GuardError('Missing/unsafe original')
            data = path.read_bytes(); sha = digest(data)
            if sha != result.get('sha256'): raise GuardError('Original hash mismatch')
            item = self.get(task).get('attachment_queue', {}).get(request_id)
            if not item: raise GuardError('Unknown attachment request')
            fmt = item['expected_format'].lower()
            if fmt == 'pdf':
                if not data.startswith(b'%PDF-'): raise GuardError('Not a PDF original')
                from pypdf import PdfReader
                if not PdfReader(str(path)).pages: raise GuardError('Unreadable PDF')
            elif fmt == 'json': json.loads(data)
            elif fmt in ('docx', 'xlsx', 'zip'):
                import zipfile
                with zipfile.ZipFile(path) as z:
                    if z.testzip(): raise GuardError('Corrupt archive')
                    if fmt == 'docx' and 'word/document.xml' not in z.namelist(): raise GuardError('Not DOCX')
                    if fmt == 'xlsx' and 'xl/workbook.xml' not in z.namelist(): raise GuardError('Not XLSX')
            elif fmt in ('csv', 'txt', 'html'): data.decode('utf-8-sig')
            else: raise GuardError('Unsupported original format')
            if status == 'downloaded':
                p = result.get('edge_observation', {})
                if p.get('kind')!=('gui_observation' if self.mode=='live' else 'simulated') or p.get('browser') != 'Microsoft Edge' or p.get('completed') is not True or p.get('path') != str(path) or not p.get('observed_at') or not p.get('evidence_path'):
                    raise GuardError('Actual Edge completion/path evidence required; stable size is insufficient')
                evidence = Path(p['evidence_path'])
                observation_data=evidence.read_bytes() if evidence.is_file() else None
                if observation_data is None or digest(observation_data) != p.get('evidence_sha256'):
                    raise GuardError('Missing Edge observation bytes/hash')
                if p.get('url') != item['url'] or p.get('version') != item['version']: raise GuardError('Wrong Edge version/URL')
            else:
                origin = result.get('provenance', {})
                om = self.get(origin.get('task_id', ''))
                a = self.artifact(om, origin.get('artifact_id'))
                if digest(self.read_artifact(om, a['artifact_id'])) != sha: raise GuardError('Reuse must match registered original')
                if a['role'] not in ('source', 'evidence'): raise GuardError('Reuse needs original/evidence provenance')
                identity={k:item[k] for k in ('url','version','scope','expected_format')}
                if a.get('acquisition_identity')!=identity: raise GuardError('Reuse original URL/version/scope/format is unbound or mismatched')
            checked = {'path': str(path), 'sha256': sha, 'bytes': len(data), 'format': fmt, 'format_readable': True,
                       'fact_verified': False}
        elif not result.get('reason'): raise GuardError('Failure/uncertainty needs reason')
        with self._mutate(task) as m:
            item = m.get('attachment_queue', {}).get(request_id)
            if not item: raise GuardError('Unknown attachment request')
            if item['status'] == 'uncertain' and status == 'downloaded' and not result.get('reconciliation'):
                raise GuardError('Reconcile uncertain attempt before retry; never auto-redownload')
            if checked and status=='downloaded':
                identity={k:item[k] for k in ('url','version','scope','expected_format')}
                observed=self._artifact(m,observation_data,'evidence','codex',
                    'edge-observation-'+request_id[-10:]+'.txt',[])
                original=self._artifact(m,data,'evidence','codex','edge-original-'+request_id[-10:]+'.'+checked['format'],[observed['artifact_id']],
                    {'acquisition_identity':identity,'fact_verified':False,'acquisition_channel':'native_edge'})
                checked['provenance']={'task_id':task,'artifact_id':original['artifact_id']}
            elif checked:checked['provenance']=result['provenance']
            entry = {**result, **(checked or {})}; item['attempts'].append(entry); item['status'] = status
            item['current'] = checked
            self._event(m, 'ATTACHMENT_RESULT', 'orchestrator', 'Item-specific result; availability is not fact verification', extra={'request_id': request_id, 'status': status})
        return item

    def publish_attachment_receipt(self, task):
        from .core import js
        with self._mutate(task) as m:
            queue = m.get('attachment_queue', {})
            body = {'task_id': task, 'items': list(queue.values()), 'delivery_status': 'published', 'consumption': 'unknown'}
            a = self._artifact(m, js(body).encode(), 'evidence', 'codex', 'attachment_receipt.json', [])
            m.setdefault('attachment_receipts', []).append({'artifact_id': a['artifact_id'], 'sha256': a['hash'], 'consumption': 'unknown'})
        return a

    def acknowledge_receipt(self, task, receipt_artifact, evidence_artifact):
        from .core import GuardError
        with self._mutate(task) as m:
            receipt = self._fresh(m, receipt_artifact); evidence = self._fresh(m, evidence_artifact)
            # Acknowledgement must be visible in captured member output, not controller intent.
            text = self.read_artifact(m, evidence_artifact).decode('utf-8')
            external=set(m.get('role_bindings',{}).values())-{'codex'}
            if evidence['provider'] not in external or evidence.get('logical_name', '').startswith('raw_output-') is False:
                raise GuardError('Actual captured member response required')
            submission=next((s for s in m['submissions'] if s.get('captured_artifact')==evidence_artifact
                and s['submission_id']==evidence.get('submission_id') and s['provider']==evidence['provider']),None)
            if not submission:raise GuardError('Acknowledgement is not linked to a confirmed captured submission')
            try:
                payload=json.loads(text.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip())
            except (ValueError,AttributeError):raise GuardError('Explicit structured acknowledgement required; mentioning hash is insufficient')
            ack=payload.get('receipt_acknowledgements',[]) if isinstance(payload,dict) else []
            if not isinstance(ack,list) or not any(isinstance(x,dict) and x.get('receipt_sha256')==receipt['hash']
                and x.get('status')=='consumed' and x.get('consumed') is True for x in ack):
                raise GuardError('Member did not affirm consumption of exact receipt version')
            rec = next(x for x in m['attachment_receipts'] if x['artifact_id'] == receipt_artifact)
            rec.update(consumption='acknowledged', evidence_artifact=evidence_artifact, member=evidence['provider'])
            self._event(m, 'RECEIPT_ACKNOWLEDGED', 'orchestrator', 'Exact receipt acknowledgement observed', evidence_artifact)
        return rec

    def record_optional_action(self, task, action, outcome, critical=False):
        from .core import GuardError
        if outcome not in ('completed', 'denied', 'user_stop', 'uncertain'): raise GuardError('Unknown optional action result')
        decision = 'stop_affected_action' if critical or outcome in ('user_stop', 'uncertain') else ('skip_optional_action' if outcome == 'denied' else 'continue')
        with self._mutate(task) as m:
            self._event(m, 'OPTIONAL_ACTION_RESULT', 'orchestrator', action, extra={'outcome': outcome, 'critical': critical, 'decision': decision})
        return {'decision': decision}

    def record_handled_work(self, parent, old_task, replacement, reason, evidence):
        from .core import GuardError, js
        old = self.get(old_task); newer = self.get(replacement)
        if not reason or not evidence: raise GuardError('Replacement needs reason and exact evidence')
        if newer['status'] != 'DELIVERED' or not self.audit(replacement)['passed']: raise GuardError('Replacement must be delivered and audited')
        # Append relationship at maintenance parent; never rewrite historical failure/state.
        with self._mutate(parent) as m:
            for aid in evidence: self._fresh(m, aid)
            rec = {'old_task': old_task, 'original_status': old['status'], 'handled_by': replacement, 'reason': reason, 'evidence': evidence,
                   'historical_state_unchanged': True, 'consumption': 'unknown'}
            a = self._artifact(m, js(rec).encode(), 'evidence', 'codex', 'handled-'+old_task[-4:]+'.json', evidence)
            m.setdefault('handled_work', []).append({**rec, 'receipt_artifact': a['artifact_id']})
        return rec

    def register_release(self, task, root, options=None):
        from .core import GuardError, js, digest
        from .release_checks import check_release
        options = options or {}; root = Path(root).resolve()
        report = check_release(root, **options)
        if not report['passed']: raise GuardError('Release reference/content gate failed: '+js(report))
        manifest = root / options.get('manifest_path', 'MANIFEST_SHA256.json')
        sha = digest(manifest.read_bytes())
        profile_sha=digest(js(options).encode());report.update(check_options=options,check_profile_sha256=profile_sha)
        with self._mutate(task) as m:
            self._accepted_gate(m)
            if any(s['status'] in ('started', 'confirmed', 'running') for s in m['submissions']): raise GuardError('Active/unknown submission prevents release')
            if any(x['status'] == 'open' and x['blocking'] for x in m['open_issues']): raise GuardError('Open blocker prevents release')
            prior = next((x for x in m.get('releases', []) if x['manifest_sha256'] == sha and x['root'] == str(root)
                          and x.get('check_profile_sha256')==profile_sha), None)
            if prior: return prior
            if m['status'] == 'DELIVERED': raise GuardError('Delivered task is immutable')
            a = self._artifact(m, js(report).encode(), 'evidence', 'codex', 'release_check.json', [])
            rec = {'release_id': 'REL-'+sha[:16], 'root': str(root), 'manifest_sha256': sha, 'check_artifact': a['artifact_id'],
                   'check_profile_sha256':profile_sha,
                   'status': 'structurally_registered', 'assurance': 'structure_only', 'delivery_eligible': False, 'user_delivery': 'not_yet_confirmed'}
            m.setdefault('releases', []).append(rec)
            self._event(m, 'RELEASE_REGISTERED', 'orchestrator', 'Verified physical files before idempotent release registration', a['artifact_id'])
        return rec


    def _release_accepted_input(self, m, entry):
        """Resolve accepted authority from state/artifacts, never declared hashes."""
        from .core import GuardError, digest
        task = entry.get('input_task_id')
        origin = m if task == m['task_id'] else self.get(task)
        self._material_gate(origin)
        self._canonical_review_binding_gate(origin)
        self._review_cycle_gate(origin)
        self._document_stage_gate(origin)
        aid = entry.get('input_artifact_id')
        acceptance = entry.get('acceptance_id')
        candidate = self._fresh(origin, origin.get('candidate'))
        allowed = {candidate['artifact_id']}
        for rec in origin.get('candidate_files', []):
            if rec.get('candidate_artifact_id') != candidate['artifact_id']:
                raise GuardError('Release input frozen for a different candidate')
            allowed.add(rec['artifact_id'])
        if origin is m:
            validations = [v for v in m['validation'] if v['candidate_artifact_id'] == candidate['artifact_id']]
            if not validations or not validations[-1]['passed']:
                raise GuardError('Release requires current passing candidate validation')
            accepted_id = m.get('review') if m['routing']['review_required'] else validations[-1]['artifact_id']
            if not accepted_id or acceptance != accepted_id:
                raise GuardError('Release acceptance is not current exact review/validation')
            record = self._fresh(m, accepted_id)
            if m['routing']['review_required']:
                review = json.loads(self.read_artifact(m, accepted_id))
                if review.get('candidate_artifact_id') != candidate['artifact_id'] or (review.get('blocking') and not m.get('review_cycle')):
                    raise GuardError('Release review is not an accepted current candidate')
                if m.get('review_cycle'):self._review_cycle_gate(m)
        else:
            accepted = next((r for r in m.get('accepted_work', []) if r.get('task_id') == task
                             and r.get('receipt_artifact') == acceptance), None)
            if not accepted:
                raise GuardError('Release input task is not explicitly accepted work')
            record = self._fresh(m, acceptance)
            accepted_payload = json.loads(self.read_artifact(m, acceptance))
            if not isinstance(accepted_payload, dict) or any(accepted_payload.get(k) != accepted.get(k) for k in ('task_id', 'artifact_id', 'sha256', 'review_artifact', 'candidate_origin')):
                raise GuardError('Release acceptance receipt does not name this accepted child scope')
            if origin['status'] != 'DELIVERED' or not self.audit(task)['passed']:
                raise GuardError('Accepted release origin no longer delivered/audited')
            final = self._fresh(origin, origin['final_output'])
            if final['hash'] != accepted['sha256'] or final['hash'] != candidate['hash']:
                raise GuardError('Accepted release origin candidate changed')
            allowed.add(final['artifact_id'])
        if aid not in allowed:
            raise GuardError('Release input was not in the exact accepted candidate scope')
        artifact = self._fresh(origin, aid)
        data = self.read_artifact(origin, aid)
        if entry.get('input_sha256') != artifact['hash'] or digest(data) != artifact['hash']:
            raise GuardError('Release declared input differs from authoritative accepted bytes')
        authority = {'acceptance_id': acceptance, 'acceptance_sha256': record['hash'],
                     'candidate_artifact_id': candidate['artifact_id'], 'candidate_sha256': candidate['hash'],
                     'input_task_id': task, 'input_artifact_id': aid, 'input_sha256': artifact['hash']}
        from .release_binding import accepted_source_identities
        if origin.get('review_cycle'):
            authority['effective_review_cycle']=self._review_cycle_gate(origin)
            authority['adjudication_evidence']=[{k:x.get(k) for k in ('artifact_id','sha256','report_sha256','candidate_sha256')} for x in self._review_cycle_state(origin)['adjudications']]
        authority['accepted_source_identities'] = accepted_source_identities(self.read_artifact(origin, candidate['artifact_id']))
        return data, authority


    def _check_release_binding(self, m, release, bindings, zip_path=None, archive_prefix=''):
        from .core import GuardError, digest
        from .release_checks import check_release
        from .release_binding import inspect_binding
        import zipfile
        from xml.etree.ElementTree import ParseError
        check = self._fresh(m, release['check_artifact'])
        options = json.loads(self.read_artifact(m, check['artifact_id']))['check_options']
        try:
            report = check_release(release['root'], **options)
            if not report['passed']:
                raise GuardError('Current release tree check failed')
            result = inspect_binding(release['root'], options.get('manifest_path', 'MANIFEST_SHA256.json'),
                bindings, lambda entry: self._release_accepted_input(m, entry), options,
                zip_path=zip_path, archive_prefix=archive_prefix)
            if result['manifest_sha256'] != release['manifest_sha256']:
                raise GuardError('Registered manifest changed; register a new current release')
            return result
        except (ValueError, OSError, zipfile.BadZipFile, ParseError) as exc:
            raise GuardError('Accepted release binding failed: ' + str(exc)) from exc

    def bind_release(self, task, release_id, bindings, zip_path=None, archive_prefix=''):
        """Bind every non-manifest file to an exact accepted scope and replay.

        identity_v1 is byte exact. utf8/docx_reference_map_v1 are restricted to
        exact registered source IDs. Receipts are evidence, outside the tree.
        """
        from .core import GuardError, js, digest, now
        with self._mutate(task) as m:
            self._accepted_gate(m)
            if m['status'] == 'DELIVERED':
                raise GuardError('Delivered task is immutable')
            if any(s['status'] in ('started', 'confirmed', 'running') for s in m['submissions']):
                raise GuardError('Active/unknown submission prevents binding')
            release = next((r for r in m.get('releases', []) if r['release_id'] == release_id), None)
            if not release:
                raise GuardError('Unknown registered release')
            result = self._check_release_binding(m, release, bindings, zip_path, archive_prefix)
            contract_hash = digest(js(result).encode())
            if release.get('binding_sha256') == contract_hash:
                self._fresh(m, release['binding_artifact'])
                return release
            inputs = [release['check_artifact']]
            inputs += [e['acceptance_id'] for e in bindings if e['acceptance_id'] not in inputs]
            receipt = self._artifact(m, js(result).encode(), 'evidence', 'codex', 'release_binding.json', inputs)
            release.update(status='locally_published', assurance='exact_accepted_scope', delivery_eligible=True,
                binding_artifact=receipt['artifact_id'], binding_sha256=receipt['hash'], bound_at=now(),
                bound_candidate=m.get('candidate'), bound_candidate_sha256=self._fresh(m, m['candidate'])['hash'] if m.get('candidate') else None)
            self._event(m, 'RELEASE_BOUND', 'orchestrator', 'Accepted exact file scope and replay bound to current manifest and ZIP', receipt['artifact_id'])
            return release

    def _release_delivery_gate(self, m):
        """Actual final delivery checks registered releases, not just a helper API."""
        from .core import GuardError, js, digest
        for release in m.get('releases', []):
            if release.get('assurance') != 'exact_accepted_scope' or not release.get('binding_artifact'):
                raise GuardError('Structural release registration alone cannot be delivered; exact binding required')
            if release.get('bound_candidate') != m.get('candidate') or release.get('bound_candidate_sha256') != self._fresh(m, m['candidate'])['hash']:
                raise GuardError('Release binding belongs to another parent candidate')
            receipt = self._fresh(m, release['binding_artifact'])
            original = json.loads(self.read_artifact(m, receipt['artifact_id']))
            z = original.get('zip') or {}
            current = self._check_release_binding(m, release, original['bindings'], z.get('path'), z.get('archive_prefix', ''))
            if digest(js(current).encode()) != release.get('binding_sha256'):
                raise GuardError('Accepted release/ZIP/current authority differs from bound receipt')

    def current_release_status(self, task, release_id):
        """Current authority projection; never alters historical fact statuses."""
        from .core import GuardError, now
        m = self.get(task)
        r = next((x for x in m.get('releases', []) if x['release_id'] == release_id), None)
        if not r:
            raise GuardError('Unknown release')
        return {'schema_version': 'current_release_status_v1', 'task_id': task, 'release_id': release_id,
                'task_status': m['status'], 'release_status': r['status'], 'assurance': r.get('assurance', 'legacy_structure_only'),
                'candidate_artifact_id': r.get('bound_candidate'), 'candidate_sha256': r.get('bound_candidate_sha256'),
                'acceptance_binding_artifact': r.get('binding_artifact'), 'generated_at': now(),
                'accepted_work': m.get('accepted_work', []), 'historical_task_entry': str(self.task_path(task)),
                'user_delivery': r.get('user_delivery', 'not_yet_confirmed'),
                'factual_status_policy': 'Accepted output does not certify underlying unknown facts; original semantic/historical records remain unchanged.'}
