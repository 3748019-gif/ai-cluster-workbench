"""Structured source cards and exact-claim independent review checks."""
import hashlib
import json
import copy
import re
from datetime import datetime
from .permissions import public_url
from .handoff import HandoffError, issue, MEMBER_CONTENT

RESULTS=('verified','confirmed_error','evidence_gap','scope_or_time_ambiguity','access_failed','wording_suggestion')
SOURCE_FIELDS=('source_id','collected_by','url','title','published_at','updated_at','accessed_at','access_status',
               'applicable_cohort_or_version','level','scope','locator','quote','excerpt_is_complete',
               'omissions_marked','supports_claim_ids','limits','raw_artifact_id')
CLAIM_FIELDS=('claim_id','text','scope','time','producer','source_ids','location')

def normalize_metadata(data,provider=None,boolean_strings=False):
    """Conservative schema repair; never invent a timestamp, full-page access or content."""
    result=copy.deepcopy(data);changes=[]
    cards=result.get('sources',[])
    if not isinstance(cards,list):return result,changes
    for index,s in enumerate(cards):
        if not isinstance(s,dict):continue
        if boolean_strings and 'id' in s and 'source_id' not in s:
            # A local source-card identifier alias, never a guessed URL,
            # registered artifact identity, access status or evidence content.
            s['source_id']=copy.deepcopy(s['id'])
            changes.append({'source_id':s['source_id'],'field':f'sources[{index}].source_id',
                            'action':'copy explicit id alias verbatim; retain original id',
                            'before_present':False,'after':copy.deepcopy(s['id'])})
        if boolean_strings and isinstance(s.get('url'),str):
            annotated=re.fullmatch(r'(https?://[^\s（]+)（canonical，node/\d+）',s['url'])
            if annotated:
                original=s['url'];s['reported_url']=original;s['url']=annotated.group(1)
                changes.append({'source_id':s.get('source_id'),'field':'url','action':'remove explicit trailing canonical/node display annotation; retain exact original','before':original,'after':s['url']})
        alias=s.get('collected_by')
        if provider and alias in (provider+'-reviewer',provider+'-researcher',provider+'-drafter'):
            s['reported_collected_by']=alias;s['collected_by']=provider
            changes.append({'source_id':s.get('source_id'),'field':'collected_by','action':'strip known role suffix from confirmed submitting provider; retain reported alias'})
        if isinstance(s.get('limits'),str):
            s['limits']=[s['limits']]
            changes.append({'source_id':s.get('source_id'),'field':'limits','action':'wrap exact original string in single-element list'})
        if boolean_strings and s.get('access_status')=='access_failed' and 'quote' in s and s['quote'] is None:
            # Represent the same absence in the old string transport without
            # inventing an excerpt or upgrading failed access. Diagnostics are
            # checked by the member protocol; the exact raw null is retained.
            s['quote']=''
            changes.append({'source_id':s.get('source_id'),'field':'quote',
                            'action':'encode absent failed-access excerpt as empty string; no content inferred',
                            'before':None,'after':''})
        stamp=s.get('accessed_at')
        if isinstance(stamp,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',stamp):
            try:datetime.strptime(stamp,'%Y-%m-%d')
            except ValueError:pass
            else:
                s['reported_accessed_at']=stamp;s['accessed_at']=None
                s['access_time_note']='Member supplied date only; exact time and timezone unknown'
            changes.append({'source_id':s.get('source_id'),'field':'accessed_at','action':'retain reported date; exact timestamp null'})
        elif boolean_strings and isinstance(stamp,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[（(\s].+|快照.*)',stamp):
            # A date followed by a snapshot/access note is not an access instant.
            # Retain the entire statement and do not extract its date as access.
            try:datetime.strptime(stamp[:10],'%Y-%m-%d')
            except ValueError:continue
            s['reported_access_description']=stamp;s['accessed_at']=None
            s['access_time_note']='Member supplied an annotated date or snapshot description; exact access time and date meaning are unknown. Original statement retained.'
            changes.append({'source_id':s.get('source_id'),'field':'accessed_at','action':'retain annotated date verbatim; do not infer snapshot date as access; exact time null','before':stamp,'after':None})
        if s.get('access_status') in ('ok','partial'):
            s['reported_access_status']=s['access_status'];s['access_status']='reported_access'
            s['access_status_note']='Original status did not establish full-page versus snippet access; independent verifier must check'
            changes.append({'source_id':s.get('source_id'),'field':'access_status','action':'conservative reported_access, not upgraded to full_text'})
    if boolean_strings:
        def adapt(obj,key,path):
            if isinstance(obj,dict) and isinstance(obj.get(key),str) and obj[key].strip().lower() in ('true','false'):
                original=obj[key];obj[key]=original.strip().lower()=='true'
                changes.append({'field':path,'action':'typed boolean string adaptation','before':original,'after':obj[key]})
        adapt(result,'blocking','blocking')
        for index,s in enumerate(cards):
            for key in ('excerpt_is_complete','omissions_marked'):
                adapt(s,key,f'sources[{index}].{key}')
        for index,check in enumerate(result.get('claim_checks',[]) if isinstance(result.get('claim_checks'),list) else []):
            adapt(check,'blocking',f'claim_checks[{index}].blocking')
            if isinstance(check,dict):adapt(check.get('applicability'),'context_complete',f'claim_checks[{index}].applicability.context_complete')
        for category in ('wording_suggestions','semantic_findings'):
            entries=result.get(category,[])
            for index,finding in enumerate(entries if isinstance(entries,list) else []):
                for key in ('meaning_changed','blocking'):adapt(finding,key,f'{category}[{index}].{key}')
                if isinstance(finding,dict):adapt(finding.get('resolution'),'independently_verified',f'{category}[{index}].resolution.independently_verified')
    return result,changes

def claim_hash(claim):
    semantic={k:claim.get(k) for k in ('claim_id','text','scope','time','location','source_ids')}
    # Preserve historical hashes when these fields do not exist. New typed
    # conditions/source versions are semantic, even when displayed prose stays.
    for key in ('applicability','quantitative_assertion','semantic_context','source_versions','condition_dependencies'):
        if key in claim:semantic[key]=claim[key]
    return hashlib.sha256(json.dumps(semantic,
        ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def _finish(errors,own,skipped):
    if own and errors:raise HandoffError(errors,skipped)

def _strings(value):
    return isinstance(value,list) and all(isinstance(x,str) and x for x in value)

def check_sources(data,provider=None,domains=None,*,protocol=None,issues=None,skipped=None):
    own=issues is None;errors=[] if own else issues;skipped=[] if skipped is None else skipped
    cards=data.get('sources',[])
    if not isinstance(cards,list):
        errors.append(issue('array_required','sources','sources must be a list'))
        skipped.append({'location':'sources[*]','reason':'Source list prerequisite missing'})
        _finish(errors,own,skipped);return set()
    seen=set()
    for index,s in enumerate(cards):
        path=f'sources[{index}]'
        def bad(code,key,message,**kw):errors.append(issue(code,path+'.'+key,message,**kw))
        if not isinstance(s,dict):
            errors.append(issue('object_required',path,'Source card must be an object'))
            skipped.append({'location':path+'.fields','reason':'Source object prerequisite missing'});continue
        for key in SOURCE_FIELDS:
            if key not in s:bad('required_field',key,'Source card missing fields: '+key)
        ident=s.get('source_id')
        if not isinstance(ident,str) or not ident or ident in seen:bad('source_identity','source_id','Duplicate/missing source_id')
        else:seen.add(ident)
        if 'url' in s:
            try:valid_url=isinstance(s['url'],str) and public_url(s['url'],domains)
            except (ValueError,TypeError):valid_url=False
            if not valid_url:bad('public_url','url','Source must be an authorized public URL')
        if provider and 'collected_by' in s and s['collected_by']!=provider:bad('collector','collected_by','Source collector mismatch',category='identity')
        if 'title' in s and (not isinstance(s['title'],str) or not s['title']):bad('source_text','title','Invalid source text: title')
        if 'quote' in s:
            quote=s['quote']
            if protocol==MEMBER_CONTENT:
                no_excerpt=quote is None or (isinstance(quote,str) and not quote.strip())
                if no_excerpt and s.get('access_status')=='access_failed':
                    locator=s.get('locator');limits=s.get('limits')
                    if (not isinstance(locator,str) or not locator.strip() or not _strings(limits)
                            or not limits or not any(x.strip() for x in limits)):
                        bad('access_failure_diagnostic','quote','An absent failed-access excerpt needs actual failure locator and nonempty limits; no body text is inferred',category='evidence')
                elif not isinstance(quote,str) or not quote.strip():
                    bad('source_text','quote','Nonempty original excerpt required unless access_failed has explicit diagnostics')
            elif not isinstance(quote,str):bad('source_text','quote','Invalid source text: quote')
        if 'accessed_at' in s:
            if s['accessed_at'] is None:
                if not isinstance(s.get('access_time_note'),str) or not s['access_time_note']:bad('access_time','access_time_note','Unknown access time requires an explicit note')
            else:
                try:
                    stamp=datetime.fromisoformat(s['accessed_at'].replace('Z','+00:00')) if isinstance(s['accessed_at'],str) else None
                    if stamp is None or stamp.tzinfo is None:raise ValueError('accessed_at requires timezone')
                except ValueError:bad('access_time','accessed_at','accessed_at requires a valid timestamp with timezone')
        allowed=('full_text','partial_text','search_snippet','access_failed','reported_access')
        if 'access_status' in s and s['access_status'] not in allowed:bad('access_status','access_status','Invalid access_status',allowed=allowed)
        if s.get('raw_artifact_id') is not None:bad('artifact_identity','raw_artifact_id','Members cannot invent local raw artifact IDs',category='identity')
        for k in ('excerpt_is_complete','omissions_marked'):
            if k in s and type(s[k]) is not bool:bad('boolean_required',k,'Source flag must be boolean',allowed=[True,False])
        for key in ('limits','supports_claim_ids'):
            if key in s and not _strings(s[key]):bad('string_array_required',key,'Source arrays required: strings only')
    _finish(errors,own,skipped)
    return seen

def check_research(data,provider=None,domains=None,*,protocol=None,issues=None,skipped=None):
    own=issues is None;errors=[] if own else issues;skipped=[] if skipped is None else skipped
    ids=check_sources(data,provider,domains,protocol=protocol,issues=errors,skipped=skipped)
    if not isinstance(data.get('draft_markdown'),str) or not data['draft_markdown'].strip():errors.append(issue('draft_required','draft_markdown','Usable draft_markdown required'))
    claims=data.get('claims')
    if not isinstance(claims,list) or not claims:
        errors.append(issue('claims_required','claims','claims required'));skipped.append({'location':'claims[*]','reason':'Nonempty claims list prerequisite missing'})
        _finish(errors,own,skipped);return {'claims':0,'sources':len(ids),'semantic_support':'requires independent review'}
    seen=set()
    for index,x in enumerate(claims):
        path=f'claims[{index}]'
        if not isinstance(x,dict):errors.append(issue('object_required',path,'Claim must be an object'));continue
        for key in CLAIM_FIELDS:
            if key not in x:errors.append(issue('required_field',path+'.'+key,'Claim missing fields: '+key))
        cid=x.get('claim_id')
        if isinstance(cid,str):
            if cid in seen:errors.append(issue('duplicate_claim',path+'.claim_id','Duplicate claim_id'))
            seen.add(cid)
        refs=x.get('source_ids')
        if not _strings(refs) or not refs:errors.append(issue('source_references',path+'.source_ids','Claim cites unknown/missing source'))
        elif isinstance(data.get('sources',[]),list) and not set(refs)<=ids:errors.append(issue('unknown_source',path+'.source_ids','Claim cites unknown/missing source'))
        else:
            if not isinstance(data.get('sources',[]),list):skipped.append({'location':path+'.source_ids','reason':'Source ID resolution prerequisite missing'})
        if provider and 'producer' in x and x['producer']!=provider:errors.append(issue('producer','claims['+str(index)+'].producer','Claim producer mismatch; inherited claims require explicit separate lineage',category='identity'))
        for key in ('claim_id','text','scope','location','producer'):
            if key in x and (not isinstance(x[key],str) or not x[key]):errors.append(issue('claim_text',path+'.'+key,'Invalid claim: '+key))
    _finish(errors,own,skipped)
    return {'claims':len(claims),'sources':len(ids),'semantic_support':'requires independent review'}

def check_review(data,candidate,provider,*,protocol=None,contributors=None,enforce_semantics=True,issues=None,skipped=None,domains=None):
    own=issues is None;errors=[] if own else issues;skipped=[] if skipped is None else skipped
    before=len(errors);check_sources(data,provider,domains,protocol=protocol,issues=errors,skipped=skipped);sources_valid=len(errors)==before
    claims={x['claim_id']:x for x in candidate.get('claims',[]) if isinstance(x,dict) and isinstance(x.get('claim_id'),str)}
    checks=data.get('claim_checks',[])
    if not isinstance(checks,list):
        errors.append(issue('array_required','claim_checks','claim_checks must be a list'));skipped.append({'location':'claim_checks[*]','reason':'Review checks list prerequisite missing'})
        _finish(errors,own,skipped);return []
    cards=data.get('sources',[]) if isinstance(data.get('sources'),list) else []
    seen=set();source_ids={s['source_id'] for s in cards if isinstance(s,dict) and isinstance(s.get('source_id'),str)}
    global_authors=set(contributors) if isinstance(contributors,(list,tuple,set)) else set()
    for index,x in enumerate(checks):
        path=f'claim_checks[{index}]'
        if not isinstance(x,dict):errors.append(issue('object_required',path,'Review check must be an object'));continue
        cid=x.get('claim_id')
        if not isinstance(cid,str) or cid not in claims or cid in seen:
            errors.append(issue('reviewed_claim',path+'.claim_id','Unknown/duplicate reviewed claim'))
            skipped.append({'location':path+'.candidate_checks','reason':'Unique known claim prerequisite missing'});continue
        seen.add(cid)
        authors=set(contributors.get(cid,[])) if isinstance(contributors,dict) else global_authors
        if claims[cid].get('producer')==provider or provider in authors:errors.append(issue('self_review',path+'.claim_id','Self review cannot count as independent',category='independence'))
        if x.get('claim_sha256')!=claim_hash(claims[cid]):errors.append(issue('stale_claim',path+'.claim_sha256','Review refers to stale claim semantics',category='identity'))
        if x.get('result') not in RESULTS:errors.append(issue('review_category',path+'.result','Invalid review category',allowed=RESULTS))
        if type(x.get('blocking')) is not bool:errors.append(issue('boolean_required',path+'.blocking','Invalid review category: blocking must be boolean'))
        if x.get('blocking') is False and x.get('result')!='verified':errors.append(issue('unverified_pass',path+'.blocking','An important factual claim needs verified status to pass; put nonblocking wording advice in wording_suggestions',category='content'))
        if not isinstance(x.get('reason'),str) or not x['reason']:errors.append(issue('reason_required',path+'.reason','Review rationale required'))
        refs=x.get('source_ids',[])
        valid_refs=_strings(refs)
        if not valid_refs or not set(refs)<=source_ids:errors.append(issue('unknown_source',path+'.source_ids','Review cites unregistered response source IDs'))
        if x.get('result') in ('verified','confirmed_error'):
            if not refs:errors.append(issue('proof_required',path+'.source_ids','Proof required for verified/error',category='evidence'))
            elif sources_valid and valid_refs and set(refs)<=source_ids:
                cited=[s for s in cards if s['source_id'] in refs]
                if not any(s['access_status'] in ('full_text','partial_text') and s['quote'].strip() and s['locator'] and cid in s['supports_claim_ids'] for s in cited):errors.append(issue('original_proof',path+'.source_ids','Search snippets/access failures cannot verify a claim; need located original-page excerpts',category='evidence'))
            else:skipped.append({'location':path+'.semantic_support','reason':'Valid registered source cards prerequisite missing'})
        if x.get('result') in ('confirmed_error','evidence_gap','scope_or_time_ambiguity','access_failed') and x.get('blocking') is False:errors.append(issue('hidden_blocker',path+'.blocking','Unresolved important claim must remain blocking',category='content'))
    if claims and seen!=set(claims):errors.append(issue('coverage','claim_checks','Review must explicitly cover every submitted claim, including unverified'))
    if any(isinstance(x,dict) and x.get('blocking') is True for x in checks) and not data.get('blocking'):errors.append(issue('hidden_blocker','blocking','Blocking claim cannot be hidden',category='content'))
    if protocol==MEMBER_CONTENT and enforce_semantics:
        from .handoff import semantic_gate
        if not errors:
            for finding in semantic_gate(data,candidate,contributors)['findings']:
                errors.append(issue('semantic_unclosed',finding['location'],finding['reason'],category=finding['category'],owner='orchestrator'))
        else:skipped.append({'location':'semantic_gate','reason':'Review structure prerequisite failed'})
    _finish(errors,own,skipped)
    return checks

RESEARCH_OUTPUT='''成功写入result.json和report.md后，界面仅回传任务/提交ID、路径、摘要、未解决问题和可得哈希，不重复完整JSON或正文。无法写入时才在界面完整返回。修订时可读取明确授权的上轮JSON，用程序仅更新受影响字段，原样保留其余字段。返回结构化JSON（可写入指定输出目录，正文放draft_markdown字段而非压成一句话）：task_id、submission_id、work_item_id、round_id、summary、draft_markdown、sources、claims、gaps、changes。sources每项字段：'''+', '.join(SOURCE_FIELDS)+'''. 缺日期或版本填null。accessed_at为带时区ISO时间；若只知日期，填null并加reported_accessed_at和access_time_note说明，不补造精确时间。access_status必须明确为full_text（实际读原页全文）、partial_text（实际读原页部分）、search_snippet（仅摘要）、access_failed或reported_access（范围不明）；不要写ok。检索摘要不能冒充原页。访问失败如实记录，成员摘录不是控制器已抓取原件，raw_artifact_id填null。claims每项字段：'''+', '.join(CLAIM_FIELDS)+'''. 来源ID只在本回复内命名；producer/collected_by使用任务包指定的实际成员标识（如kimi或workbuddy）。来源完整附件不受摘要字数限制。summary简短即可，正文直接完成指定目标。每个重要事实有原始链接、关键摘录、时间范围和正文位置。'''

REVIEW_OUTPUT='''在原复核JSON字段基础上，增加sources（按来源卡字段）、claim_checks与issue_resolutions。collected_by只填任务包指定的实际当前成员标识（如kimi或workbuddy），不加角色后缀。sources中的supports_claim_ids和limits必须是字符串数组，excerpt_is_complete和omissions_marked是布尔值。每个claim_checks含claim_id、给定的claim_sha256、result、blocking、reason、source_ids。result可为verified/confirmed_error/evidence_gap/scope_or_time_ambiguity/access_failed/wording_suggestion。只有直接证据反驳才是confirmed_error；材料不足属于evidence_gap，重要缺口仍阻塞。sources中使用本回复自己的source_id，不编造artifact ID。可以主动联网补证，仅核验非本人生产的事实。issue_resolutions可列本轮确已关闭的原问题ID和证据，不以他人裁决代替核验。'''
