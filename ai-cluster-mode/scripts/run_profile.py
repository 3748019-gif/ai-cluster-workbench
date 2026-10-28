"""Per-run configuration. No GUI, provider API, packages or paid actions."""
import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

ROLES = ('orchestrator', 'primary_executor', 'secondary_executor')
EFFORTS = (None, 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')
PRESETS = {'gpt-6.1-sol/current': ('gpt-6.1-sol', None),
           'gpt-6.1-sol/ultra': ('gpt-6.1-sol', 'ultra'),
           'gpt-6-astra/ultra': ('gpt-6-astra', 'ultra'),
           'gpt-6-astra/xhigh': ('gpt-6-astra', 'xhigh')}
LIMITS = ('max_calls', 'max_calls_per_phase', 'max_calls_per_work_item', 'max_search_queries', 'max_review_rounds')
BOUNDARY = {'actual_client_settings': 'unknown_until_current_observation',
            'client_settings_changed': False, 'paid_actions_allowed': False,
            'codex_call_metric': 'Task creations through this run-profile launcher, not model invocations',
            'codex_model_tokens': None, 'external_model_tokens': None,
            'budget_metric': 'observed account balance/spend between dispatches; not exact task billing or a guaranteed per-call fee ceiling',
            'kimi_quota_period': 'monthly; no budget reset inferred from task/model restart',
            'fallback_execution': 'manual current-client action after explicit matching authorization; never automatic switching'}

def _keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError('Unknown or invalid '+label+' fields')

def _number(value, label):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        raise ValueError(label+' must be finite nonnegative or null')

def _text(value, label, nullable=False):
    if nullable and value is None: return
    if not isinstance(value, str) or not value.strip() or any(ord(c)<32 for c in value):
        raise ValueError(label+' needs nonempty plain text')

def load_config(source, registry):
    source = Path(source)
    if source.is_symlink() or not source.is_file(): raise ValueError('Config must be a regular JSON file')
    data = json.loads(source.read_text(encoding='utf-8'))
    _keys(data, ('schema_version', 'run_id', 'authorization_source', 'roles', 'default_task_route', 'workflow_v1','assignment_v1'), 'profile')
    if data.get('assignment_v1') is not None:
        import importlib.util
        assignment_path=Path(__file__).resolve().parents[1]/'assets/runtime/cluster/assignment.py'
        module_spec=importlib.util.spec_from_file_location('profile_assignment_validation',assignment_path)
        module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
        module.validate_assignment(data['assignment_v1'],registry)
    flow=data.get('workflow_v1')
    if flow is not None:
        _keys(flow,('canonical_required','native_documents_required','automatic_review_rounds','hard_timeout_minutes'),'workflow_v1')
        for key in ('canonical_required','native_documents_required'):
            if type(flow.get(key)) is not bool:raise ValueError('workflow_v1 needs boolean '+key)
        if type(flow.get('automatic_review_rounds')) is not int or flow['automatic_review_rounds']!=2:raise ValueError('Business review automatic rounds must be 2; provider max_review_rounds remains independent')
        if flow.get('hard_timeout_minutes') is not None:raise ValueError('This version does not enable a hard timeout')
        if flow['native_documents_required'] and not flow['canonical_required']:raise ValueError('Native documents need canonical data')
    if data.get('default_task_route') not in (None,'file_processing','open_research','mixed'):
        raise ValueError('Unknown default_task_route')
    if data.get('schema_version') != 1 or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', data.get('run_id', '')):
        raise ValueError('Profile needs schema_version 1 and safe unique run_id')
    if data['run_id'].lower() == 'active' or data['run_id'].lower().endswith('.bindings'):
        raise ValueError('run_id collides with reserved profile filenames')
    _text(data.get('authorization_source'), 'authorization_source')
    if not isinstance(data.get('roles'), dict) or set(data['roles']) != set(ROLES):
        raise ValueError('All three fixed roles are required')
    seen = set()
    for role, cfg in data['roles'].items():
        _keys(cfg, ('member', 'requested', *LIMITS, 'budget', 'quota_period', 'fallback', 'network_access'), role)
        if cfg.get('network_access') not in (None,'offline','allowed_as_needed'):raise ValueError('Invalid role network_access')
        member = cfg.get('member'); profile = registry['members'].get(member)
        if not profile: raise ValueError('Register and verify replacement member first: '+str(member))
        if role == 'orchestrator' and member != 'codex': raise ValueError('Codex remains orchestrator')
        if role != 'orchestrator':
            if member == 'codex' or profile.get('transport') != 'gui' or not {'producer','reviewer'} <= set(profile.get('roles', [])):
                raise ValueError('Executors need registered independent GUI production/review capability')
            if profile.get('verification_status') == 'unverified': raise ValueError('Replacement requires recorded capability pilot')
        if member in seen: raise ValueError('Roles must bind distinct members')
        seen.add(member)
        req = cfg.setdefault('requested', {'model': None, 'reasoning_effort': None, 'speed': None})
        _keys(req, ('model', 'reasoning_effort', 'speed'), 'requested')
        for key in ('model','reasoning_effort','speed'): req.setdefault(key,None)
        _text(req['model'], 'requested model', True)
        if req['reasoning_effort'] not in EFFORTS: raise ValueError('Invalid requested reasoning effort')
        if req['speed'] not in (None, 'fast', 'standard', 'preserve_current'): raise ValueError('Invalid requested speed')
        for key in LIMITS:
            cfg.setdefault(key, None)
            if cfg[key] is not None and (type(cfg[key]) is not int or cfg[key] < 0):
                raise ValueError(key+' must be null or a nonnegative integer')
        if role == 'orchestrator' and any(cfg[k] is not None for k in LIMITS if k != 'max_calls'):
            raise ValueError('Codex supports scheduler max_calls only; internal phase/search/model calls are unknown')
        budget = cfg.setdefault('budget', {})
        _keys(budget, ('max_observed_spend','minimum_remaining','baseline','unit','max_percent','quota_total'), 'budget')
        for key in ('max_observed_spend','minimum_remaining','baseline'):
            budget.setdefault(key,None); _number(budget[key],key)
        budget.setdefault('unit',None); _text(budget['unit'],'budget unit',True)
        budget.setdefault('max_percent',None); budget.setdefault('quota_total',None)
        for key in ('max_percent','quota_total'): _number(budget[key],key)
        if budget['max_percent'] is not None:
            if budget['max_percent']>100: raise ValueError('Budget max_percent must be 0..100')
            if not budget['quota_total']: raise ValueError('Percentage denominator quota_total unknown; observe it before importing a finite percentage cap')
            converted=budget['quota_total']*budget['max_percent']/100
            if budget['max_observed_spend'] is not None and not math.isclose(budget['max_observed_spend'],converted):
                raise ValueError('Native spend cap conflicts with percentage conversion')
            budget['max_observed_spend']=converted
        if budget['max_observed_spend'] is not None or budget['minimum_remaining'] is not None:
            if role == 'orchestrator': raise ValueError('Codex native quota has no reliable local hard-stop adapter; keep budget null and observe UI')
            if budget['unit'] is None: raise ValueError('Finite external budget requires observed native unit')
            if budget['max_observed_spend'] is not None and budget['baseline'] is None:
                raise ValueError('Observed spend budget needs current observed balance baseline')
        cfg.setdefault('quota_period', 'monthly' if member == 'kimi' else 'observe_current_client')
        _text(cfg['quota_period'], 'quota period')
        if member == 'kimi' and cfg['quota_period'] != 'monthly': raise ValueError('Kimi quota is monthly; do not assume daily reset')
        fallback = cfg.setdefault('fallback', {'authorized':False, 'authorization_source':None, 'on':[], 'requested':None})
        _keys(fallback, ('authorized','authorization_source','on','requested'), 'fallback')
        if type(fallback.get('authorized')) is not bool: raise ValueError('Fallback needs explicit authorized boolean')
        if fallback['authorized']:
            _text(fallback.get('authorization_source'), 'fallback authorization')
            if (not isinstance(fallback.get('on'),list) or not fallback['on']
                    or any(x not in ('model_unavailable','client_error','quota_exhausted') for x in fallback['on'])):
                raise ValueError('Fallback needs explicit failure conditions')
            r=fallback.get('requested'); _keys(r,('model','reasoning_effort','speed'),'fallback requested')
            _text(r.get('model'),'fallback model')
            if r.get('reasoning_effort') not in EFFORTS or r.get('speed') not in (None,'fast','standard','preserve_current'):
                raise ValueError('Invalid fallback requested settings')
        elif fallback.get('requested') is not None or fallback.get('on'):
            raise ValueError('Unapproved fallback cannot include an executable plan')
    normalized=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return {'config':data, 'sha256':hashlib.sha256(normalized).hexdigest(), 'boundary':copy.deepcopy(BOUNDARY)}

def profile_import(tool, workspace, source):
    p=tool.verify_instance(workspace)
    registry=tool.run_ctl(p,['roles'])
    record=load_config(source,registry); cfg=record['config']
    folder=p/'inputs/run_profiles'
    if (p/'inputs').is_symlink() or folder.is_symlink(): raise ValueError('Profile storage cannot be a symlink')
    folder.mkdir(exist_ok=True)
    target=folder/(cfg['run_id']+'.json'); active=folder/'active.json'
    if target.is_symlink() or active.is_symlink(): raise ValueError('Profile record cannot be a symlink')
    if target.exists():
        old=json.loads(target.read_text())
        if old['sha256'] != record['sha256']: raise ValueError('Frozen run_id cannot be changed; use a new run_id')
        if not active.exists() or json.loads(active.read_text())['run_id']!=cfg['run_id']:
            raise ValueError('Do not reactivate an old run; use a new run_id')
        return old
    record['frozen_at']=datetime.now(timezone.utc).isoformat()
    policy=json.loads((p/'.cluster/budget_policy.json').read_text())
    policy['policy_id']='run-profile-'+cfg['run_id']+'-'+record['sha256'][:12]
    policy['authorization_source']=cfg['authorization_source']
    for role in ('primary_executor','secondary_executor'):
        r=cfg['roles'][role]; member=r['member']
        q=copy.deepcopy(policy['providers'].get(member,registry['members'][member].get('policy',policy['providers']['kimi'])))
        for key in LIMITS: q[key]=r[key]
        if r.get('network_access') is not None:q['network_access']=r['network_access']
        q.update({k:r['budget'][k] for k in ('max_observed_spend','minimum_remaining','baseline','unit')})
        q['quota_policy']='explicit_limit' if any(r['budget'][k] is not None for k in ('max_observed_spend','minimum_remaining')) else 'observe_and_report'
        policy['providers'][member]=q
    # Validate compiled provider policy using the frozen module without mutation.
    import sys
    sys.path.insert(0,str(p))
    from cluster.permissions import validate_policy
    validate_policy(policy)
    bindings={role:cfg['roles'][role]['member'] for role in ROLES}
    binding_file=folder/(cfg['run_id']+'.bindings.json'); tool.write_json(binding_file,bindings)
    tool.run_ctl(p,['configure-roles','--file',str(binding_file),'--authority',cfg['authorization_source']])
    tool.write_json(p/'.cluster/budget_policy.json',policy)
    record['policy_id']=policy['policy_id']; record['bindings']=bindings
    record['policy_sha256']=hashlib.sha256((p/'.cluster/budget_policy.json').read_bytes()).hexdigest()
    # Exclusive immutable identity; profiles authorize settings requests only.
    with target.open('x',encoding='utf-8') as f: f.write(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    tool.write_json(active,{'run_id':cfg['run_id'],'sha256':record['sha256']})
    return record

def read_active(tool,workspace):
    p=tool.verify_instance(workspace); folder=p/'inputs/run_profiles'
    if (p/'inputs').is_symlink() or folder.is_symlink() or (folder/'active.json').is_symlink(): raise ValueError('Unsafe profile storage')
    active=json.loads((folder/'active.json').read_text())
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',active.get('run_id','')): raise ValueError('Unsafe active run_id')
    target=folder/(active['run_id']+'.json')
    if target.is_symlink(): raise ValueError('Unsafe profile record')
    r=json.loads(target.read_text())
    if r['sha256']!=active['sha256']: raise ValueError('Active profile identity mismatch')
    normalized=json.dumps(r['config'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    if hashlib.sha256(normalized).hexdigest()!=r['sha256']: raise ValueError('Frozen profile content changed')
    if tool.run_ctl(p,['roles'])['bindings']!=r['bindings'] or hashlib.sha256((p/'.cluster/budget_policy.json').read_bytes()).hexdigest()!=r['policy_sha256']:
        raise ValueError('Instance roles/policy changed since profile import; import a new authorized run_id')
    return p,r

def all_tasks(tool,p,mode='live'):
    return [tool.run_ctl(p,['show',t['task_id']],mode) for t in tool.run_ctl(p,['list'],mode)]

def dispatch_guard(tool,p,args,mode='live'):
    """Persisted started/confirmed history counts even after cancellation."""
    if not args or args[0] not in ('prepare','begin'): return
    m=tool.run_ctl(p,['show',args[1]],mode)
    record=m.get('spec',{}).get('run_profile_snapshot')
    if not record: return
    phase='producer'
    if args[0]=='prepare':
        if '--phase' in args: phase=args[args.index('--phase')+1]
    else:
        sid=args[args.index('--submission')+1] if '--submission' in args else None
        phase=next((s['phase'] for s in m['submissions'] if s['submission_id']==sid),'producer')
    provider=m['producer'] if phase=='producer' else m['reviewer']
    provider=m.get('member_profiles',{}).get(provider,{}).get('platform',provider)
    cfg=record['config']; member=next((c for c in cfg['roles'].values() if c['member']==provider),None)
    if member is None: raise ValueError('Dispatch provider lies outside frozen run profile')
    tasks=[t for t in all_tasks(tool,p,mode) if t.get('spec',{}).get('run_profile_snapshot',{}).get('config',{}).get('run_id')==cfg['run_id'] or m.get('assignment') and (t.get('assignment') or {}).get('authorization_source')==m['assignment']['authorization_source']]
    attempted=[s for t in tasks for s in t['submissions'] if t.get('member_profiles',{}).get(s['provider'],{}).get('platform',s['provider'])==provider and
               (s.get('confirmed_at') or (s.get('started_at') and s['status']!='not_submitted'))]
    if member['max_calls'] is not None and len(attempted)>=member['max_calls']:
        raise ValueError('Run profile cumulative max_calls reached; confirmed/cancelled and unknown intents consume slots')
    budget=member['budget']
    if attempted and any(budget[k] is not None for k in ('max_observed_spend','minimum_remaining')):
        obs=[o for t in tasks for o in t['cost_observations'] if t.get('member_profiles',{}).get(o['provider'],{}).get('platform',o['provider'])==provider and o['unit']==budget['unit'] and o.get('value') is not None]
        if not obs or max(o['observed_at'] for o in obs)<max(s.get('confirmed_at') or s['started_at'] for s in attempted):
            raise ValueError('Finite budget requires current balance after previous attempt; cost remains unknown')

def create_task(tool,workspace,source,mode='live',dry_run=False):
    p,r=read_active(tool,workspace); cfg=r['config']
    # Per-instance launcher lock makes scheduler check+create one critical section.
    import fcntl
    lock=p/'inputs/run_profiles/creation.lock'
    if lock.is_symlink(): raise ValueError('Unsafe scheduler lock')
    with lock.open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        tasks=all_tasks(tool,p,mode)
        count=sum(t.get('spec',{}).get('run_profile_snapshot',{}).get('config',{}).get('run_id')==cfg['run_id'] for t in tasks)
        cap=cfg['roles']['orchestrator']['max_calls']
        if cap is not None and count>=cap: raise ValueError('Codex scheduler Task creation cap reached')
        source=Path(source)
        if source.is_symlink() or not source.is_file(): raise ValueError('Task spec must be a regular JSON file')
        spec=json.loads(source.read_text())
        if 'run_profile_snapshot' in spec or 'assignment_v1' in spec: raise ValueError('Caller cannot replace frozen profile/assignment snapshot')
        if spec.get('policy_overrides') or spec.get('controller_model_preset'):
            raise ValueError('Change run config explicitly instead of overriding its policy/model in Task spec')
        allowed=set(r['bindings'].values())
        allowed.update(a['member'] for a in (cfg.get('assignment_v1') or {}).get('candidates',[]))
        for member in (spec.get('assigned_provider'),spec.get('reviewer_provider'),spec.get('external_benefit',{}).get('provider')):
            if member is not None and member not in allowed: raise ValueError('Task routes outside the frozen role members')
        spec['run_profile_snapshot']=copy.deepcopy(r)
        if cfg.get('assignment_v1'):spec['assignment_v1']=copy.deepcopy(cfg['assignment_v1'])
        if cfg.get('workflow_v1'):
            flow=cfg['workflow_v1']
            for flag,value in (('canonical_workflow_v1',flow['canonical_required']),('native_document_workflow_v1',flow['native_documents_required'])):
                if flag in spec and spec[flag]!=value:raise ValueError('Task cannot bypass active workflow profile: '+flag)
                spec[flag]=value
            if not spec.get('review_cycle_contract'):raise ValueError('Active workflow needs stable unit/scope review_cycle_contract')
        if cfg.get('default_task_route') is not None:spec.setdefault('task_route',cfg['default_task_route'])
        spec['authorization_source']=cfg['authorization_source']
        req=cfg['roles']['orchestrator']['requested']
        preset=next((k for k,v in PRESETS.items() if v==(req['model'],req['reasoning_effort'])),None)
        if preset: spec['controller_model_preset']=preset
        spec['requested_member_settings']={cfg['roles'][role]['member']:copy.deepcopy(cfg['roles'][role]['requested']) for role in ROLES}
        prepared=p/'inputs'/('profile-spec-'+cfg['run_id']+'-'+str(count+1)+'.json')
        tool.write_json(prepared,spec)
        if dry_run:
            if not spec.get('assignment_v1'):raise ValueError('plan-task requires an explicit dynamic assignment profile')
            return tool.run_ctl(p,['assignment-plan','--file',str(prepared)],mode,profile_safe=True)
        # Core create checks the same transactional work-round lineage as
        # new_work; profile_safe bypasses only the launcher entry restriction,
        # never previous-round or dispatch guards.
        result=tool.run_ctl(p,['create','--file',str(prepared)],mode,profile_safe=True)
        return {'task':result,'run_id':cfg['run_id'],'scheduler_tasks_created':count+1,
                'actual_model_calls':None,'actual_tokens':None,'external_calls':0}
