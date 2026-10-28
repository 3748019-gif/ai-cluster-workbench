"""Small work items, append-only research ingestion and observable quota policy."""
from datetime import datetime, timezone
from pathlib import Path
import json
import re
from .permissions import LIMITS, compile_policy, limit, permission_text, prompt
from .research import check_sources, check_research, check_review, claim_hash, normalize_metadata
from .document_review import PROTOCOL as OFFLINE_DOCUMENT, check_document_review, document_statements, document_review_scope

class WorkflowMixin:
    def accept_work(self,parent,child):
        from .core import GuardError,js
        result=self.get(child)
        if result.get('parent_task_id')!=parent or result['status']!='DELIVERED' or not self.audit(child)['passed']:
            raise GuardError('Only delivered, audited child results may be accepted')
        if result.get('work_kind') in ('research','revision','synthesis') and not result.get('review'):
            raise GuardError('Accept an independent review work item for a final research deliverable')
        final=self._fresh(result,result['final_output'])
        with self._mutate(parent) as m:
            if m['status']=='DELIVERED':raise GuardError('Parent is immutable')
            record={'task_id':child,'artifact_id':final['artifact_id'],'sha256':final['hash'],
                    'review_artifact':result.get('review'),'candidate_origin':result.get('candidate_origin')}
            a=self._artifact(m,js(record).encode(),'evidence','local','accepted-'+child[-4:]+'.json',[])
            m.setdefault('accepted_work',[]).append({**record,'receipt_artifact':a['artifact_id']})
        return record

    def resolve_from_work(self,task,issue_id,child,reason):
        from .core import GuardError
        result=self.get(child);original=self.get(task)
        if (result.get('parent_task_id') or child)!=(original.get('parent_task_id') or task):
            raise GuardError('Resolution must belong to the same parent task')
        origin=result.get('candidate_origin',{}).get('task_id')
        declared=set(result.get('origin_issue_ids',[]))
        if origin:declared.update(self.get(origin).get('origin_issue_ids',[]))
        if issue_id not in declared:raise GuardError('Followup is not linked to this original issue')
        if result['status']!='DELIVERED' or not result.get('review') or not self.audit(child)['passed']:
            raise GuardError('Resolution requires delivered independent followup review')
        a=self._fresh(result,result['review']);raw=self.read_artifact(result,a['artifact_id']);data=json.loads(raw)
        resolutions=data.get('issue_resolutions',[])
        match=next((x for x in resolutions if x.get('issue_id')==issue_id and x.get('status')=='resolved' and x.get('reason')),None)
        if not match or data['blocking']:raise GuardError('Independent reviewer has not closed this issue')
        source_ids={x['source_id'] for x in data.get('sources',[])}
        if not match.get('source_ids') or not set(match['source_ids'])<=source_ids:raise GuardError('Resolution needs registered followup sources')
        with self._mutate(task) as m:
            issue=next((x for x in m['open_issues'] if x['id']==issue_id and x['status']=='open'),None)
            if not issue:raise GuardError('No open issue')
            receipt=self._artifact(m,raw,'evidence',a['provider'],'resolution-'+child[-4:]+'.json',[],
                {'origin_task_id':child,'origin_artifact_id':a['artifact_id'],'origin_sha256':a['hash'],
                 'independent_resolution':True,'resolved_issue_ids':[issue_id]})
        self.resolve(task,issue_id,reason,[receipt['artifact_id']])
        return receipt

    def _accepted_gate(self,m):
        from .core import GuardError
        if m.get('assignment'):self._assignment_guard(m,'reviewer' if m['routing']['review_required'] else 'producer')
        self._canonical_gate(m)
        self._canonical_review_binding_gate(m)
        self._review_cycle_gate(m)
        self._document_stage_gate(m)
        for rec in m.get('candidate_files',[]):
            if rec.get('candidate_artifact_id') not in (None,m.get('candidate')):raise GuardError('Frozen files belong to another candidate')
            if self._fresh(m,rec['artifact_id'])['hash']!=rec['sha256']:raise GuardError('Frozen deliverable file version changed')
        for d in m.get('depends_on',[]):
            if not isinstance(d,dict) or not all(k in d for k in ('task_id','artifact_id','sha256')):raise GuardError('Invalid dependency identity')
            origin=self.get(d['task_id'])
            if self._fresh(origin,d['artifact_id'])['hash']!=d['sha256']:raise GuardError('Dependency version changed')
        for r in m.get('accepted_work',[]):
            child=self.get(r['task_id'])
            if child['status']!='DELIVERED' or not self.audit(child['task_id'])['passed']:raise GuardError('Accepted child no longer valid')
            if self._fresh(child,r['artifact_id'])['hash']!=r['sha256']:raise GuardError('Accepted child version changed')
        if m.get('review') and m.get('schema_version',1)>=2:
            review=json.loads(self.read_artifact(m,m['review']))
            candidate=self._fresh(m,m['candidate'])
            try:content=json.loads(self.read_artifact(m,candidate['artifact_id']))
            except ValueError:content={}
            if m['spec'].get('result_protocol')==OFFLINE_DOCUMENT:
                package=self._fresh(m,m['packages']['reviewer'])
                check_document_review(review,content,m['reviewer'],package['source_artifact_ids'],m.get('active_review_scope',m['spec'].get('document_review_scope')),candidate['artifact_id'])
            else:
                from .handoff import MEMBER_CONTENT
                check_review(review,content,m['reviewer'],protocol=m['spec'].get('result_protocol'),
                    contributors=m.get('candidate_contributors') or candidate.get('claim_contributors') or candidate.get('content_contributors'),
                    enforce_semantics=m['spec'].get('result_protocol')==MEMBER_CONTENT)

    def decision_packet(self,task):
        """Read-only routing snapshot; no objective, document text or full history."""
        m=self.get(task)
        def identity(aid):
            if not aid:return None
            a=self.artifact(m,aid)
            return {k:a[k] for k in ('artifact_id','hash','status','provider')}
        current=[v for v in m['validation'] if v['candidate_artifact_id']==m.get('candidate')]
        validation={k:current[-1][k] for k in ('artifact_id','candidate_artifact_id','passed')} if current else None
        blockers=[{'issue_id':x['id'],'kind':x['kind']} for x in m['open_issues'] if x['status']=='open' and x['blocking']]
        active=[{'submission_id':s['submission_id'],'phase':s['phase'],'provider':s['provider'],'status':s['status']}
                for s in m['submissions'] if s['status'] in ('prepared','started','confirmed','running')]
        action=m.get('next_expected_action') or self._next(m)
        if any(s['status']=='started' for s in active):action='inspect_existing_client_record_do_not_resend'
        elif any(s['status'] in ('confirmed','running') for s in active) and m['status'] in ('CLIENT_UNAVAILABLE','LOGIN_REQUIRED','FAILED','OUTPUT_MISSING'):
            action='inspect_client_without_resubmitting'
        model=m.get('controller_model')
        model_summary={'requested':model['requested'],'observed':model['observed']} if model else None
        receipts=[{k:x.get(k) for k in ('artifact_id','sha256','consumption','member')} for x in m.get('attachment_receipts',[])]
        queue=[{'request_id':x['request_id'],'status':x['status'],'subscribers':[s['requester'] for s in x['subscriptions']],
                'handled_by_request':x.get('handled_by_request')} for x in m.get('attachment_queue',{}).values()]
        return {'task_id':task,'status':m['status'],'candidate':identity(m.get('candidate')),'review':identity(m.get('review')),
                'validation':validation,'validation_status':m.get('validation_status','not_run'),
                'provider_execution_status':m.get('provider_execution_status'),
                'artifact_ingestion_status':m.get('artifact_ingestion_status'),
                'blocked':bool(blockers or m.get('blocking_issue')),'blocking_issues':blockers,
                'active_submissions':active,'review_required':m['routing']['review_required'],
                'failure_owner':m.get('failure_owner'),'controller_model':model_summary,'next_action':action,
                'attachment_queue':queue,'attachment_receipts':receipts,
                'semantic_status':m.get('semantic_gate',{}).get('blocking'),
                'depends_on':m.get('depends_on',[]),'handled_work':m.get('handled_work',[]),
                'releases':m.get('releases',[])}

    def review_batch_status(self,parent):
        """Read-only progress from actual accepted sets, never a parent auto-pass."""
        self.get(parent)
        tasks=[t for t in self.list_tasks() if t['task_id']==parent or t.get('parent_task_id')==parent]
        units={}
        for m in tasks:
            contract=m['spec'].get('review_cycle_contract')
            if not contract:continue
            unit=contract['unit_id']
            if unit not in units or m['created_at']>units[unit]['created_at']:units[unit]=m
        result=[]
        for unit,m in units.items():
            state=self._review_cycle_state(m)
            frozen=bool(m.get('content_freeze') or state.get('content_frozen'))
            ready=frozen;reason=None
            if frozen:
                try:self._canonical_review_binding_gate(m);self._review_cycle_gate(m)
                except ValueError as exc:ready=False;reason=str(exc)
            visual=False
            if ready and m['spec'].get('native_document_workflow_v1'):
                try:self._document_stage_gate(m);visual=True
                except ValueError as exc:reason=str(exc)
            result.append({'unit_id':unit,'task_id':m['task_id'],'content_complete':ready,
                           'native_pages_complete':visual,'waiting_user':state['waiting_user'],
                           'reason':reason,'open_issue_ids':[i for i,x in state['issues'].items() if x['status']=='open' and x['blocking']]})
        return {'parent_task_id':parent,'units':result,'total_units':len(result),
                'content_complete':sum(x['content_complete'] for x in result),
                'native_pages_complete':sum(x['native_pages_complete'] for x in result),
                'waiting_user':sum(x['waiting_user'] for x in result),
                'parent_status_unchanged':self.get(parent)['status'],'automatic_parent_delivery':False}

    def prepare_document_review(self,parent,producer,scope=None):
        """Prepare an independent round from original logical files and exact candidate.

        producer is the origin Task ID, not a member name. Scope is explicit;
        this helper does not compute impact or reuse any previous review.
        """
        from .core import GuardError
        p=self.get(parent);origin=self.get(producer)
        if origin.get('assignment'):raise GuardError('Dynamic task already pairs its reviewer; use its reviewer package or a safe create-task followup, not legacy role fallback')
        if (origin.get('parent_task_id') or producer)!=parent:raise GuardError('Document producer belongs to a different parent')
        if origin['spec'].get('result_protocol')!=OFFLINE_DOCUMENT:raise GuardError('Document review helper needs an offline document producer')
        candidate=self._fresh(origin,origin['candidate'])
        content=json.loads(self.read_artifact(origin,candidate['artifact_id']))
        selected=document_review_scope(content,scope)
        originals={a['logical_name']:a for a in p['artifacts'] if a['artifact_id'] in p['source_files']
                   and a['role']=='source' and a['provider']=='user' and a['logical_name'] in ('courses.csv','policy.md')}
        if set(originals)!= {'courses.csv','policy.md'}:raise GuardError('Parent needs original courses.csv and policy.md')
        # Validate all known inputs before creating the child; never use producer source_files or old verdicts.
        for a in originals.values():self._fresh(p,a['artifact_id'])
        if any(s['status'] in ('started','confirmed','running') for s in origin['submissions']):
            raise GuardError('Inspect existing producer submission before review')
        bindings=origin['role_bindings'];member=bindings['primary_executor'] if candidate['provider']==bindings['secondary_executor'] else bindings['secondary_executor']
        if member==candidate['provider']:raise GuardError('No independent document reviewer available')
        spec=dict(origin['spec'])
        for key in ('input_artifact_ids','reviewer_provider','external_benefit','previous_round_id','work_item_id','round_id'):spec.pop(key,None)
        spec.update(objective='Independently verify the exact offline document candidate',work_kind='review',
                    assigned_provider=member,review_required=True,depends_on=[],document_review_scope=selected,
                    output_requirements='Return the offline_document_v1 independent review JSON for the scoped statements.',
                    validators=[{'kind':'json','required_fields':['task_id','statements' if 'statements' in content else 'claims']}])
        child=self.new_work(parent,spec)['task_id'];sources=[]
        for name in ('courses.csv','policy.md'):
            sources.append(self.import_artifact(child,parent,originals[name]['artifact_id'],name))
        self.package(child)
        adopted=self.adopt_candidate(child,producer,candidate['artifact_id'])
        package=self.package(child,'reviewer');submission=self.prepare_submission(child,'reviewer')
        return {'task_id':child,'scope':selected,'sources':sources,'candidate':adopted,'package':package,'submission':submission,
                'impact_computed':False,'prior_review_reused':False}

    @staticmethod
    def _work_in_flight(m):
        return (m['status'] in ('SUBMISSION_UNCERTAIN','SUBMITTED','RUNNING','REVIEW_RUNNING')
                or any(s['status'] in ('started','confirmed','running') for s in m['submissions']))

    def _work_round_spec(self,spec,current=None):
        """Shared lineage guard, called inside the creation/dispatch transaction.

        Distinct work items may progress independently. Reusing one item must
        extend its latest recorded round, never hide an in-flight send in a
        newly numbered task. No historical state is changed by this check.
        """
        from .core import GuardError
        spec=dict(spec);parent=spec.get('parent_task_id');previous=spec.get('previous_round_id')
        work=spec.get('work_item_id');round_id=spec.get('round_id',1)
        if type(round_id) is not int or round_id<1:raise GuardError('CONFIG_ERROR: invalid round_id')
        if work is not None and (not isinstance(work,str) or not work.strip()):
            raise GuardError('Invalid work_item_id')
        if parent:
            p=self.get(parent)
            if p['run_mode']!=self.mode:raise GuardError('Parent run mode mismatch')
        if previous:
            if not parent:raise GuardError('Previous round requires its parent_task_id')
            old=self.get(previous)
            if old['run_mode']!=self.mode:raise GuardError('Previous round run mode mismatch')
            if (old.get('parent_task_id') or old['task_id'])!=parent:
                raise GuardError('Previous round belongs to different parent')
            expected_work=old.get('work_item_id',previous);expected_round=old.get('round_id',1)+1
            if work is not None and work!=expected_work:raise GuardError('Previous round belongs to different work item')
            if 'round_id' in spec and round_id!=expected_round:raise GuardError('Followup round_id must immediately follow previous round')
            work=expected_work;round_id=expected_round
            spec.update(work_item_id=work,round_id=round_id)
            # Check status before goal declarations so uncertainty cannot be
            # hidden by malformed follow-up metadata or an exception state.
            if self._work_in_flight(old):
                raise GuardError('Inspect existing submission; a new round cannot bypass uncertain or running work')
            reason=spec.get('followup_reason');goal=spec.get('new_work')
            if not isinstance(reason,str) or not reason.strip() or not (goal is True or isinstance(goal,str) and goal.strip()):
                raise GuardError('New round needs unresolved goal and new query/evidence/revision direction')
            if not old.get('candidate') and not old.get('review') and old['status'] not in ('CLIENT_UNAVAILABLE','LOGIN_REQUIRED','QUOTA_EXCEEDED','FAILED'):
                raise GuardError('Previous work has no captured output or known unavailability')
        elif round_id!=1:
            raise GuardError('Later round requires previous_round_id')
        peers=[m for m in self.list_tasks() if work is not None and m.get('work_item_id')==work
               and m['task_id']!=current]
        for peer in peers:
            if peer['run_mode']!=self.mode:raise GuardError('Work item run mode mismatch')
            if (peer.get('parent_task_id') or peer['task_id'])!=parent:
                raise GuardError('Work item belongs to different parent')
            if self._work_in_flight(peer):
                raise GuardError('Inspect existing submission; a new round cannot bypass uncertain or running work')
        if peers:
            if not previous:raise GuardError('Existing work item requires previous_round_id')
            latest=max(peers,key=lambda m:m.get('round_id',1))
            if latest['task_id']!=previous or any(m.get('round_id',1)>=round_id for m in peers):
                raise GuardError('Followup must extend the latest unique work-item round')
        return spec

    def _work_dispatch_guard(self,m):
        spec=dict(m['spec'])
        # Durable task identity, not caller-supplied dispatch options, governs.
        for key in ('parent_task_id','previous_round_id','work_item_id','round_id'):
            spec[key]=m.get(key)
        self._work_round_spec(spec,current=m['task_id'])
        if m.get('assignment'):self._assignment_guard(m,'producer')

    def new_work(self,parent,spec,previous=None):
        from .core import GuardError
        spec=dict(spec)
        if spec.get('parent_task_id') not in (None,parent):raise GuardError('Conflicting parent_task_id')
        if previous is not None and spec.get('previous_round_id') not in (None,previous):
            raise GuardError('Conflicting previous_round_id')
        spec['parent_task_id']=parent
        if previous is not None:spec['previous_round_id']=previous
        # create owns the transaction and invokes the shared lineage guard.
        return self.create(spec)

    def select_inputs(self,task,artifact_ids):
        from .core import GuardError
        with self._mutate(task) as m:
            if m['status'] not in ('CREATED','PACKAGED','READY_TO_SUBMIT','OUTPUT_RECEIVED','REVIEW_PENDING'):
                raise GuardError('Select inputs before submitting this work item')
            if any(x['status'] not in ('prepared','cancelled','not_submitted') for x in m['submissions']):
                raise GuardError('Cannot change inputs after a send intent')
            if not artifact_ids or len(set(artifact_ids))!=len(artifact_ids) or not set(artifact_ids)<=set(m['source_files']):
                raise GuardError('Select nonempty unique registered sources')
            for sid in artifact_ids:self._fresh(m,sid)
            for submission in m['submissions']:
                if submission['status']=='prepared':
                    submission.update(status='cancelled',cancellation_reason='Input selection changed before send intent; old ticket cannot be sent')
            m['spec']['input_artifact_ids']=list(artifact_ids)
            if m['status']=='READY_TO_SUBMIT':self._move(m,'PACKAGED','INPUTS_SELECTED','orchestrator','Repackage selected inputs; preserve all original sources')
            else:self._event(m,'INPUTS_SELECTED','orchestrator','Repackage selected inputs; preserve all original sources')
        return {'selected':list(artifact_ids),'repackage_required':True}

    def import_artifact(self,task,origin,artifact_id,name=None):
        from .core import GuardError
        old=self.get(origin);a=self._fresh(old,artifact_id);raw=self.read_artifact(old,artifact_id)
        with self._mutate(task) as m:
            if m['status']!='CREATED':raise GuardError('Import inputs before freezing this round')
            if m.get('work_kind')=='review' and a['role'] not in ('source','background'):
                raise GuardError('Independent reviewer inputs must be original sources; adopt candidate separately, never import prior verdicts')
            result=self._artifact(m,raw,'source',a['provider'],name or 'import-'+artifact_id[-12:]+Path(a['filename']).suffix,[],
                {'origin_task_id':origin,'origin_artifact_id':artifact_id,'origin_sha256':a['hash'],'evidence_level':a.get('evidence_level','imported_saved_artifact')})
            m.setdefault('depends_on',[]).append({'task_id':origin,'artifact_id':artifact_id,'sha256':a['hash']})
        return result

    def adopt_candidate(self,task,origin,artifact_id):
        from .core import GuardError
        old=self.get(origin);a=self._fresh(old,artifact_id)
        if a['role'] not in ('producer','final'):raise GuardError('Only exact producer/final artifacts can be adopted')
        if any(s['status'] in ('started','confirmed','running') for s in old['submissions']):raise GuardError('Prior provider work still active')
        raw=self.read_artifact(old,artifact_id)
        with self._mutate(task) as m:
            if m['status']!='PACKAGED' or m.get('work_kind')!='review':raise GuardError('Review work must be packaged before candidate adoption')
            actual_producer=a['provider']
            provenance=a
            if a['role']=='final' and old.get('candidate') and old['candidate'] in a['input_artifacts']:
                provenance=self._fresh(old,old['candidate']);actual_producer=provenance['provider']
            if m['spec'].get('result_protocol')==OFFLINE_DOCUMENT and a['role']=='final':
                if not old.get('candidate') or old['candidate'] not in a['input_artifacts']:
                    raise GuardError('Offline final has no exact producer provenance')
                actual_producer=self._fresh(old,old['candidate'])['provider']
            if actual_producer==m['reviewer']:raise GuardError('Producer cannot independently review own candidate')
            contributions=provenance.get('claim_contributors') or provenance.get('content_contributors') or [actual_producer]
            people=set(sum(contributions.values(),[])) if isinstance(contributions,dict) else set(contributions)
            if m['reviewer'] in people:raise GuardError('Actual content contributor cannot review by relabeling provider or model')
            new=self._artifact(m,raw,'producer',actual_producer,'candidate'+Path(a['filename']).suffix,[m['packages']['producer']],
                {'origin_task_id':origin,'origin_artifact_id':artifact_id,'origin_sha256':a['hash'],
                 'claim_contributors':contributions if isinstance(contributions,dict) else None,'content_contributors':sorted(people)})
            m['candidate_contributors']=contributions
            if m.get('assignment'):
                records=old.get('author_history',[])
                if records:
                    inherited=records[-1]['authors']
                    new['author_lineage']={**records[-1],'candidate_artifact_id':new['artifact_id'],'candidate_sha256':new['hash']}
                    m.setdefault('author_history',[]).append(new['author_lineage'])
                else:
                    self._assignment_provenance(m,new,json.loads(raw),{'actor_id':'legacy:'+actual_producer,'member':old.get('member_profiles',{}).get(actual_producer,{}).get('platform',actual_producer),'session_id':None,'legacy_platform_only':True})
            frozen=[]
            for rec in old.get('candidate_files',[]):
                file_artifact=self._fresh(old,rec['artifact_id'])
                if file_artifact['hash']!=rec['sha256']:raise GuardError('Frozen origin deliverable changed')
                copied=self._artifact(m,self.read_artifact(old,rec['artifact_id']),'evidence',actual_producer,
                    'adopted-file-'+str(len(frozen)).zfill(2)+Path(rec['logical_filename']).suffix,[new['artifact_id']],
                    {'origin_task_id':origin,'origin_artifact_id':rec['artifact_id'],'origin_sha256':rec['sha256'],'candidate_payload_file':True})
                frozen.append({**rec,'artifact_id':copied['artifact_id'],'frozen_path':str(self.task_path(task)/copied['filename']),
                    'candidate_artifact_id':new['artifact_id']})
            m['candidate_files']=frozen
            m.setdefault('candidate_file_sets',{})[new['artifact_id']]=frozen
            m['candidate']=new['artifact_id'];m['candidate_origin']={'task_id':origin,'artifact_id':artifact_id,'sha256':a['hash'],'producer':actual_producer}
            m.setdefault('depends_on',[]).append({'task_id':origin,'artifact_id':artifact_id,'sha256':a['hash']})
            self._move(m,'OUTPUT_RECEIVED','CANDIDATE_ADOPTED','orchestrator','Exact previous output adopted without rewriting',new['artifact_id'])
        return new

    def _budget_guard(self,provider,m=None,phase='producer'):
        from .core import GuardError
        path=self.root/'.cluster/budget_policy.json'
        project=json.loads(path.read_text()) if path.exists() else None
        if project and project.get('schema_version')!=2:return self._legacy_budget_guard(provider)
        if m is None:return
        ep=m.get('effective_policy')
        if not ep:raise GuardError('Legacy task permissions remain frozen; create a linked new round')
        q=ep['providers'][provider]
        tasks=self.list_tasks()
        platform=m.get('member_profiles',{}).get(provider,{}).get('platform',provider)
        authority=(m.get('assignment') or {}).get('authorization_source')
        active=[(t,s) for t in tasks for s in t['submissions'] if t.get('member_profiles',{}).get(s['provider'],{}).get('platform',s['provider'])==platform and s['status'] not in ('not_submitted','cancelled','prepared') and (t.get('effective_policy',{}).get('policy_id')==ep['policy_id'] or authority and (t.get('assignment') or {}).get('authorization_source')==authority)]
        for key,n in [('max_calls',len(active)),
                      ('max_calls_per_work_item',sum(t.get('work_item_id')==m.get('work_item_id') for t,s in active)),
                      ('max_calls_per_phase',sum((t.get('parent_task_id') or t['task_id'])==(m.get('parent_task_id') or m['task_id']) and s['phase']==phase for t,s in active)),
                      ('max_review_rounds',sum(t.get('work_item_id')==m.get('work_item_id') and s['phase']=='reviewer' for t,s in active))]:
            cap=q.get(key);limit(cap,key)
            if key=='max_review_rounds' and phase!='reviewer':continue
            if cap is not None and n>=cap:raise GuardError('Explicit call cap reached: '+key)
        cap=m['spec'].get('call_limits',{}).get(phase)
        if cap is not None and sum(s['phase']==phase and s['status'] not in ('prepared','not_submitted','cancelled') for s in m['submissions'])>=cap:
            raise GuardError('Explicit phase call cap reached')
        obs=[o for t in tasks for o in t['cost_observations'] if t.get('member_profiles',{}).get(o['provider'],{}).get('platform',o['provider'])==platform]
        status=[o for o in obs if o.get('availability') in ('available','exhausted','login_required','payment_required','unavailable')]
        if status and max(status,key=lambda o:o['observed_at'])['availability'] in ('exhausted','login_required','payment_required','unavailable'):
            raise GuardError('Known provider unavailable; observe recovery before another action')
        if q.get('quota_policy')=='explicit_limit':
            unit=q.get('unit');values=[o for o in obs if o['unit']==unit and o.get('value') is not None]
            if not values:raise GuardError('Explicit balance limit requires a matching-unit observation')
            v=max(values,key=lambda o:o['observed_at'])['value']
            if q.get('minimum_remaining') is not None and v<q['minimum_remaining']:raise GuardError('Explicit balance floor reached')
            if q.get('max_observed_spend') is not None:
                if q.get('baseline') is None:raise GuardError('Explicit spend limit requires baseline')
                if q['baseline']-v>=q['max_observed_spend']:raise GuardError('Explicit spend cap reached')
        # Unknown/stale is observable, not fabricated availability or an implicit prohibition.

    def capture_output(self,task,sid,path,token,proof):
        from .core import GuardError,digest,now
        raw=Path(path).read_bytes()
        with self._mutate(task) as m:
            self._lock(task,token);s=self._sub(m,sid)
            if s['status'] not in ('confirmed','running'):raise GuardError('No confirmed result to capture')
            p,e=self._proof(m,s,proof,'output')
            if p.get('output_hash')!=digest(raw) or p.get('completed') is not True:raise GuardError('Output completion/hash not proven')
            a=self._artifact(m,raw,'evidence',s['provider'],'raw_output-'+sid[-3:]+'.txt',[s['package_id'],e['artifact_id']],{'submission_id':sid})
            observations=s.setdefault('completion_observations',[])
            if s.get('first_completion_observed_at') and not observations:
                prior_evidence=s.get('first_completion_evidence')
                if not prior_evidence and s.get('captured_artifact'):
                    prior_evidence=self.artifact(m,s['captured_artifact'])['input_artifacts'][-1]
                observations.append({'observed_at':s['first_completion_observed_at'],'evidence_artifact':prior_evidence,'raw_artifact':s.get('captured_artifact')})
            observations.append({'observed_at':p['observed_at'],'evidence_artifact':e['artifact_id'],'raw_artifact':a['artifact_id']})
            earliest=min(observations,key=lambda x:datetime.fromisoformat(x['observed_at'].replace('Z','+00:00')))
            s.update(captured_artifact=a['artifact_id'],first_completion_observed_at=earliest['observed_at'],first_completion_evidence=earliest['evidence_artifact'],captured_at=now())
            m.update(provider_execution_status='completed',artifact_ingestion_status='captured',failure_owner=None)
            self._event(m,'OUTPUT_CAPTURED',s['phase'],'Raw response retained; parsing can run after releasing GUI_LOCK',a['artifact_id'])
        return a

    def ingest_output(self,task,sid):
        from .core import GuardError,now,js
        from .validators import review_schema
        from .handoff import MEMBER_CONTENT,ADAPTER_VERSION,HandoffError,build_envelope,process_member_content
        m=self.get(task);s=self._sub(m,sid)
        if s['status'] not in ('confirmed','running') or not s.get('captured_artifact'):raise GuardError('Capture confirmed response first; never resend to fix parsing')
        raw=self.read_artifact(m,s['captured_artifact'])
        content_protocol=m['spec'].get('result_protocol')==MEMBER_CONTENT
        semantic_report=None;protocol_report=None
        try:
            text=raw.decode('utf-8').strip()
            if text.startswith('```'):
                text=re.sub(r'^```(?:json)?\s*','',text);text=re.sub(r'\s*```$','',text)
            data=json.loads(text)
            if content_protocol:
                package=self._fresh(m,s['package_id'])
                candidate=self._fresh(m,m['candidate']) if s['phase']=='reviewer' else None
                cd=json.loads(self.read_artifact(m,candidate['artifact_id'])) if candidate else None
                envelope=build_envelope(m,s,package,candidate)
                contributors=m.get('candidate_contributors') or (candidate or {}).get('claim_contributors') or (candidate or {}).get('content_contributors')
                protocol_report=process_member_content(data,envelope,cd,
                    allowed_domains=m['effective_policy']['providers'][s['provider']].get('allowed_public_domains'),
                    source_artifact_ids=m['source_files'],contributors=contributors)
                data=protocol_report['data'];normalizations=protocol_report['repairs'];semantic_report=protocol_report['semantic_gate']
                if protocol_report['errors']:raise HandoffError(protocol_report['errors'],protocol_report['skipped'])
            else:
                envelope_repairs=[]
                if m.get('assignment') and s['phase']=='producer':
                    if not isinstance(data,dict):raise GuardError('Producer must return the declared JSON object')
                    for key,value in (('task_id',task),('submission_id',sid)):
                        if key in data and data[key]!=value:raise GuardError('Explicit producer identity conflict: '+key)
                        if key not in data:data[key]=value;envelope_repairs.append({'field':key,'operation':'bind_known_submission_identity','value':value})
                    if m['spec'].get('result_protocol')==OFFLINE_DOCUMENT:
                        data,transport_repairs=self._assignment_transport_metadata(m,s,data)
                        envelope_repairs.extend(transport_repairs)
                if m.get('review_cycle') and m['spec'].get('result_protocol')==OFFLINE_DOCUMENT and s['phase']=='reviewer':
                    from .offline_adapter import bind_offline_review
                    exact=self._fresh(m,m['candidate']);exact_data=json.loads(self.read_artifact(m,exact['artifact_id']))
                    data,envelope_repairs=bind_offline_review(data,m,s,exact,exact_data,m.get('active_review_scope',m['spec'].get('document_review_scope')) or list(document_statements(exact_data)))
                if not isinstance(data,dict) or data.get('task_id')!=task or data.get('submission_id')!=sid:raise GuardError('Output belongs to another task or submission')
                data,normalizations=normalize_metadata(data,s['provider'])
                normalizations=envelope_repairs+normalizations
            if normalizations:text=js(data)
            if data.get('work_item_id',m['work_item_id'])!=m['work_item_id'] or data.get('round_id',m['round_id'])!=m['round_id']:raise GuardError('Work item identity mismatch')
            ep=m['effective_policy']['providers'][s['provider']]
            offline=m['spec'].get('result_protocol')==OFFLINE_DOCUMENT
            if offline and data.get('sources',[])!=[]:raise GuardError('Offline document result cannot contain web source cards')
            if not offline and s['phase']=='producer' and m.get('work_kind') in ('research','synthesis','revision','evidence_followup'):
                check_research(data,s['provider'],ep.get('allowed_public_domains'),protocol=MEMBER_CONTENT if content_protocol else None)
            else:check_sources(data,s['provider'],ep.get('allowed_public_domains'),protocol=MEMBER_CONTENT if content_protocol else None)
            package=self._fresh(m,s['package_id'])
            if s['phase']=='reviewer':
                self._assignment_guard(m,'reviewer')
                candidate=self._fresh(m,m['candidate'])
                if package.get('candidate_artifact_id')!=candidate['artifact_id']:raise GuardError('Stale candidate review')
                source_ids=package['source_artifact_ids'] if offline else m['source_files']+[x['source_id'] for x in data.get('sources',[])]
                review_schema(data,task,candidate,source_ids)
                try:cd=json.loads(self.read_artifact(m,candidate['artifact_id']))
                except json.JSONDecodeError:cd={}
                if offline:
                    if candidate['provider']==s['provider']:raise GuardError('Producer cannot independently review own candidate')
                    check_document_review(data,cd,s['provider'],source_ids,m.get('active_review_scope',m['spec'].get('document_review_scope')),candidate['artifact_id'])
                else:check_review(data,cd,s['provider'],
                    protocol=MEMBER_CONTENT if content_protocol else None,
                    contributors=(m.get('candidate_contributors') or candidate.get('claim_contributors') or candidate.get('content_contributors')) if content_protocol else None,
                    enforce_semantics=not content_protocol)
        except (ValueError,TypeError,KeyError) as exc:
            with self._mutate(task) as failure:
                failure['artifact_ingestion_status']='identity_mismatch' if 'identity' in str(exc) or 'another task' in str(exc) or (isinstance(exc,HandoffError) and any(x.get('category')=='identity' for x in exc.errors)) else 'parse_error'
                failure['failure_owner']='transport' if failure['artifact_ingestion_status']=='identity_mismatch' else 'orchestrator'
                report_id=None
                if content_protocol:
                    problems=exc.errors if isinstance(exc,HandoffError) else [{'code':'invalid_input','location':'result','message':str(exc),'owner':'orchestrator'}]
                    adapted_snapshot=None
                    if protocol_report is not None:
                        adapted_snapshot=self._artifact(failure,js(protocol_report['data']).encode(),'evidence','local','adapted-unaccepted-'+sid[-3:]+'.json',[s['captured_artifact']])
                    report={'task_id':task,'submission_id':sid,'raw_response':s['captured_artifact'],
                            'raw_sha256':self.artifact(failure,s['captured_artifact'])['hash'],
                            'adapted_artifact':adapted_snapshot['artifact_id'] if adapted_snapshot else None,
                            'adapted_sha256':adapted_snapshot['hash'] if adapted_snapshot else None,
                            'adapter_version':ADAPTER_VERSION,'errors':problems,
                            'skipped':exc.skipped if isinstance(exc,HandoffError) else [],
                            'repairs':(protocol_report or {}).get('repairs',[]),'original_preserved':True,
                            'facts_verified':False,'new_external_submission_required':False}
                    report_id=self._artifact(failure,js(report).encode(),'evidence','local','ingestion-check-'+sid[-3:]+'.json',[s['captured_artifact']])['artifact_id']
                    failure['last_ingestion_report']=report_id
                self._event(failure,'INGESTION_FAILED','orchestrator',str(exc),report_id)
            raise GuardError('Captured output retained; '+str(exc)) from exc
        with self._mutate(task) as m:
            s=self._sub(m,sid);mapping={}
            for source in data.get('sources',[]):
                a=self._artifact(m,js(source).encode(),'source',s['provider'],'web-'+sid[-3:]+'-'+str(len(mapping)+1)+'.json',[s['captured_artifact']],
                    {'source_id':source['source_id'],'url':source['url'],'accessed_at':source['accessed_at'],
                     'evidence_level':'member_reported_excerpt','raw_artifact_id':None,
                     **({'source_namespace':source['source_namespace'],'source_global_id':source['source_namespace']+'/'+source['source_id']} if content_protocol else {})})
                mapping[source['source_id']]=a['artifact_id']
            a=self._artifact(m,text.encode(),'producer' if s['phase']=='producer' else 'reviewer',s['provider'],
                'candidate.json' if s['phase']=='producer' else 'review.json',[s['package_id'],s['captured_artifact']],{'submission_id':sid,
                    **({'result_protocol':MEMBER_CONTENT,'content_contributors':[s['provider']],
                        'claim_contributors':{x['claim_id']:[s['provider']] for x in data.get('claims',[])}} if content_protocol and s['phase']=='producer' else {})})
            derived={'task_id':task,'submission_id':sid,'raw_response':s['captured_artifact'],'response_artifact':a['artifact_id'],
                     'source_id_map':mapping,'claims':[{**x,'claim_sha256':claim_hash(x),'reviewer':None,'verification_status':'pending'} for x in data.get('claims',[])],
                     'claim_checks':data.get('claim_checks',[]),'normalization':normalizations or ['outer code fence and surrounding whitespace only'],
                     'original_preserved':True,'research_content_rewritten':False}
            self._artifact(m,js(derived).encode(),'evidence','local','ingestion-'+sid[-3:]+'.json',[a['artifact_id']]+list(mapping.values()))
            if content_protocol:
                repair={'task_id':task,'submission_id':sid,'adapter_version':ADAPTER_VERSION,
                        'adapter_code_hashes':protocol_report.get('adapter_code_hashes'),
                        'raw_response':s['captured_artifact'],'raw_sha256':self.artifact(m,s['captured_artifact'])['hash'],
                        'adapted_artifact':a['artifact_id'],'adapted_sha256':a['hash'],'changes':normalizations,
                        'original_preserved':True,'research_content_rewritten':False,
                        'body_or_fact_text_changed':False,
                        'source_registration_changed':any(x.get('source_id') or str(x.get('field','')).startswith('sources[') for x in normalizations),
                        'semantic_gate':semantic_report,'source_global_map':{
                            source['source_namespace']+'/'+source['source_id']:mapping[source['source_id']] for source in data.get('sources',[])}}
                receipt=self._artifact(m,js(repair).encode(),'evidence','local','repair-'+sid[-3:]+'.json',[s['captured_artifact'],a['artifact_id']])
                m['last_repair_record']=receipt['artifact_id']
            s.update(status='completed',completed_at=now(),output_artifact=a['artifact_id'],source_id_map=mapping)
            m.update(provider_execution_status='completed',artifact_ingestion_status='ingested',validation_status='not_run',failure_owner=None,blocking_issue=None)
            m.setdefault('output_artifacts',[]).append(a['artifact_id'])
            if s['phase']=='producer':
                self._assignment_provenance(m,a,data)
                if m.get('candidate_files'):
                    m.setdefault('candidate_file_sets',{})[m['candidate']]=m['candidate_files']
                m['candidate_files']=[]
                m['candidate']=a['artifact_id'];m['review']=None;m['final_output']=None
                self._move(m,'OUTPUT_RECEIVED','OUTPUT_RECEIVED','producer','Captured output ingested without new model call',a['artifact_id'])
            else:
                m['review']=a['artifact_id']
                self._assignment_review_authorship(m,data,a)
                self._move(m,'REVIEW_COMPLETED','REVIEW_COMPLETED','reviewer','Independent review bound to candidate and claim versions',a['artifact_id'],extra={'confirmed_errors':len(data['confirmed_errors'])})
                checks=data.get('claim_checks',[])
                findings=[{'id':x['claim_id'],'description':x['reason'],'category':x['result'],**x} for x in checks if x['blocking']]
                if data['blocking'] and not findings:findings=data['confirmed_errors'] or [{'id':'review-block','category':'evidence_gap','description':'Unresolved blocking scope'}]
                if semantic_report:
                    findings.extend(semantic_report['findings'])
                    m['semantic_gate']={**semantic_report,'candidate_artifact_id':m['candidate'],
                        'candidate_sha256':self.artifact(m,m['candidate'])['hash'],'review_artifact_id':a['artifact_id']}
                if m.get('review_cycle'):
                    self._review_cycle_received(m,s,a,data)
                else:
                    for error in findings:
                        m['open_issues'].append({'id':a['artifact_id']+':'+error['id'],'kind':error.get('category','confirmed_error'),
                            'status':'open','blocking':True,'detail':error,'evidence':a['artifact_id'],'candidate_artifact_id':m['candidate'],
                            'assigned_provider':m.get('candidate_origin',{}).get('producer',m['producer'])})
                    if findings:self._move(m,'REVIEW_CONFLICT','CONFLICT_FOUND','reviewer','Evidence gaps/errors need followup',a['artifact_id'])
        return a

    def receive(self,task,sid,path,token,proof):
        if self.get(task).get('schema_version',1)<2:return self._receive_v1(task,sid,path,token,proof)
        self.capture_output(task,sid,path,token,proof)
        return self.ingest_output(task,sid)

    def reassign(self,task,provider,reason):
        from .core import GuardError
        old=self.get(task)
        if old['status'] not in ('CLIENT_UNAVAILABLE','LOGIN_REQUIRED','QUOTA_EXCEEDED','FAILED'):raise GuardError('Reassignment requires recorded unavailability')
        spec=dict(old['spec']);spec.update(assigned_provider=provider,followup_reason=reason,new_work='Continue remaining work with a different available member; keep independent review')
        spec.pop('round_id',None)
        if old.get('assignment'):
            spec['assignment_request']=dict(spec.get('assignment_request',{}));spec['assignment_request']['executor']=provider
        spec.pop('external_benefit',None)
        return self.new_work(old.get('parent_task_id') or task,spec,task)

    def stage(self,task,name,action,note=''):
        from .core import GuardError,now
        allowed=('codex_research','codex_drafting','codex_rewriting','adjudication','gui','controller_debug','handoff','user_edit')
        if name not in allowed or action not in ('start','stop'):raise GuardError('Invalid stage')
        with self._mutate(task) as m:
            stages=m.setdefault('stage_measurements',[])
            if action=='start':
                if any(x['name']==name and x['ended_at'] is None for x in stages):raise GuardError('Stage already running')
                stages.append({'name':name,'started_at':now(),'ended_at':None,'seconds':None,'note':note})
            else:
                active=next((x for x in reversed(stages) if x['name']==name and x['ended_at'] is None),None)
                if not active:raise GuardError('Stage not started; no retroactive inferred duration')
                active['ended_at']=now();active['seconds']=(datetime.fromisoformat(active['ended_at'])-datetime.fromisoformat(active['started_at'])).total_seconds()
            self._event(m,'STAGE_'+action.upper(),'orchestrator',note,extra={'stage':name})

    def rollup(self,parent):
        from .core import js
        tasks=[m for m in self.list_tasks() if m['task_id']==parent or m.get('parent_task_id')==parent]
        sources={};claims={};items=[]
        for m in tasks:
            items.append({'task_id':m['task_id'],'work_item_id':m.get('work_item_id'),'round_id':m.get('round_id'),
                'work_kind':m.get('work_kind'),'producer':m['producer'],'reviewer':m['reviewer'],'status':m['status'],
                'provider_execution_status':m.get('provider_execution_status'),'ingestion_status':m.get('artifact_ingestion_status'),
                'open_issues':[x for x in m['open_issues'] if x['status']=='open'],'metrics':self.metrics(m['task_id'])})
            for a in m['artifacts']:
                if a.get('evidence_level')=='member_reported_excerpt':
                    # Imported cards retain exact bytes but older metadata omitted URL.
                    url=a.get('url')
                    if not url:
                        card=json.loads(self.read_artifact(m,a['artifact_id']))
                        url=card['url']
                    sources.setdefault(url,[]).append({'artifact_id':a['artifact_id'],'provider':a['provider'],'hash':a['hash']})
            if m.get('candidate'):
                try:data=json.loads(self.read_artifact(m,m['candidate']))
                except (ValueError,UnicodeDecodeError):continue
                for x in data.get('claims',[]):claims.setdefault(claim_hash(x),x)
        return {'parent_task_id':parent,'items':items,'unique_reported_source_urls':sources,'unique_claim_versions':claims,
                'confirmed_external_calls':sum(x['metrics']['external_calls_confirmed'] for x in items),
                'verified_increment':None,'codex_tokens':None,'user_edit_seconds':None,
                'notes':['Reported source count is not independently fetched source count.','Claim semantic equivalence and adoption need human/model adjudication; no automatic savings claim.']}
