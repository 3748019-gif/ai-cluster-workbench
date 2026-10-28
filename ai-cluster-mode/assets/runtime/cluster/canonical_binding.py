"""Bind content review to the one master and both generated views."""
import json


class CanonicalBindingMixin:
    def canonical_candidate(self,task):
        m=self.get(task)
        self._canonical_gate(m)
        state=m['canonical']
        draft=json.loads(self.read_artifact(m,state['draft']))
        statements=[{'claim_id':x['id'],'producer':m['producer'],'canonical_item':x} for x in draft['items']]
        statements += [{'claim_id':x['id'],'producer':m['producer'],'canonical_group':x} for x in draft['groups']]
        return {'task_id':task,'statements':statements,
                'canonical_draft_sha256':self._fresh(m,state['draft'])['hash'],
                'canonical_inventory_sha256':self._fresh(m,state['inventory'])['hash'],
                'canonical_views':{k:v['sha256'] for k,v in state['views'].items()},
                'canonical_view_documents':{k:json.loads(self.read_artifact(m,v['artifact_id'])) for k,v in state['views'].items()}}

    def _canonical_review_binding_gate(self,m):
        from .core import GuardError
        if not m['spec'].get('canonical_workflow_v1'):return
        self._canonical_gate(m)
        if not m.get('candidate'):raise GuardError('Canonical review requires exact generated candidate')
        expected=self.canonical_candidate(m['task_id'])
        actual=json.loads(self.read_artifact(m,m['candidate']))
        for key in ('statements','canonical_draft_sha256','canonical_inventory_sha256','canonical_views','canonical_view_documents'):
            if actual.get(key)!=expected[key]:raise GuardError('Review candidate differs from canonical master/views: '+key)
        contract=m['spec'].get('review_cycle_contract')
        ids={x['claim_id'] for x in expected['statements']}
        if not contract or set(contract['scope_item_ids'])!=ids:
            raise GuardError('Independent review scope must cover canonical items and shared groups')
