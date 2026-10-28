"""Opt-in, durable business review cycles on the controller's existing tasks store.

All underscored mutation hooks run inside the caller's existing IMMEDIATE
transaction. There is no provider-call quota or second state database here.
"""
import copy
import hashlib
import json
from datetime import datetime

CONTRACT_VERSION = 1
_AUTOMATIC_ROUNDS = 2
_FIELDS = {'version','unit_id','scope_id','scope_item_ids','dependency_map','user_rules',
           'exceptions','required_source_artifacts','source_read_allowed','review_role',
           'scope_authorization'}
_SUBSTANTIVE = {'course','credit','unit','requiredness','route','version','group','scope',
                'translation','source_meaning','conflict','omission','semantic','evidence_gap',
                'unverified','source_limit','handoff'}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _strings(value, label, nonempty=False):
    if not isinstance(value,list) or any(not isinstance(x,str) or not x.strip() for x in value) or len(value)!=len(set(value)) or (nonempty and not value):
        raise ValueError(label+' must contain unique nonempty strings')
    return value


def check_review_cycle_contract(spec):
    """Validate opt-in config, rejecting unknown controls rather than ignoring them."""
    c=spec.get('review_cycle_contract')
    if c is None:return None
    if not isinstance(c,dict) or set(c)-_FIELDS:raise ValueError('Unknown review_cycle_contract field')
    if c.get('version')!=CONTRACT_VERSION:raise ValueError('Unsupported review_cycle_contract version')
    for k in ('unit_id','scope_id'):
        if not isinstance(c.get(k),str) or not c[k].strip():raise ValueError('Review cycle needs '+k)
    ids=_strings(c.get('scope_item_ids'),'scope_item_ids',True)
    for k in ('user_rules','exceptions','required_source_artifacts'):_strings(c.get(k,[]),k)
    deps=c.get('dependency_map',{})
    if not isinstance(deps,dict) or not set(deps)<=set(ids):raise ValueError('Invalid dependency_map keys')
    for k,v in deps.items():
        _strings(v,'dependency_map.'+k)
        if not set(v)<=set(ids) or k in v:raise ValueError('Invalid dependency_map dependency')
    if c.get('source_read_allowed') is not True:raise ValueError('Independent review must be allowed to read its required originals')
    if c.get('review_role','secondary_executor')!='secondary_executor':raise ValueError('Content review uses the independent secondary_executor role')
    auth=c.get('scope_authorization')
    if auth is not None:
        if not isinstance(auth,dict) or not all(isinstance(auth.get(k),str) and auth[k].strip() for k in ('authority','reason','evidence_artifact_id')):
            raise ValueError('Scope expansion needs explicit authority, reason and evidence_artifact_id')
        if auth.get('authority')!='explicit_user':raise ValueError('Only explicit user authorization can expand stable scope')
    return copy.deepcopy(c)


def statement_versions(candidate):
    """Exact statement identity; no inferred equivalence from similar wording."""
    if not isinstance(candidate,dict):raise ValueError('Review candidate must be an object')
    rows=candidate.get('statements',candidate.get('claims',candidate.get('items',[])))
    if not isinstance(rows,list):raise ValueError('Candidate statement list is required')
    out={}
    for r in rows:
        ident=r.get('claim_id',r.get('item_id',r.get('id'))) if isinstance(r,dict) else None
        if not isinstance(ident,str) or not ident or ident in out:raise ValueError('Candidate needs unique stable statement IDs')
        out[ident]=_hash({k:v for k,v in r.items() if k not in ('semantic_sha','semantic_sha256')})
    return out


def candidate_context(candidate, canonical=False):
    # Transport identities do not change facts. Everything outside the stable
    # statements remains conservative context: prose, cells, routes, views and
    # shared-budget declarations cannot drift unnoticed.
    excluded={'task_id','submission_id','work_item_id','round_id','statements','claims','items'}
    if canonical:
        # These exact generated projections are separately checked against the
        # single master by _canonical_review_binding_gate, never by author assertion.
        excluded|={'canonical_draft_sha256','canonical_inventory_sha256','canonical_views','canonical_view_documents'}
    return _hash({k:v for k,v in candidate.items() if k not in excluded})


def _pointer(data, pointer):
    if not isinstance(pointer,str) or not pointer.startswith('/'):raise ValueError('Exact JSON pointer required')
    for bit in pointer.split('/')[1:]:
        bit=bit.replace('~1','/').replace('~0','~')
        data=data[int(bit)] if isinstance(data,list) else data[bit]
    return data


