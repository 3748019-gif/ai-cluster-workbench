"""Requested orchestrator presets, separate from observed application settings.

This module records configuration only. It never switches an app model or
calls a model backend, and it does not alter member roles or routing.
"""
import copy
import json
from datetime import datetime
from pathlib import Path

PRESETS={
    'gpt-6.1-sol/current':{'model':'gpt-6.1-sol','reasoning_effort':None},
    'gpt-6.1-sol/ultra':{'model':'gpt-6.1-sol','reasoning_effort':'ultra'},
    'gpt-6-astra/ultra':{'model':'gpt-6-astra','reasoning_effort':'ultra'},
    'gpt-6-astra/xhigh':{'model':'gpt-6-astra','reasoning_effort':'xhigh'},
}
DEFAULT_PRESET='gpt-6.1-sol/current'
EFFORTS=('none','minimal','low','medium','high','xhigh','max','ultra')

def requested(preset):
    if not isinstance(preset,str) or preset not in PRESETS:raise ValueError('CONFIG_ERROR: unknown controller model preset')
    return {'preset':preset,**copy.deepcopy(PRESETS[preset])}

def unknown_observed():
    return {'status':'unknown','model':None,'reasoning_effort':None,'service_tier':None,'speed':None}

def load(root):
    path=Path(root)/'.cluster/controller_model.json'
    if not path.exists():
        return {'schema_version':1,'requested':requested(DEFAULT_PRESET),'authorization_source':None,
                'configured_at':None,'source':'default_preserve_current','application_model_changed':False}
    data=json.loads(path.read_text())
    if data.get('schema_version')!=1 or data.get('requested')!=requested(data.get('requested',{}).get('preset')):
        raise ValueError('CONFIG_ERROR: invalid controller model config')
    if not isinstance(data.get('authorization_source'),str) or not data['authorization_source'].strip():
        raise ValueError('CONFIG_ERROR: controller model config needs authorization source')
    if data.get('application_model_changed') is not False:raise ValueError('CONFIG_ERROR: config cannot claim an app model switch')
    return data

def snapshot(root,spec):
    from .core import now
    config=load(root)
    if 'controller_model_preset' in spec:
        if not isinstance(spec.get('authorization_source'),str) or not spec['authorization_source'].strip():
            raise ValueError('CONFIG_ERROR: per-round controller preset needs authorization_source')
        config={'requested':requested(spec['controller_model_preset']),
                'authorization_source':spec['authorization_source'],'source':'spec_override'}
    return {'requested':copy.deepcopy(config['requested']),'authorization_source':config['authorization_source'],
            'request_source':config['source'],'frozen_at':now(),'observed':unknown_observed(),'observations':[]}

class ControllerModelMixin:
    def controller_model_presets(self):
        return {'presets':[requested(p) for p in PRESETS],'default_preset':DEFAULT_PRESET,
                'application_model_changed':False}

    def controller_model_config(self):return copy.deepcopy(load(self.root))

    def configure_controller_model(self,preset,authority):
        from .core import GuardError,atomic,js,now,digest
        if not isinstance(authority,str) or not authority.strip():raise GuardError('Controller preset requires explicit authorization source')
        old=load(self.root)
        config={'schema_version':1,'requested':requested(preset),'authorization_source':authority,
                'configured_at':now(),'source':'project_config','application_model_changed':False}
        atomic(self.root/'.cluster/controller_model.json',js(config).encode())
        change={'at':config['configured_at'],'authorization_source':authority,
                'before_sha256':digest(js(old).encode()),'after_sha256':digest(js(config).encode()),
                'requested':config['requested'],'application_model_changed':False}
        with (self.root/'.cluster/controller_model_changes.jsonl').open('a') as f:
            f.write(json.dumps(change,ensure_ascii=False)+'\n')
        return config

    def record_controller_model_observation(self,task,observation):
        from .core import GuardError,now
        if not isinstance(observation,dict):raise GuardError('Controller observation must be an object')
        allowed={'kind','observed_at','evidence_ref','model','reasoning_effort','service_tier','speed','note'}
        if set(observation)-allowed:raise GuardError('Unknown controller observation field')
        kind=observation.get('kind')
        if kind not in ('ui','turn_context','unknown'):raise GuardError('Controller observation needs ui, turn_context or unknown kind')
        try:stamp=datetime.fromisoformat(observation['observed_at'])
        except (KeyError,TypeError,ValueError):raise GuardError('Controller observation needs ISO observed_at')
        if stamp.tzinfo is None:raise GuardError('Controller observation needs timezone')
        if not isinstance(observation.get('evidence_ref'),str) or not observation['evidence_ref'].strip():
            raise GuardError('Controller observation needs an explicit evidence_ref; it is not fetched automatically')
        observed={key:observation.get(key) for key in ('model','reasoning_effort','service_tier','speed')}
        for key,value in observed.items():
            if value is not None and (not isinstance(value,str) or not value.strip()):raise GuardError('Observed setting must be text or null')
        if observed['reasoning_effort'] is not None and observed['reasoning_effort'] not in EFFORTS:raise GuardError('Unknown observed reasoning effort')
        if kind=='unknown' and any(v is not None for v in observed.values()):raise GuardError('Unknown observations cannot infer actual settings')
        if kind!='unknown' and all(v is None for v in observed.values()):raise GuardError('Known observation must record at least one observed setting')
        observed['status']='unknown' if kind=='unknown' else ('observed' if all(observed[k] is not None for k in ('model','reasoning_effort')) else 'partially_observed')
        record={**copy.deepcopy(observation),'settings':observed,'recorded_at':now()}
        with self._mutate(task) as m:
            if not m.get('controller_model'):raise GuardError('Historical task has no frozen controller config; do not infer one')
            state=m['controller_model'];state['observations'].append(record)
            latest=max(enumerate(state['observations']),key=lambda pair:(datetime.fromisoformat(pair[1]['observed_at']),pair[0]))[1]
            state['observed']=copy.deepcopy(latest['settings'])
            self._event(m,'CONTROLLER_MODEL_OBSERVED','orchestrator','Explicit model observation; requested configuration retained',extra=record)
        return copy.deepcopy(self.get(task)['controller_model'])
