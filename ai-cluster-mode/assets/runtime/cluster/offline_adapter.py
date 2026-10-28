"""Deterministic envelope binding for an already confirmed offline review.

No findings, scope, source-reading claim, verdict or evidence is invented here.
"""
import copy
from .document_review import document_statements, document_statement_hash


def _supplementary_notes(out,m,candidate,candidate_data,scope,repairs):
    """Move explicitly nonblocking check references out of claim-check fields.

    This narrow adapter requires existing independent positive scope coverage.
    It preserves every reference value and the note's wording. It neither fixes
    an asserted bad hash nor upgrades an unverified fact to a positive check.
    """
    statements=document_statements(candidate_data);current=set(scope)
    contract=m.get('spec',{}).get('review_cycle_contract') or {}
    stable=set(contract.get('scope_item_ids',statements))
    if not current<=stable or not stable<=set(statements):raise ValueError('Offline stable/current scope conflicts with frozen candidate')
    eligible=[]
    for index,row in enumerate(out.get('unverified',[])):
        if (isinstance(row,dict) and isinstance(row.get('check_id'),str) and row['check_id'].strip()
                and row.get('blocking') is False
                and any(k in row for k in ('claim_id','affected_ids','semantic_sha256'))):
            eligible.append((index,row))
    if not eligible:return
    # This is a format-only continuation: all current claims must already have
    # a correct independent positive check, not a fabricated adapter default.
    passed={};allowed_sources=set(m.get('source_files',[]))|{candidate['artifact_id']}
    package_id=m.get('packages',{}).get('reviewer')
    package=next((a for a in m.get('artifacts',[]) if a.get('artifact_id')==package_id),None)
    if package and 'source_artifact_ids' in package:allowed_sources=set(package['source_artifact_ids'])|{candidate['artifact_id']}
    def check_explicit_identity(row):
        for key,expected in (('candidate_artifact_id',candidate['artifact_id']),('candidate_sha256',candidate['hash'])):
            if key in row and row[key]!=expected:raise ValueError('Explicit supplementary candidate conflict: '+key)
        if 'source_artifacts' in row:
            refs=row['source_artifacts']
            if not isinstance(refs,list) or any(not isinstance(x,str) for x in refs) or not set(refs)<=allowed_sources:
                raise ValueError('Supplementary adaptation cites unknown frozen sources')
    for row in out.get('passed_checks',[]):
        if not isinstance(row,dict) or row.get('claim_id') is None:continue
        ident=row['claim_id']
        if ident not in current or ident in passed:raise ValueError('Supplementary adaptation needs unique current-scope formal checks')
        if row.get('blocking') is True or row.get('result') not in (None,'pass','passed','verified'):
            raise ValueError('Unverified formal claim cannot authorize supplementary adaptation')
        if row.get('semantic_sha256')!=document_statement_hash(statements[ident]):
            raise ValueError('Supplementary adaptation needs an already correct controller statement hash')
        if not isinstance(row.get('basis'),str) or not row['basis'].strip():raise ValueError('Formal independent check needs its original basis')
        check_explicit_identity(row);passed[ident]=row
    if set(passed)!=current:raise ValueError('Supplementary adaptation requires complete current independent passed coverage')
    for category in ('confirmed_errors','suspicions','unverified'):
        for row in out.get(category,[]):
            if not isinstance(row,dict):continue
            is_note=any(row is note for _,note in eligible)
            ident=row.get('claim_id')
            if not is_note and ident in current:
                raise ValueError('A genuine unresolved scoped claim cannot be adapted into a supplementary notice')
    for index,row in eligible:
        check_explicit_identity(row)
        if 'item_id' in row:raise ValueError('Ambiguous supplementary item identity')
        ident=row.get('claim_id')
        if ident is not None and (not isinstance(ident,str) or ident not in stable):raise ValueError('Supplementary claim ID is outside stable scope')
        affected=row.get('affected_ids',[])
        if not isinstance(affected,list) or any(not isinstance(i,str) for i in affected) or len(affected)!=len(set(affected)) or not set(affected)<=stable:
            raise ValueError('Supplementary affected IDs are outside stable scope')
        referenced=set(affected)|({ident} if ident else set())
        if not referenced:raise ValueError('Supplementary metadata has no known referenced claim')
        if not (referenced&current)<=set(passed):raise ValueError('Referenced current claim has no formal independent pass')
        proof=[]
        if 'semantic_sha256' in row and row['semantic_sha256'] is not None:
            hash_ident=ident or (next(iter(referenced)) if len(referenced)==1 else None)
            if hash_ident is None:raise ValueError('Supplementary hash has ambiguous referenced claim')
            wrapper=document_statement_hash(statements[hash_ident]);view_hashes={}
            views=candidate_data.get('canonical_view_documents',{})
            if isinstance(views,dict):
                for name,view in views.items():
                    mapping=view.get('semantic_mapping',{}) if isinstance(view,dict) else {}
                    entry=mapping.get(hash_ident,{}) if isinstance(mapping,dict) else {}
                    if isinstance(entry,dict) and isinstance(entry.get('semantic_sha256'),str):view_hashes[name]=entry['semantic_sha256']
            reported=row['semantic_sha256'];domains=[]
            if reported==wrapper:domains.append('controller_statement')
            domains += ['canonical_view:'+name for name,value in view_hashes.items() if reported==value]
            if not domains:raise ValueError('Supplementary reference hash does not match any exact frozen hash domain')
            proof=[{'claim_id':hash_ident,'reported_sha256':reported,'controller_statement_sha256':wrapper,
                    'canonical_view_item_sha256':view_hashes,'matched_domains':domains}]
        changes=[]
        for old,new in (('claim_id','referenced_claim_id'),('affected_ids','referenced_affected_ids'),('semantic_sha256','referenced_semantic_sha256')):
            if old not in row:continue
            value=copy.deepcopy(row[old])
            if new in row and row[new]!=value:raise ValueError('Explicit supplementary reference metadata conflict: '+new)
            row[new]=value;del row[old]
            changes.append({'from_field':old,'to_field':new,'old_value':value,'new_value':copy.deepcopy(value)})
        repairs.append({'field':'unverified['+str(index)+']','kind':'supplementary_reference_metadata',
            'check_id':row['check_id'],'changes':changes,'reference_hash_proof':proof,
            'current_scope_proof':[{'claim_id':i,'semantic_sha256':document_statement_hash(statements[i])} for i in sorted(current)],
            'authority':'explicit check_id and blocking=false; already complete independent passed coverage; exact frozen reference values',
            'counts_as_scoped_coverage':False,'underlying_facts_verified':False,'body_or_fact_text_changed':False})