class ReviewCycleMixin:
    """New tasks opt in through spec.review_cycle_contract; old snapshots are inert."""
    def _review_cycle_evidence(self,m,artifact_id):
        # Historical review/candidate bytes remain audit evidence after a new
        # candidate supersedes them. They never count as the current candidate;
        # exact current statement/source/dependency bindings are checked below.
        a=self.artifact(m,artifact_id)
        self.read_artifact(m,artifact_id)
        return a

    def _review_cycle_contract(self,m):
        return m.get('spec',{}).get('review_cycle_contract')

    def _review_cycle_peers(self,c,current=None):
        return [t for t in self.list_tasks() if t['task_id']!=current and t.get('run_mode')==self.mode
                and (t.get('spec',{}).get('review_cycle_contract') or {}).get('unit_id')==c['unit_id']]

    def _review_cycle_create_guard(self,spec):
        from .core import GuardError
        try:c=check_review_cycle_contract(spec)
        except ValueError as e:raise GuardError('CONFIG_ERROR: '+str(e)) from e
        if c is None:return
        prev=spec.get('previous_round_id')
        if prev:
            old=self.get(prev).get('spec',{}).get('review_cycle_contract')
            if old and old['unit_id']!=c['unit_id']:raise GuardError('Previous business unit identity cannot be renamed')
        peers=self._review_cycle_peers(c)
        if not peers:return
        anchor=self.get(peers[0].get('review_cycle_anchor',peers[0]['task_id']))
        state=anchor.get('review_cycle_state')
        if not state:raise GuardError('Review cycle anchor missing; recover before creating followup')
        for t in peers:
            if self._review_cycle_inflight(t):raise GuardError('Inspect existing uncertain or running review; never resend under another Task ID')
        additions=set(c['scope_item_ids'])-set(state['scope_item_ids'])
        if additions and not c.get('scope_authorization'):raise GuardError('New business scope needs explicit user authorization; existing rounds are retained')
        # A reduced or relabelled scope still attaches to the original cycle.
        # Creation itself never consumes a review or bypasses dispatch checks.

    @staticmethod
    def _review_cycle_inflight(m):
        return m.get('status')=='SUBMISSION_UNCERTAIN' or any(s.get('status') in ('started','confirmed','running') for s in m.get('submissions',[]))

    def _review_cycle_attach(self,m):
        c=self._review_cycle_contract(m)
        if not c:return
        peers=self._review_cycle_peers(c,m['task_id'])
        anchor=peers[0].get('review_cycle_anchor',peers[0]['task_id']) if peers else m['task_id']
        m['review_cycle_anchor']=anchor
        m['review_cycle']={'anchor_task_id':anchor,'unit_id':c['unit_id']}
        if anchor==m['task_id']:
            m['review_cycle_state']={'version':1,'cycle_id':'REVIEW-'+_hash({'unit_id':c['unit_id']})[:20],
                'anchor_task_id':anchor,'unit_id':c['unit_id'],'scope_id':c['scope_id'],
                'scope_item_ids':list(c['scope_item_ids']),'rounds':[], 'issues':{},
                'passed_items':{},'adjudications':[],'authorizations':[], 'scope_expansions':[],
                'requests':[],'waiting_user':False,'content_frozen':None}
        else:
            state=self._review_cycle_state(m)
            additions=sorted(set(c['scope_item_ids'])-set(state['scope_item_ids']))
            if additions:
                auth=c['scope_authorization']
                parent=m.get('parent_task_id')
                # Authority must be an existing frozen evidence artifact, never
                # only a text label in an author's new spec.
                if not parent:raise ValueError('Scope authorization must reference parent evidence')
                proof=self._fresh(self.get(parent),auth['evidence_artifact_id'])
                if proof['role'] not in ('request','evidence') or proof['provider'] not in ('user','codex'):raise ValueError('Scope expansion needs explicit user instruction evidence')
                state['scope_item_ids'].extend(additions)
                state['scope_expansions'].append({**auth,'new_item_ids':additions,'task_id':m['task_id']})
                self._review_cycle_store(m,state)

    def _review_cycle_state(self,m):
        anchor=m.get('review_cycle_anchor')
        if not anchor:raise ValueError('Review cycle must be attached before use')
        src=m if anchor==m['task_id'] else self.get(anchor)
        return copy.deepcopy(src['review_cycle_state'])

    def _review_cycle_store(self,m,state):
        """Persist anchor in the same SQL transaction as dispatch/receive."""
        from .core import js,now
        anchor=m['review_cycle_anchor']
        if anchor==m['task_id']:
            m['review_cycle_state']=state
        else:
            other=self.get(anchor);other['review_cycle_state']=state
            other['updated_at']=now();other['revision']+=1
            self.db.execute('UPDATE tasks SET body=? WHERE id=?',(js(other),anchor))
        m['review_cycle_snapshot']={'cycle_id':state['cycle_id'],'anchor_task_id':anchor,
                                    'rounds':len(state['rounds']),'waiting_user':state['waiting_user']}

    def register_review_inputs(self,task,inputs):
        from .core import GuardError,now,js
        with self._mutate(task) as m:
            if not self._review_cycle_contract(m):raise GuardError('Task has no review cycle contract')
            if any(s.get('review_cycle') for s in m['submissions']):raise GuardError('Review inputs are frozen once a submission is reserved')
            if not isinstance(inputs,list) or not inputs:raise GuardError('Required original input inventory is empty')
            checked=[];errors=[];seen=set()
            for row in inputs:
                if not isinstance(row,dict) or set(row)-{'artifact_id','read_allowed','required','purpose'}:
                    errors.append('Invalid review input record');continue
                aid=row.get('artifact_id')
                if aid in seen:errors.append('Duplicate required original: '+str(aid));continue
                seen.add(aid)
                try:
                    a=self._fresh(m,aid)
                    if aid not in m['source_files'] or a['role']!='source':raise GuardError('Input is not a registered source')
                    if row.get('read_allowed') is not True:raise GuardError('Read permission missing')
                    checked.append({**row,'sha256':a['hash'],'logical_name':a['logical_name'],'required':row.get('required',True)})
                except (ValueError,KeyError) as e:errors.append(str(aid)+': '+str(e))
            expected=set(self._review_cycle_contract(m).get('required_source_artifacts',[]))|set(m['spec'].get('input_artifact_ids',m['source_files']))
            if not expected<=seen:errors.append('Missing required originals: '+', '.join(sorted(expected-seen)))
            if errors:raise GuardError('Review input preflight failed: '+'; '.join(errors))
            m['review_inputs']={'inputs':checked,'registered_at':now()}
            rec=self._artifact(m,js(m['review_inputs']).encode(),'evidence','local','review-inputs.json',list(seen))
            m['review_inputs']['artifact_id']=rec['artifact_id']
            self._event(m,'REVIEW_INPUTS_REGISTERED','orchestrator','Original identities and read permissions frozen',rec['artifact_id'])
        return self.get(task)['review_inputs']

    def _review_cycle_reconciled(self,m,submission):
        """Affirmatively not-sent intent remains evidence, not a business review."""
        if not self._review_cycle_contract(m) or submission.get('phase')!='reviewer':return
        if submission.get('status') not in ('not_submitted','cancelled'):return
        state=self._review_cycle_state(m)
        row=next((r for r in state['rounds'] if r['submission_id']==submission['submission_id']),None)
        if row:
            if row.get('confirmed_at'):raise ValueError('A confirmed external review cannot be relabelled not sent')
            state.setdefault('reconciled_intents',[]).append({**row,'reconciliation_status':submission['status']})
            state['rounds']=[r for r in state['rounds'] if r['submission_id']!=submission['submission_id']]
            for authority in state['authorizations']:
                if authority.get('used_by')==submission['submission_id']:authority['used_by']=None
            self._review_cycle_store(m,state)

    def _review_cycle_candidate(self,m):
        from .core import GuardError
        a=self._fresh(m,m['candidate'])
        try:body=json.loads(self.read_artifact(m,a['artifact_id']));versions=statement_versions(body)
        except (ValueError,TypeError) as e:raise GuardError('Review cycle needs structured exact candidate: '+str(e)) from e
        return a,body,versions

    def _review_cycle_sources(self,m):
        from .core import GuardError
        inputs=m.get('review_inputs',{}).get('inputs',[]);errors=[]
        if not inputs:errors.append('Required original inventory not registered')
        for row in inputs:
            try:
                a=self._fresh(m,row['artifact_id'])
                if a['hash']!=row['sha256']:raise GuardError('Original source version changed')
                if row.get('read_allowed') is not True:raise GuardError('Required source cannot be read')
            except (ValueError,KeyError) as e:errors.append(str(row.get('artifact_id'))+': '+str(e))
        if self._review_cycle_contract(m).get('source_read_allowed') is not True:errors.append('Contract forbids reading originals')
        if errors:raise GuardError('Review input preflight failed: '+'; '.join(errors))
        contract=self._review_cycle_contract(m)
        return _hash({'originals':sorted((r['logical_name'],r['sha256']) for r in inputs),
                      'user_rules':contract.get('user_rules',[]),'exceptions':contract.get('exceptions',[]),
                      'dependency_map':contract.get('dependency_map',{})})

    def _review_cycle_dependencies(self,m):
        deps=copy.deepcopy(self._review_cycle_contract(m).get('dependency_map',{}))
        if m['spec'].get('canonical_workflow_v1') and m.get('candidate'):
            body=json.loads(self.read_artifact(m,m['candidate']))
            for row in body.get('statements',[]):
                ident=row['claim_id'];entity=row.get('canonical_item',row.get('canonical_group',{}))
                related=set(entity.get('group_ids',[]))|{x['item_id'] for x in entity.get('members',[])}
                deps[ident]=sorted(set(deps.get(ident,[]))|related)
        return deps

    def _review_context(self,m,body):
        return self._assignment_projection_context(m) if m.get('assignment') else candidate_context(body,m['spec'].get('canonical_workflow_v1',False))

    def _review_cycle_item_basis(self,m,versions,source_sha,context,item_ids):
        deps=self._review_cycle_dependencies(m)
        affected=set(item_ids)
        while True:
            expanded=affected|{d for i in affected for d in deps.get(i,[])}
            if expanded==affected:break
            affected=expanded
        scoped=self._assignment_source_versions(m,sorted(affected)) if m.get('assignment') else None
        return _hash({'versions':{i:versions.get(i) for i in sorted(affected)},
                      'source':scoped if scoped is not None else source_sha,'candidate_context':context,
                      'dependency_map':{i:deps.get(i,[]) for i in sorted(affected)}})

    def _review_cycle_plan(self,m,state,versions,source_sha):
        from .core import GuardError
        c=self._review_cycle_contract(m);scope=set(c['scope_item_ids'])
        if not scope<=set(versions):raise GuardError('Candidate omits stable review items: '+', '.join(sorted(scope-set(versions))))
        completed=[r for r in state['rounds'] if r.get('received_at')]
        latest=completed[-1] if completed else None
        if not latest:
            explicit=m['spec'].get('document_review_scope')
            if explicit is not None and set(explicit)!=scope:raise GuardError('First independent review must cover the whole stable scope')
            if scope!=set(state['scope_item_ids']):raise GuardError('First independent review must cover the whole stable scope')
            return {'mode':'full','changed_ids':sorted(scope),'review_scope':sorted(scope),'inherited_ids':[]}
        changed={i for i in state['scope_item_ids'] if versions.get(i)!=latest['item_versions'].get(i)}
        current_body=json.loads(self.read_artifact(m,m['candidate']))
        item_sources=self._assignment_source_versions(m,sorted(scope)) if m.get('assignment') else None
        if latest['source_sha256']!=source_sha:
            prior=latest.get('item_source_versions')
            if item_sources is not None and prior is not None:changed.update(i for i in scope if item_sources[i]!=prior.get(i))
            else:changed.update(scope)
        context=self._assignment_projection_context(m) if m.get('assignment') else self._review_context(m,current_body)
        if latest.get('candidate_context_sha256')!=context:changed.update(scope)
        # Dependents are transitive: a group denominator or route change must
        # invalidate dependent claims and both view mappings supplied in scope.
        affected=set(changed);deps=self._review_cycle_dependencies(m)
        while True:
            more={i for i,requires in deps.items() if set(requires)&affected}
            if more<=affected:break
            affected|=more
        # Old independent gaps remain in effective school state; they do not
        # force re-review of unchanged local work in this incremental packet.
        explicit=m['spec'].get('document_review_scope')
        required=affected&scope
        selected=set(explicit) if explicit is not None else required
        if not required<=selected or not selected<=scope:raise GuardError('Incremental scope omits changed items or their dependencies')
        return {'mode':'incremental','changed_ids':sorted(changed&scope),'review_scope':sorted(selected),
                'inherited_ids':sorted(scope-selected)}

    def _review_cycle_guard(self,m,stage,submission=None,phase='reviewer'):
        """Called under existing transaction at package/prepare/begin/confirm/recover.

        prepare reserves an ordinal on the ticket; begin rechecks peers. The
        ordinal becomes a business round only with an uncertain send intent.
        A cancelled/prepared ticket never manufactures a completed review.
        """
        from .core import GuardError
        if not self._review_cycle_contract(m) or phase!='reviewer' or (submission and submission.get('phase')!='reviewer'):return None
        state=self._review_cycle_state(m)
        if stage=='recover':
            self._review_cycle_effective_open_issues(m,state)
            return {'waiting_user':state['waiting_user'],'automatic_resend_allowed':False}
        peers=[m]+self._review_cycle_peers(self._review_cycle_contract(m),m['task_id'])
        for t in peers:
            for s in t['submissions']:
                if s.get('phase')=='reviewer' and s.get('status') in ('started','confirmed','running') and not (submission and s['submission_id']==submission['submission_id']):
                    raise GuardError('Inspect existing review submission; uncertain/running send cannot be reset')
        candidate,body,versions=self._review_cycle_candidate(m);source_sha=self._review_cycle_sources(m)
        plan=self._review_cycle_plan(m,state,versions,source_sha)
        if not plan['review_scope']:
            raise GuardError('No changed or unresolved scope requires another independent review; freeze the accepted content')
        # Reservations across processes are visible because caller holds the
        # existing IMMEDIATE transaction, including create/prepare/begin.
        reserved=[]
        for t in peers:
            for s in t['submissions']:
                rc=s.get('review_cycle')
                if s.get('status')=='prepared' and rc and not (submission and s['submission_id']==submission['submission_id']):reserved.append(s)
        if reserved:raise GuardError('Another prepared review already reserves this stable business cycle')
        prior_rounds=[r for r in state['rounds'] if r.get('kind')=='substantive']
        ordinal=len(prior_rounds)+1
        if submission and submission.get('review_cycle'):
            own=submission['review_cycle'];ordinal=own['round_no']
            if own['candidate_sha256']!=candidate['hash'] or own['source_sha256']!=source_sha:raise GuardError('Prepared review candidate/source binding changed')
        current_sid=submission.get('submission_id') if submission else None
        ownround=next((r for r in state['rounds'] if r.get('submission_id')==current_sid),None)
        if ownround:ordinal=ownround['round_no']
        auth=next((a for a in state['authorizations'] if a['round_no']==ordinal and not a.get('used_by')),None)
        if ordinal>_AUTOMATIC_ROUNDS and not ownround and not auth:
            raise GuardError('REVIEW_LIMIT: third substantive review needs a recorded explicit user authorization')
        if auth and not set(plan['review_scope'])<=set(auth['affected_ids']):raise GuardError('Additional review exceeds the explicitly authorized affected scope')
        if ownround and stage in ('begin','prepare'):raise GuardError('Review send intent already exists; inspect original conversation')
        info={'cycle_id':state['cycle_id'],'round_no':ordinal,'kind':'substantive',
              'candidate_sha256':candidate['hash'],'source_sha256':source_sha,
              'item_versions':versions,'candidate_context_sha256':self._assignment_projection_context(m) if m.get('assignment') else self._review_context(m,body),**plan}
        if m.get('assignment'):info['item_source_versions']=self._assignment_source_versions(m,sorted(versions))
        if submission is not None:
            submission['review_cycle']=info
            if stage in ('begin','confirm') and not ownround:
                from .core import now
                state['rounds'].append({**copy.deepcopy(info),'task_id':m['task_id'],
                    'submission_id':submission['submission_id'],'provider':submission['provider'],
                    'intent_at':now(),'confirmed_at':None,'received_at':None})
                if auth:auth['used_by']=submission['submission_id']
            if stage=='confirm':
                row=next(r for r in state['rounds'] if r['submission_id']==submission['submission_id'])
                row['confirmed_at']=submission.get('confirmed_at')
            self._review_cycle_store(m,state)
        return info

    def review_cycle_packet(self,task):
        """Neutral reviewer contract; it contains no producer verdict or old approval."""
        m=self.get(task)
        if not self._review_cycle_contract(m):return None
        state=self._review_cycle_state(m);c=self._review_cycle_contract(m)
        a,body,versions=self._review_cycle_candidate(m);source=self._review_cycle_sources(m)
        plan=self._review_cycle_plan(m,state,versions,source)
        return {'version':1,'cycle_id':state['cycle_id'],'unit_id':c['unit_id'],
            'stable_scope_id':state['scope_id'],'scope_item_ids':state['scope_item_ids'],
            'candidate_artifact_id':a['artifact_id'],'candidate_sha256':a['hash'],
            'user_rules':c.get('user_rules',[]),'user_exceptions':c.get('exceptions',[]),
            'required_originals':m['review_inputs']['inputs'],**plan,
            'open_issue_ids':[i for i,x in state['issues'].items() if x['status']=='open'],
            'role_boundary':'Read authorized originals and candidate only. No Word/WPS/Edge control, cleanup, source edits, or directory replacement. Return all scoped content findings; visual QA belongs to Codex.',
            'output_contract':'Use the task result_protocol complete schema. Include one passed/error/unverified check per scoped stable item; evidence and exact locations are required. Do not infer missing facts.'}

    def _review_cycle_effective_open_issues(self,m,state=None):
        if not self._review_cycle_contract(m):return m.get('open_issues',[])
        state=state or self._review_cycle_state(m)
        old=[i for i in m['open_issues'] if not i.get('review_cycle_issue')]
        effective=[]
        for ident,i in state['issues'].items():
            effective.append({'id':ident,'kind':i['kind'],'status':i['status'], 'blocking':i['blocking'],
                'detail':i['detail'],'evidence':i['report_artifact_id'],'candidate_artifact_id':i['candidate_artifact_id'],
                'review_cycle_issue':True,'affected_ids':i['affected_ids'],'origin_task_id':i['task_id']})
        m['open_issues']=old+effective
        return effective

    def _review_cycle_received(self,m,submission,report_artifact,data):
        from .core import GuardError,now
        if not self._review_cycle_contract(m) or submission.get('phase')!='reviewer':return False
        state=self._review_cycle_state(m);rc=submission.get('review_cycle')
        if not rc:raise GuardError('Review response has no durable business cycle reservation')
        row=next((r for r in state['rounds'] if r['submission_id']==submission['submission_id']),None)
        if not row:raise GuardError('Review response has no persisted send intent')
        current,body,versions=self._review_cycle_candidate(m)
        if rc['candidate_sha256']!=current['hash']:raise GuardError('Stale review candidate')
        expected=set(rc['review_scope']);covered=set();passed=set();findings=[];notices=[]
        offline=m['spec'].get('result_protocol')=='offline_document_v1'
        for category in ('passed_checks','confirmed_errors','suspicions','unverified'):
            for x in data.get(category,[]):
                if not isinstance(x,dict):continue
                item=x.get('claim_id',x.get('item_id'));affected=x.get('affected_ids') or ([item] if item else [])
                if item in expected:
                    covered.add(item)
                    if category=='passed_checks':passed.add(item)
                if category!='passed_checks':
                    # Offline protocol explicitly permits general limitations
                    # outside scoped statement coverage (hash recomputation,
                    # visual responsibility, absent optional metadata). Preserve
                    # them as notices, never as invented whole-scope failures.
                    if offline and category in ('suspicions','unverified') and not item and not affected and x.get('blocking') is not True:
                        notices.append((category,x))
                    else:findings.append((category,x,list(affected)))
        for x in data.get('claim_checks',[]):
            item=x.get('claim_id')
            if item in expected:
                covered.add(item)
                if x.get('result')=='verified' and not x.get('blocking'):passed.add(item)
                elif x.get('blocking'):
                    findings.append(('confirmed_errors',{'id':x.get('issue_id',item),**x},[item]))
        # No empty CE shortcut: complete positive/negative coverage is required.
        if not expected<=covered:raise GuardError('Independent report omits scoped items: '+', '.join(sorted(expected-covered)))
        if any(x.get('producer')==submission['provider'] for x in body.get('statements',body.get('claims',[])) if x.get('claim_id') in expected):
            raise GuardError('Self-produced statements cannot be accepted as independent review')
        # Complete per-item coverage was checked above before any generic
        # limitation can be treated as a notice. Original raw blocking below
        # still requires a concrete scoped/explicit finding or remains blocking.
        for category,detail in notices:
            notice_id='NOTICE-'+_hash({'category':category,'detail':detail,'report_sha256':report_artifact['hash']})[:20]
            if not any(n['id']==notice_id for n in state.setdefault('notices',[])):
                state['notices'].append({'id':notice_id,'category':category,'detail':copy.deepcopy(detail),
                    'task_id':m['task_id'],'report_artifact_id':report_artifact['artifact_id'],
                    'report_sha256':report_artifact['hash'],'candidate_sha256':current['hash'],'observed_at':now(),
                    'counts_as_scoped_coverage':False,'underlying_facts_verified':False})
        failed_ids={i for _,_,ids in findings for i in ids}
        passed-=failed_ids
        if data.get('blocking') and not findings:
            findings.append(('unverified',{'id':'RAW-UNSPECIFIED-BLOCK','description':'Original independent report remains blocking without a precise issue list'},list(expected)))
        for item in passed:
            state['passed_items'][item]={'semantic_sha256':versions[item],'source_sha256':rc['source_sha256'],
                'report_sha256':report_artifact['hash'],'report_artifact_id':report_artifact['artifact_id'],
                'dependency_sha256':self._review_cycle_item_basis(m,versions,rc['source_sha256'],self._review_context(m,body),[item]),
                'task_id':m['task_id'],'provider':submission['provider'],'reviewed_at':now(),
                'content_check_status':'independently_checked','underlying_fact_status':next((x.get('underlying_fact_status','not_asserted') for x in data.get('passed_checks',[]) if isinstance(x,dict) and x.get('claim_id',x.get('item_id'))==item),'not_asserted')}
        for category,x,affected in findings:
            ident=x.get('issue_id',x.get('id',x.get('claim_id')))
            if not ident:ident='ISSUE-'+_hash({'category':category,'location':x.get('location'),'description':x.get('description',x.get('reason'))})[:16]
            if not affected:affected=list(expected)
            if not set(affected)<=set(state['scope_item_ids']):
                raise GuardError('New out-of-scope finding needs an explicitly registered business scope')
            dependency=self._review_cycle_item_basis(m,versions,rc['source_sha256'],self._review_context(m,body),affected)
            prior=state['issues'].get(ident)
            history=(prior or {}).get('observations',[])
            observation={'task_id':m['task_id'],'report_artifact_id':report_artifact['artifact_id'],
                'report_sha256':report_artifact['hash'],'candidate_sha256':current['hash'],'detail':copy.deepcopy(x),'observed_at':now()}
            if not any(h['report_sha256']==observation['report_sha256'] for h in history):history.append(observation)
            # An adjudication applies only to the exact dependency version.
            closed=prior and prior['status']!='open' and prior.get('dependency_sha256')==dependency
            state['issues'][ident]={'id':ident,'kind':x.get('category',category),'status':prior['status'] if closed else 'open',
                'blocking':x.get('blocking',True),'affected_ids':affected,'detail':copy.deepcopy(x),
                'candidate_artifact_id':current['artifact_id'],'candidate_sha256':current['hash'],
                'report_artifact_id':report_artifact['artifact_id'],'report_sha256':report_artifact['hash'],
                'task_id':m['task_id'],'dependency_sha256':dependency,'observations':history,
                'adjudication':prior.get('adjudication') if closed else None}
        # A later independent positive check can close an existing affected
        # substantive issue, without changing the original report.
        for ident,issue in state['issues'].items():
            if issue['status']=='open' and issue['report_sha256']!=report_artifact['hash'] and set(issue['affected_ids'])<=passed:
                issue.update(status='resolved_independently',resolution_report=report_artifact['artifact_id'],resolved_at=now(),
                    dependency_sha256=self._review_cycle_item_basis(m,versions,rc['source_sha256'],self._review_context(m,body),issue['affected_ids']))
        row.setdefault('first_ingested_at',now())
        row.update(received_at=row.get('received_at') or now(),last_ingested_at=now(),report_artifact_id=report_artifact['artifact_id'],report_sha256=report_artifact['hash'],
                   blocking_original=data.get('blocking'),passed_ids=sorted(passed),covered_ids=sorted(covered))
        state['waiting_user']=len(state['rounds'])>=_AUTOMATIC_ROUNDS and any(i['status']=='open' and i['blocking'] for i in state['issues'].values())
        if state['waiting_user']:
            from .core import js
            unresolved=[i for i,x in state['issues'].items() if x['status']=='open' and x['blocking']]
            if not state['requests'] or state['requests'][-1].get('open_issue_ids')!=unresolved:
                request={'requested_at':now(),'task_id':m['task_id'],'reason':'Two substantive business reviews completed; unresolved issues require bounded adjudication or explicit additional review authority',
                    'next_round':len(state['rounds'])+1,'open_issue_ids':unresolved,
                    'affected_ids':sorted({i for x in state['issues'].values() if x['status']=='open' for i in x['affected_ids']}),
                    'reports':[{'task_id':r['task_id'],'artifact_id':r.get('report_artifact_id'),'sha256':r.get('report_sha256')} for r in state['rounds'] if r.get('received_at')]}
                request_artifact=self._artifact(m,js(request).encode(),'evidence','local','review-user-request.json',[])
                state['requests'].append({**request,'artifact_id':request_artifact['artifact_id'],'sha256':request_artifact['hash']})
        self._review_cycle_store(m,state);self._review_cycle_effective_open_issues(m,state)
        m['blocking_issue']='Waiting for user adjudication; local passed scope retained' if state['waiting_user'] else None
        if any(i['status']=='open' and i['blocking'] for i in state['issues'].values()):
            self._move(m,'REVIEW_CONFLICT','REVIEW_SCOPE_BLOCKED','orchestrator','Only affected business unit remains blocked',report_artifact['artifact_id'])
        self._event(m,'BUSINESS_REVIEW_RECEIVED','reviewer','Raw report preserved; partial progress and effective issues recorded',report_artifact['artifact_id'],extra={'cycle_id':state['cycle_id'],'round_no':rc['round_no'],'passed_ids':sorted(passed),'waiting_user':state['waiting_user']})
        return True

    def request_review_adjudication(self,task,reason,affected_ids):
        from .core import GuardError,now,js
        with self._mutate(task) as m:
            state=self._review_cycle_state(m)
            if not isinstance(reason,str) or not reason.strip() or not set(affected_ids)<=set(state['scope_item_ids']):raise GuardError('Adjudication request needs reason and affected stable IDs')
            record={'requested_at':now(),'reason':reason,'affected_ids':list(affected_ids),'next_round':len(state['rounds'])+1,
                'open_issue_ids':[i for i,x in state['issues'].items() if x['status']=='open'],'task_id':task}
            a=self._artifact(m,js(record).encode(),'evidence','local','review-user-request.json',[])
            state['requests'].append({**record,'artifact_id':a['artifact_id'],'sha256':a['hash']});state['waiting_user']=True
            self._review_cycle_store(m,state)
            self._event(m,'REVIEW_USER_DECISION_REQUIRED','orchestrator',reason,a['artifact_id'])
        return record

    def authorize_review_round(self,task,request):
        """Explicit user authorization must itself be frozen evidence."""
        from .core import GuardError,now,js
        with self._mutate(task) as m:
            state=self._review_cycle_state(m)
            if not isinstance(request,dict) or not all(request.get(k) for k in ('authority','reason','affected_ids','evidence_artifact_id')):raise GuardError('Explicit review authorization is incomplete')
            evidence=self._fresh(m,request['evidence_artifact_id'])
            if evidence['role'] not in ('request','evidence') or evidence['provider'] not in ('user','codex'):raise GuardError('Authorization needs a user instruction evidence record')
            if request['authority']!='explicit_user':raise GuardError('Only explicit user authority can extend the review cycle')
            if not set(request['affected_ids'])<=set(state['scope_item_ids']):raise GuardError('Authorization scope is unknown')
            ordinal=len(state['rounds'])+1
            if any(a['round_no']==ordinal for a in state['authorizations']):raise GuardError('This round already has an authorization record')
            record={**copy.deepcopy(request),'round_no':ordinal,'evidence_sha256':evidence['hash'],'authorized_at':now(),'used_by':None}
            a=self._artifact(m,js(record).encode(),'evidence','local','review-authority.json',[evidence['artifact_id']])
            state['authorizations'].append({**record,'artifact_id':a['artifact_id']});state['waiting_user']=False
            self._review_cycle_store(m,state);self._event(m,'REVIEW_ROUND_AUTHORIZED','user',request['reason'],a['artifact_id'])
        return record

    def _review_cycle_close_mechanical(self,m,state,request):
        from .core import GuardError,now,js
        issue=state['issues'].get(request.get('issue_id'))
        if not issue or issue['status']!='open':raise GuardError('Open mechanical issue not found')
        if issue['kind'] not in ('mechanical','display','format'):raise GuardError('Substantive or unclassified issue cannot use mechanical adjudication')
        if request.get('authority_basis')!='user_confirmed_bounded_codex':raise GuardError('Bounded authority basis is required')
        origin=self.get(issue['task_id']);report=self._review_cycle_evidence(origin,issue['report_artifact_id'])
        self._review_cycle_evidence(origin,issue['candidate_artifact_id'])
        if request.get('report_sha256')!=report['hash'] or request.get('candidate_sha256')!=issue['candidate_sha256']:raise GuardError('Mechanical report/candidate hash mismatch')
        proof=request.get('evidence',{});pairs=[]
        for name in ('before','after'):
            rec=proof.get(name,{})
            task=rec.get('task_id',m['task_id'])
            if task not in (m['task_id'],issue['task_id']):raise GuardError('Mechanical proof belongs to another business task')
            owner=m if task==m['task_id'] else self.get(task)
            a=self._review_cycle_evidence(owner,rec.get('artifact_id'))
            if a['role'] not in ('evidence','source','producer') or a['hash']!=rec.get('sha256'):raise GuardError('Mechanical file identity/hash mismatch')
            pairs.append((owner,a,self.read_artifact(owner,a['artifact_id'])))
        before,after=pairs
        # Tie the proof to the challenged file, not an arbitrary equal pair.
        frozen=(origin.get('candidate_files',[]) if origin.get('candidate')==issue['candidate_artifact_id'] else origin.get('candidate_file_sets',{}).get(issue['candidate_artifact_id'],[]))
        bound_ids={x['artifact_id'] for x in frozen}|{issue['candidate_artifact_id']}
        if issue['detail'].get('file_artifact_id'):bound_ids.add(issue['detail']['file_artifact_id'])
        if before[1]['artifact_id'] not in bound_ids:raise GuardError('Mechanical evidence is not bound to the challenged candidate file')
        operation=proof.get('operation')
        semantic_sha=None
        if operation=='remove_utf8_bom':
            if not before[2].startswith(b'\xef\xbb\xbf') or before[2][3:]!=after[2]:raise GuardError('Only removal of one exact UTF-8 BOM is mechanical')
            semantic_sha=hashlib.sha256(after[2]).hexdigest()
        elif operation=='docx_layout':
            from .document_stage import inspect_docx,check_view_docx
            try:
                left,right=inspect_docx(before[2]),inspect_docx(after[2])
                if left['semantic_sha256']!=right['semantic_sha256']:raise GuardError('DOCX text, group boundaries or footnotes changed; independent review required')
                semantic_sha=right['semantic_sha256']
                if m['spec'].get('canonical_workflow_v1'):
                    self._canonical_review_binding_gate(m)
                    view_name=proof.get('view');view_record=m['canonical']['views'].get(view_name)
                    if not view_record:raise GuardError('Current canonical view identity required')
                    view=json.loads(self.read_artifact(m,view_record['artifact_id']))
                    if not isinstance(proof.get('title'),str) or not proof['title']:raise GuardError('Exact document title required')
                    check_view_docx(view,right,proof['title'])
            except (ValueError,KeyError,TypeError) as exc:raise GuardError('Mechanical DOCX proof failed: '+str(exc)) from exc
        else:raise GuardError('Unsupported mechanical transformation; no trimming or semantic rewrite exemption')
        current,body,versions=self._review_cycle_candidate(m);source=self._review_cycle_sources(m)
        if self._review_cycle_item_basis(m,versions,source,self._review_context(m,body),issue['affected_ids'])!=issue['dependency_sha256']:
            raise GuardError('Current content/source dependencies differ from the mechanical issue')
        if not isinstance(request.get('reason'),str) or not request['reason'].strip():raise GuardError('Mechanical adjudication reason missing')
        record={'version':1,**copy.deepcopy(request),'adjudicator':'codex','adjudication_task_id':m['task_id'],
            'origin_task_id':issue['task_id'],'adjudicated_at':now(),'comparison_semantic_sha256':semantic_sha,
            'affected_ids':issue['affected_ids'],'remaining_unreviewed_ids':sorted(set(state['scope_item_ids'])-set(state['passed_items'])),
            'original_report_preserved':True,'underlying_facts_verified':False,'native_visual_qa_verified':False}
        a=self._artifact(m,js(record).encode(),'evidence','local','review-mechanical.json',[])
        state['adjudications'].append({**record,'artifact_id':a['artifact_id'],'sha256':a['hash']})
        issue.update(status='resolved_mechanical',adjudication=a['artifact_id'],resolved_at=now())
        state['waiting_user']=len(state['rounds'])>=2 and any(x['status']=='open' and x['blocking'] for x in state['issues'].values())
        self._review_cycle_store(m,state);self._review_cycle_effective_open_issues(m,state)
        m['blocking_issue']='Other independent scope remains unresolved' if any(i['status']=='open' and i['blocking'] for i in state['issues'].values()) else None
        self._event(m,'REVIEW_MECHANICAL_RESOLVED','codex',request['reason'],a['artifact_id'])
        return record

    def adjudicate_review_issue(self,task,request):
        """Codex may dismiss only a demonstrable exact candidate quotation error.

        Semantic opinions and source-meaning disputes need independent review.
        The original report and all its CE/blocking fields remain untouched.
        """
        from .core import GuardError,now,js
        with self._mutate(task) as m:
            state=self._review_cycle_state(m)
            if request.get('action')=='close_mechanical':return self._review_cycle_close_mechanical(m,state,request)
            if request.get('action')!='dismiss_exact_misquote':raise GuardError('Local adjudication permits only exact quoted-location errors')
            issue=state['issues'].get(request.get('issue_id'))
            if not issue or issue['status']!='open':raise GuardError('Open review issue not found')
            if request.get('authority_basis')!='user_confirmed_bounded_codex':raise GuardError('Bounded authority basis is required')
            origin=self.get(issue['task_id']);report=self._review_cycle_evidence(origin,issue['report_artifact_id'])
            candidate=self._review_cycle_evidence(origin,issue['candidate_artifact_id'])
            if request.get('report_sha256')!=report['hash'] or request.get('candidate_sha256')!=candidate['hash']:raise GuardError('Adjudication report/candidate hash mismatch')
            current,current_body,versions=self._review_cycle_candidate(m);source=self._review_cycle_sources(m)
            current_dep=self._review_cycle_item_basis(m,versions,source,self._review_context(m,current_body),issue['affected_ids'])
            if issue['dependency_sha256']!=current_dep:raise GuardError('Issue dependencies changed; independent re-evaluation required')
            e=request.get('evidence',{});detail=issue['detail']
            quote=e.get('quoted_text');location=e.get('quoted_location')
            recorded_quote=detail.get('quoted_text')
            if not recorded_quote:
                text=detail.get('evidence',detail.get('description',detail.get('reason','')))
                recorded_quote=quote if isinstance(text,str) and isinstance(quote,str) and quote and quote in text else None
            if not quote or quote!=recorded_quote or location!=detail.get('quoted_location',detail.get('location')):raise GuardError('Reported exact quote and location not established')
            try:actual=_pointer(json.loads(self.read_artifact(origin,candidate['artifact_id'])),e.get('candidate_pointer'))
            except (ValueError,TypeError,KeyError,IndexError) as exc:raise GuardError('Candidate exact pointer not found') from exc
            # Pointer-to-location binding must have been supplied by the report
            # or match an exact field name. Arbitrary different-cell pointers
            # cannot demonstrate a quotation error.
            if detail.get('candidate_pointer'):
                if e.get('candidate_pointer')!=detail['candidate_pointer']:raise GuardError('Evidence pointer differs from reported location')
            elif e['candidate_pointer'].split('/')[-1].replace('~1','/').replace('~0','~')!=location:
                raise GuardError('Evidence pointer is not bound to reported location')
            if actual!=e.get('actual_value') or actual==quote:raise GuardError('Evidence does not prove an exact quotation mismatch')
            if not isinstance(request.get('reason'),str) or not request['reason'].strip():raise GuardError('Adjudication reason missing')
            record={'version':1,**copy.deepcopy(request),'adjudicator':'codex','adjudicated_at':now(),
                'adjudication_task_id':m['task_id'],'origin_task_id':issue['task_id'],'report_artifact_id':report['artifact_id'],
                'candidate_artifact_id':candidate['artifact_id'],'affected_ids':issue['affected_ids'],
                'remaining_unreviewed_ids':sorted(set(state['scope_item_ids'])-set(state['passed_items'])),
                'original_report_preserved':True,'underlying_facts_verified':False}
            receipt=self._artifact(m,js(record).encode(),'evidence','local','review-adjudication.json',[])
            state['adjudications'].append({**record,'artifact_id':receipt['artifact_id'],'sha256':receipt['hash']})
            issue.update(status='dismissed_exact_misquote',adjudication=receipt['artifact_id'],resolved_at=now())
            state['waiting_user']=len(state['rounds'])>=2 and any(x['status']=='open' and x['blocking'] for x in state['issues'].values())
            self._review_cycle_store(m,state);self._review_cycle_effective_open_issues(m,state)
            m['blocking_issue']='Other independent scope remains unresolved' if any(i['status']=='open' and i['blocking'] for i in state['issues'].values()) else None
            self._event(m,'REVIEW_ISSUE_ADJUDICATED','codex',request['reason'],receipt['artifact_id'])
        return record

    def _review_cycle_gate(self,m):
        from .core import GuardError
        if not self._review_cycle_contract(m):return None
        if m['spec'].get('canonical_workflow_v1'):self._canonical_review_binding_gate(m)
        for dependency in m.get('depends_on',[]):
            target=self.get(dependency['task_id'])
            if self._review_cycle_contract(target):
                target_state=self._review_cycle_state(target)
                if target_state['waiting_user'] or any(i['status']=='open' and i['blocking'] for i in target_state['issues'].values()):
                    raise GuardError('Dependent business unit awaits adjudication: '+target_state['unit_id'])
        state=self._review_cycle_state(m);candidate,body,versions=self._review_cycle_candidate(m);source=self._review_cycle_sources(m)
        self._review_cycle_effective_open_issues(m,state)
        blocked=[i for i,x in state['issues'].items() if x['status']=='open' and x['blocking']]
        if blocked:raise GuardError('Unresolved effective review issues: '+', '.join(blocked))
        missing=[];checked_proofs=set()
        for ident in state['scope_item_ids']:
            passed=state['passed_items'].get(ident)
            if passed:
                proof_key=(passed['task_id'],passed['report_artifact_id'])
                if proof_key not in checked_proofs:
                    proof_task=self.get(passed['task_id']);proof=self._review_cycle_evidence(proof_task,passed['report_artifact_id'])
                    if proof['hash']!=passed['report_sha256'] or proof['provider']!=passed['provider']:raise GuardError('Inherited independent review proof changed')
                    checked_proofs.add(proof_key)
            adjudicated=any(x['status'] in ('dismissed_exact_misquote','resolved_mechanical') and ident in x['affected_ids'] and x['dependency_sha256']==self._review_cycle_item_basis(m,versions,source,self._review_context(m,body),x['affected_ids']) for x in state['issues'].values())
            if adjudicated:
                for issue in state['issues'].values():
                    if issue['status'] in ('dismissed_exact_misquote','resolved_mechanical') and ident in issue['affected_ids']:
                        proof_task=self.get(issue['task_id']);proof=self._review_cycle_evidence(proof_task,issue['report_artifact_id'])
                        if proof['hash']!=issue['report_sha256']:raise GuardError('Adjudicated original review proof changed')
                        decision=next((a for a in state['adjudications'] if a['artifact_id']==issue.get('adjudication')),None)
                        if not decision:raise GuardError('Exact adjudication receipt is missing')
                        decision_task=self.get(decision.get('adjudication_task_id',decision['origin_task_id']))
                        if self._fresh(decision_task,decision['artifact_id'])['hash']!=decision['sha256']:raise GuardError('Adjudication receipt changed')
            if not passed or passed['semantic_sha256']!=versions.get(ident) or (passed['source_sha256']!=source and self._assignment_source_versions(m,[ident]) is None) or passed.get('dependency_sha256')!=self._review_cycle_item_basis(m,versions,source,self._review_context(m,body),[ident]):
                if not adjudicated:missing.append(ident)
        if missing:raise GuardError('Independent review is missing/stale for: '+', '.join(missing))
        # An adjudication can establish only the challenged candidate literal,
        # never unreviewed scope or unknown underlying university facts.
        return {'passed':True,'cycle_id':state['cycle_id'],'candidate_sha256':candidate['hash'],
                'review_rounds':len(state['rounds']),'underlying_unknowns_preserved':True}

    def freeze_review_content(self,task):
        from .core import now,js
        with self._mutate(task) as m:
            result=self._review_cycle_gate(m);state=self._review_cycle_state(m)
            record={**result,'frozen_at':now(),'item_versions':self._review_cycle_candidate(m)[2]}
            a=self._artifact(m,js(record).encode(),'evidence','local','content-freeze.json',[])
            state['content_frozen']={**record,'artifact_id':a['artifact_id']};self._review_cycle_store(m,state)
            self._event(m,'CONTENT_FROZEN','orchestrator','Effective independent content acceptance recorded',a['artifact_id'])
        return record

    def review_cycle_status(self,task):
        m=self.get(task)
        if not self._review_cycle_contract(m):return None
        state=self._review_cycle_state(m)
        peers=[m]+self._review_cycle_peers(self._review_cycle_contract(m),m['task_id'])
        subs=[s for t in peers for s in t['submissions'] if s.get('phase')=='reviewer']
        return {**state,'confirmed_external_messages':sum(bool(s.get('confirmed_at')) for s in subs),
            'unconfirmed_intents':sum(s.get('status')=='started' for s in subs),
            'prepared_tickets':sum(s.get('status')=='prepared' for s in subs),
            'format_adaptations':sum(e['event']=='INGESTION_FAILED' for t in peers for e in self.events(t['task_id'])),
            'underlying_unknown_count':sum(x.get('kind')=='source_limit' for x in state['issues'].values()),
            'provider_cost':None,'model_tokens':None}
