"""Offline document review using exact statements and frozen original inputs."""
import hashlib
import json

PROTOCOL = 'offline_document_v1'

def document_statement_hash(statement):
    """Hash the whole submitted statement, excluding optional reported hash fields."""
    semantic={k:v for k,v in statement.items() if k not in ('semantic_sha','semantic_sha256')}
    return hashlib.sha256(json.dumps(semantic,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def document_statements(candidate):
    if not isinstance(candidate,dict):raise ValueError('Offline candidate must be a JSON object')
    rows=candidate.get('statements',candidate.get('claims',[]))
    if not isinstance(rows,list):raise ValueError('Offline statements must be a list')
    result={}
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('claim_id'),str) or not row['claim_id']:
            raise ValueError('Offline statement needs claim_id')
        if row['claim_id'] in result:raise ValueError('Duplicate offline claim_id')
        result[row['claim_id']]=row
    return result

def document_review_scope(candidate,scope=None):
    statements=document_statements(candidate)
    if scope is None:return list(statements)
    if not isinstance(scope,list) or any(not isinstance(x,str) or not x for x in scope) or len(set(scope))!=len(scope):
        raise ValueError('document_review_scope must contain unique claim IDs')
    if not set(scope)<=set(statements):raise ValueError('Unknown offline review scope claim')
    return list(scope)

def check_document_review(data,candidate,provider,source_ids,scope=None,candidate_artifact_id=None):
    if data.get('sources')!=[] or data.get('claim_checks')!=[]:
        raise ValueError('Offline document review requires sources=[] and claim_checks=[]')
    statements=document_statements(candidate);expected=set(document_review_scope(candidate,scope))
    if any(statements[cid].get('producer')==provider for cid in expected):
        raise ValueError('Self review cannot count as independent')
    covered=set();failed=set();allowed_evidence=set(source_ids)
    if candidate_artifact_id is not None:allowed_evidence.add(candidate_artifact_id)
    for category in ('passed_checks','confirmed_errors','suspicions','unverified'):
        for check in data[category]:
            if not isinstance(check,dict):
                if category=='passed_checks':raise ValueError('Offline passed_checks must be objects')
                continue  # General limitations do not cover a statement.
            cid=check.get('claim_id')
            if cid is None:
                if category=='passed_checks':
                    basis=check.get('basis') or check.get('reason') or check.get('evidence')
                    if not isinstance(check.get('check_id'),str) or not check['check_id'] or not isinstance(basis,str) or not basis.strip():
                        raise ValueError('Additional offline checks need check_id and basis')
                continue
            if cid not in expected or cid in covered:raise ValueError('Unknown, out-of-scope or duplicate offline reviewed claim')
            if check.get('semantic_sha256')!=document_statement_hash(statements[cid]):
                raise ValueError('Offline review refers to stale statement semantics')
            basis=check.get('basis') or check.get('reason') or check.get('evidence')
            if not isinstance(basis,str) or not basis.strip():raise ValueError('Offline claim review needs an explicit basis')
            if 'source_artifacts' in check and (not isinstance(check['source_artifacts'],list) or not set(check['source_artifacts'])<=allowed_evidence):
                raise ValueError('Offline review cites an input outside the frozen package')
            covered.add(cid)
            if category!='passed_checks':failed.add(cid)
    if covered!=expected:raise ValueError('Offline review must cover every scoped statement')
    if failed and data.get('blocking') is not True:raise ValueError('Unverified offline statements must remain blocking')
    return {'scope':list(document_review_scope(candidate,scope)),'passed_claims':len(covered-failed),'blocking_claims':len(failed)}