def bind_offline_review(data,m,submission,candidate,candidate_data,scope):
    if not isinstance(data,dict):raise ValueError('Offline review must be an object')
    out=copy.deepcopy(data);repairs=[]
    known={'task_id':m['task_id'],'submission_id':submission['submission_id'],
           'candidate_artifact_id':candidate['artifact_id'],'candidate_sha256':candidate['hash'],
           'sources':[],'claim_checks':[]}
    for key,value in known.items():
        if key in out and out[key]!=value:raise ValueError('Explicit offline envelope conflict: '+key)
        if key not in out:out[key]=value;repairs.append({'field':key,'authority':'confirmed offline submission and exact package'})
    _supplementary_notes(out,m,candidate,candidate_data,scope,repairs)
    statements=document_statements(candidate_data);allowed=set(scope)
    for category in ('passed_checks','confirmed_errors','suspicions','unverified'):
        for row in out.get(category,[]):
            if not isinstance(row,dict) or row.get('claim_id') not in allowed:continue
            exact=document_statement_hash(statements[row['claim_id']])
            if 'semantic_sha256' in row and row['semantic_sha256']!=exact:raise ValueError('Explicit offline statement hash conflict')
            if 'semantic_sha256' not in row:
                row['semantic_sha256']=exact;repairs.append({'field':category+'.semantic_sha256','claim_id':row['claim_id'],'authority':'exact frozen candidate'})
    return out,repairs
