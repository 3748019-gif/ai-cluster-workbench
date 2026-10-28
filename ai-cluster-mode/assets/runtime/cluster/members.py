"""Stable business roles with replaceable GUI members; no provider API adapters."""
import copy,json,re
from pathlib import Path
from .policy import PROVIDERS
from .permissions import validate_policy,default_policy

LABELS={'orchestrator':'调度中心','primary_executor':'主执行者','secondary_executor':'副执行者'}
DEFAULT_BINDINGS={'orchestrator':'codex','primary_executor':'kimi','secondary_executor':'workbuddy'}

def defaults():
    return {'schema_version':1,'role_names':LABELS.copy(),'bindings':DEFAULT_BINDINGS.copy(),
            'members':{k:{**copy.deepcopy(v),'display_name':{'codex':'Codex','kimi':'Kimi','workbuddy':'WorkBuddy'}[k],
                          'verification_status':'existing_member_per_task_validation'} for k,v in PROVIDERS.items()}}

def validate(data):
    if data.get('schema_version')!=1 or data.get('role_names')!=LABELS:raise ValueError('Fixed role names cannot change with members')
    bindings=data['bindings'];members=data['members']
    if set(bindings)!=set(LABELS) or bindings['orchestrator']!='codex':raise ValueError('Keep Codex as orchestrator; bind both executor roles')
    if bindings['primary_executor']==bindings['secondary_executor']:raise ValueError('Executors must be different members for independent review')
    for role in ('primary_executor','secondary_executor'):
        name=bindings[role];p=members.get(name)
        if not p or p.get('transport')!='gui' or not {'producer','reviewer'}<=set(p.get('roles',[])):
            raise ValueError('Executor must be a registered GUI member supporting production and independent review')
    return data

def load(root):
    p=Path(root)/'.cluster/roles.json'
    return validate(json.loads(p.read_text()) if p.exists() else defaults())

class MemberMixin:
    def roles(self):return load(self.root)
    def _save_roles(self,data,action,authority):
        from .core import GuardError,atomic,js,now,digest
        if not isinstance(authority,str) or not authority.strip():raise GuardError('Role changes require explicit authorization source')
        validate(data);old=load(self.root)
        rec={'at':now(),'action':action,'authorization_source':authority,'before_sha256':digest(js(old).encode()),'after_sha256':digest(js(data).encode()),'before_bindings':old['bindings'],'after_bindings':data['bindings']}
        atomic(self.root/'.cluster/roles.json',js(data).encode())
        with (self.root/'.cluster/role_changes.jsonl').open('a') as f:f.write(json.dumps(rec,ensure_ascii=False)+'\n')
        return data
    def set_role(self,role,member,authority):
        if role not in ('primary_executor','secondary_executor'):raise ValueError('Only executor bindings are replaceable')
        d=load(self.root);d['bindings'][role]=member
        return self._save_roles(d,'set_role',authority)
    def configure_roles(self,bindings,authority):
        d=load(self.root);d['bindings']=dict(bindings)
        return self._save_roles(d,'configure_roles',authority)
    def register_member(self,spec):
        name=spec.get('member_id','');d=load(self.root)
        if not re.fullmatch(r'[a-z][a-z0-9_-]{1,39}',name) or name in d['members']:raise ValueError('Use a new stable member ID; existing profiles are not overwritten')
        if not spec.get('app_name') or not spec.get('display_name'):raise ValueError('Member needs display_name and app_name')
        if spec.get('transport','gui')!='gui':raise ValueError('External members use authorized Computer Use only')
        policy=spec.get('policy')
        if not isinstance(policy,dict):raise ValueError('New member needs an explicit permission policy, not inherited payment authority')
        check=default_policy();check['providers'][name]=policy;validate_policy(check)
        d['members'][name]={'display_name':spec['display_name'],'app_name':spec['app_name'],'transport':'gui',
                           'roles':['producer','reviewer','researcher','drafter'],'capabilities':spec.get('capabilities',[]),
                           'verification_status':'unverified','policy':policy,'authorization_source':spec.get('authorization_source')}
        return self._save_roles(d,'register_member',spec.get('authorization_source'))
    def verify_member(self,member,task):
        from .core import GuardError
        d=load(self.root);m=self.get(task)
        if member not in d['members'] or m['run_mode']!='live' or m['status']!='DELIVERED' or not self.audit(task)['passed']:
            raise GuardError('Verification requires a delivered, audited live capability pilot')
        if not m['spec'].get('capability_pilot') or not any(m.get('member_profiles',{}).get(s['provider'],{}).get('platform',s['provider'])==member and s['status']=='completed' for s in m['submissions']):
            raise GuardError('This live pilot did not complete work with this member')
        d['members'][member]['verification_status']='verified_for_recorded_pilot'
        d['members'][member]['verification_scope']={'task_id':task,'task_type':m['task_type'],'objective':m['objective']}
        return self._save_roles(d,'verify_member','Evidence from live pilot '+task)
