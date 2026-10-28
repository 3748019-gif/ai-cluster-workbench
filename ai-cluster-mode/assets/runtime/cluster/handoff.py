"""Versioned member-content adaptation. Identity is bound by the controller.

This module never repairs an explicit identity/hash conflict, invents evidence,
or treats a prose comparison as automatically proved. Legacy protocols do not
use these bindings unless a task explicitly selects member_content_v1.
"""
import copy
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from fractions import Fraction

MEMBER_CONTENT = 'member_content_v1'
FRESH_REVIEW_SESSION = 'fresh_independent_review_v1'
ADAPTER_VERSION = 'member_content_v1.5'
ADAPTER_CODE_HASHES={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),Path(__file__).with_name('research.py'),Path(__file__).with_name('handoff_hardening.py'))}
SCOPE_FIELDS = ('school', 'unit', 'degree_route', 'audience', 'cohort',
                'effective_time', 'requirement_type', 'exceptions')


def check_fresh_review_session(proof, task_id, sid, prompt, expected_kind):
    """Check retained observations of an empty review conversation before send.

    This attests the visible session only, never unobservable provider memory.
    Callers opt in on newly frozen review submissions, not historical tickets.
    The active-conversation text must come from actual UI observations; a click
    receipt or controller-generated expected prompt is not session evidence.
    """
    evidence = proof.get('fresh_review_session')
    if not isinstance(evidence, dict) or evidence.get('policy') != FRESH_REVIEW_SESSION:
        raise ValueError('Independent review requires actual fresh-session observations')
    expected_source = 'simulated_test_fixture' if expected_kind == 'simulated' else 'computer_use'
    frames, timestamps = {}, []
    for name in ('before_input', 'after_input'):
        frame = evidence.get(name)
        if not isinstance(frame, dict):
            raise ValueError('Fresh-session evidence needs before_input and after_input frames')
        if (frame.get('kind') != expected_kind or frame.get('source') != expected_source
                or frame.get('observation_type') != 'ui_state'
                or frame.get('scope') != 'active_conversation'
                or frame.get('window') != proof.get('window')):
            raise ValueError('Fresh-session frame must be an actual observation of this active conversation')
        raw = frame.get('raw_text')
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError('Fresh-session frame needs actual active-conversation UI text')
        try:
            stamp = datetime.fromisoformat(frame['observed_at'].replace('Z', '+00:00'))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError('Fresh-session frame needs an observation timestamp') from exc
        if stamp.tzinfo is None or abs((datetime.now(timezone.utc)-stamp).total_seconds()) > 600:
            raise ValueError('Stale fresh-session observation; inspect the actual conversation again')
        if (frame.get('prior_messages_visible') is not False or frame.get('generation_active') is not False
                or type(frame.get('message_count')) is not int or frame['message_count'] != 0):
            raise ValueError('Independent review cannot start in an existing or generating conversation')
        # An exact current prompt may mention a parent work item. Only unrelated
        # markers outside that prompt are evidence of old conversation content.
        inspected = raw.replace(prompt, '') if name == 'after_input' else raw
        markers = re.findall(r'\bTASK-\d{8}-\d{4}(?:-SUB-\d{3})?\b', inspected)
        if any(marker not in (task_id, sid) for marker in markers):
            raise ValueError('Old task or submission visible in the active review conversation')
        if name == 'before_input':
            marker = frame.get('empty_state_marker')
            if (frame.get('composer_empty') is not True or frame.get('composer_value') != ''
                    or not isinstance(marker, str)
                    or not marker.strip() or marker not in raw):
                raise ValueError('Need visible empty-session marker and empty composer before input; clicking New Task is insufficient')
        elif (not isinstance(proof.get('raw_text'), str) or prompt not in raw
                or raw not in proof['raw_text']):
            raise ValueError('After-input frame must contain the exact draft and retained actual preflight UI text')
        frames[name] = frame
        timestamps.append(stamp)
    try:
        outer = datetime.fromisoformat(proof['observed_at'].replace('Z', '+00:00'))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError('Fresh-session evidence needs a valid preflight timestamp') from exc
    if outer.tzinfo is None or not timestamps[0] <= timestamps[1] <= outer:
        raise ValueError('Fresh-session observations must be ordered before input, after input, then preflight')
    return {'checked': True, 'policy': FRESH_REVIEW_SESSION,
            'before_observed_at': frames['before_input']['observed_at'],
            'after_observed_at': frames['after_input']['observed_at'],
            'visible_session_empty': True, 'hidden_provider_memory_verified': False}


def issue(code, location, message, *, owner='member', category='structure', allowed=None):
    item = {'code': code, 'location': location, 'message': message,
            'owner': owner, 'category': category}
    if allowed is not None:
        item['allowed_values'] = list(allowed)
    return item


class HandoffError(ValueError):
    """All independently discoverable problems from one retained response."""
    def __init__(self, errors, skipped=None):
        self.errors = errors
        self.skipped = skipped or []
        super().__init__('; '.join(f"{x['location']}: {x['message']}" for x in errors))


def build_envelope(manifest, submission, package, candidate=None):
    """Only confirmed/running submissions and their immutable package may bind."""
    if submission.get('status') not in ('confirmed', 'running', 'completed'):
        raise ValueError('Controller envelope needs a confirmed submission')
    if submission.get('package_id') != package.get('artifact_id'):
        raise ValueError('Controller envelope package identity mismatch')
    result = {key: manifest[key] for key in ('task_id', 'work_item_id', 'round_id')}
    result.update(submission_id=submission['submission_id'], provider=submission['provider'],
                  phase=submission['phase'], source_namespace=manifest['task_id']+'/'+submission['submission_id'])
    if submission['phase'] == 'reviewer':
        if not candidate or package.get('candidate_artifact_id') != candidate['artifact_id'] or package.get('candidate_sha256') != candidate['hash']:
            raise ValueError('Controller envelope refers to a stale candidate')
        result.update(candidate_artifact_id=candidate['artifact_id'], candidate_sha256=candidate['hash'])
    return result


def _bind(obj, key, expected, path, repairs, errors):
    if key not in obj:
        obj[key] = copy.deepcopy(expected)
        repairs.append({'field': path, 'action': 'bind omitted controller-owned field',
                        'before_present': False, 'after': copy.deepcopy(expected),
                        'reason': 'confirmed submission and exact frozen package'})
    elif type(obj[key]) is not type(expected) or obj[key] != expected:
        errors.append(issue('identity_conflict', path, 'Explicit identity/version conflicts with the confirmed submission',
                            category='identity', allowed=[expected]))


def bind_member_content(data, envelope, candidate=None):
    """Return adapted copy, repairs, conflicts. Never overwrite explicit values."""
    from .research import claim_hash, normalize_metadata
    if not isinstance(data, dict):
        return data, [], [issue('object_required', '$', 'Member content must be a JSON object')]
    result, repairs = normalize_metadata(data, envelope['provider'], boolean_strings=True)
    errors = []
    for key in ('task_id', 'submission_id', 'work_item_id', 'round_id', 'provider'):
        _bind(result, key, envelope[key], key, repairs, errors)
    # Optional nested display envelopes are not alternate authority. If present,
    # every reported identity must agree with the actual confirmed submission.
    for name in ('controller_envelope', 'envelope', 'review_target'):
        nested = result.get(name)
        if isinstance(nested, dict):
            for key, expected in envelope.items():
                if key in nested and (type(nested[key]) is not type(expected) or nested[key] != expected):
                    errors.append(issue('identity_conflict', name+'.'+key,
                                        'Explicit nested identity/version conflicts with the confirmed submission', category='identity'))
    if envelope['phase'] == 'reviewer':
        for key in ('candidate_artifact_id', 'candidate_sha256'):
            _bind(result, key, envelope[key], key, repairs, errors)
        claims = {x['claim_id']: x for x in (candidate or {}).get('claims', [])
                  if isinstance(x, dict) and isinstance(x.get('claim_id'), str)}
        checks = result.get('claim_checks', [])
        if isinstance(checks, list):
            for index, check in enumerate(checks):
                if isinstance(check, dict) and check.get('claim_id') in claims:
                    _bind(check, 'claim_sha256', claim_hash(claims[check['claim_id']]),
                          f'claim_checks[{index}].claim_sha256', repairs, errors)
        findings=result.get('semantic_findings',[])
        if isinstance(findings,list):
            for index,finding in enumerate(findings):
                if isinstance(finding,dict) and isinstance(finding.get('resolution'),dict):
                    _bind(finding['resolution'],'candidate_sha256',envelope['candidate_sha256'],
                          f'semantic_findings[{index}].resolution.candidate_sha256',repairs,errors)
    cards = result.get('sources', [])
    if isinstance(cards, list):
        for index, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            if 'id' in card and (type(card.get('source_id')) is not type(card['id']) or card.get('source_id') != card['id']):
                errors.append(issue('identity_conflict',f'sources[{index}].source_id',
                                    'Explicit source_id conflicts with the retained local id alias',category='identity'))
            _bind(card, 'collected_by', envelope['provider'], f'sources[{index}].collected_by', repairs, errors)
            _bind(card, 'source_namespace', envelope['source_namespace'], f'sources[{index}].source_namespace', repairs, errors)
            _bind(card, 'raw_artifact_id', None, f'sources[{index}].raw_artifact_id', repairs, errors)
    claims = result.get('claims', [])
    if envelope['phase'] == 'producer' and isinstance(claims, list):
        for index, claim in enumerate(claims):
            if isinstance(claim, dict):
                _bind(claim, 'producer', envelope['provider'], f'claims[{index}].producer', repairs, errors)
    return result, repairs, errors


def _review_categories(data, envelope, source_ids, errors):
    """The existing review_schema contract, collected without first-error exits."""
    for field in ('confirmed_errors', 'suspicions', 'unverified', 'wording_suggestions', 'passed_checks'):
        if not isinstance(data.get(field), list):
            errors.append(issue('array_required', field, field+' must be a list'))
    if type(data.get('blocking')) is not bool:
        errors.append(issue('boolean_required', 'blocking', 'blocking must be boolean'))
    seen = set()
    confirmed = data.get('confirmed_errors', [])
    if not isinstance(confirmed, list):
        return
    for index, finding in enumerate(confirmed):
        path = f'confirmed_errors[{index}]'
        if not isinstance(finding, dict):
            errors.append(issue('object_required', path, 'Confirmed error must be an object'))
            continue
        for field in ('id', 'location', 'description', 'source_artifacts', 'evidence', 'suggested_fix'):
            if not finding.get(field):
                errors.append(issue('required_field', path+'.'+field, 'Confirmed errors need location, original evidence and correction'))
        refs = finding.get('source_artifacts')
        if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
            errors.append(issue('string_array_required', path+'.source_artifacts', 'Review source_artifacts must be a string array'))
        elif not set(refs) <= set(source_ids):
            errors.append(issue('unknown_source', path+'.source_artifacts', 'Review cites unknown sources'))
        ident = finding.get('id')
        if isinstance(ident, str):
            if ident in seen:
                errors.append(issue('duplicate_issue', path+'.id', 'Duplicate review issue ID'))
            seen.add(ident)
    if confirmed and data.get('blocking') is False:
        errors.append(issue('hidden_blocker', 'blocking', 'Confirmed errors cannot be silently marked nonblocking', category='content'))


def _finding(ident, location, kind, reason, *, claim_ids=None, evidence=None):
    return {'id': ident, 'location': location, 'category': kind, 'description': reason,
            'reason': reason, 'blocking': True, 'claim_ids': claim_ids or [],
            'evidence': evidence or [], 'status': 'open', 'owner': 'orchestrator',
            'automatic_fact_verdict': False}


def _semantic_structure(data, errors):
    """Guard optional issue metadata before deep closure evaluation."""
    suggestions=data.get('wording_suggestions',[])
    if isinstance(suggestions,list):
        for index,entry in enumerate(suggestions):
            if not isinstance(entry,dict):continue  # unstructured text awaits semantic triage
            path=f'wording_suggestions[{index}]'
            for field in ('id','location','reason','suggestion'):
                if field in entry and not isinstance(entry[field],str):
                    errors.append(issue('string_required',path+'.'+field,'Wording issue metadata must be text'))
            if 'claim_ids' in entry and (not isinstance(entry['claim_ids'],list) or any(not isinstance(x,str) for x in entry['claim_ids'])):
                errors.append(issue('string_array_required',path+'.claim_ids','Wording issue claim IDs must be a string array'))
    entries=data.get('semantic_findings',[])
    if not isinstance(entries,list):
        errors.append(issue('array_required','semantic_findings','semantic_findings must be a list'));return
    seen=set()
    for index,entry in enumerate(entries):
        path=f'semantic_findings[{index}]'
        if not isinstance(entry,dict):
            errors.append(issue('object_required',path,'Semantic finding must be an object'));continue
        ident=entry.get('id')
        if not isinstance(ident,str) or not ident or ident in seen:
            errors.append(issue('issue_identity',path+'.id','Semantic finding needs a unique nonempty issue ID'))
        else:seen.add(ident)
        for field in ('location','reason','kind'):
            if field in entry and not isinstance(entry[field],str):
                errors.append(issue('string_required',path+'.'+field,'Semantic issue metadata must be text'))
        for field in ('claim_ids','source_ids','affected_outputs'):
            if field in entry and (not isinstance(entry[field],list) or any(not isinstance(x,str) or not x for x in entry[field])):
                errors.append(issue('string_array_required',path+'.'+field,'Issue references must be string arrays'))
        resolution=entry.get('resolution')
        if resolution is not None:
            if not isinstance(resolution,dict):
                errors.append(issue('object_required',path+'.resolution','Issue resolution must be an object'));continue
            for field in ('source_ids','checked_outputs'):
                if field in resolution and (not isinstance(resolution[field],list) or any(not isinstance(x,str) or not x for x in resolution[field])):
                    errors.append(issue('string_array_required',path+'.resolution.'+field,'Resolution references must be string arrays'))


def semantic_gate(data, candidate, contributors=None):
    """Close structured meaning/scope findings; unstructured prose awaits triage.

    Structured matching is a reviewer's assertion checked for consistency, not
    a machine proof of entailment. Unknowns never become the opposite answer.
    """
    findings = []
    problems=[];_semantic_structure(data,problems)
    if problems:
        return {'blocking':True,'findings':[_finding('semantic-structure-'+str(index),x['location'],'semantic_pending',x['message']) for index,x in enumerate(problems)],
                'checked_claim_ids':[],'automatic_semantic_proof':False,
                'skipped':[{'location':'semantic_closure','reason':'Structured issue metadata prerequisite failed'}]}
    claims = {x['claim_id']: x for x in candidate.get('claims', []) if isinstance(x, dict) and isinstance(x.get('claim_id'), str)}
    checks = {x['claim_id']: x for x in data.get('claim_checks', []) if isinstance(x, dict) and isinstance(x.get('claim_id'), str)}
    for cid, claim in claims.items():
        check = checks.get(cid, {})
        applicable = check.get('applicability')
        if not isinstance(applicable, dict):
            findings.append(_finding('applicability-'+cid, check.get('location', claim.get('location', '')),
                                     'scope_or_time_ambiguity', 'Important fact lacks a structured applicability judgment', claim_ids=[cid]))
            continue
        status = applicable.get('status')
        reason = applicable.get('reason')
        context = applicable.get('context_complete')
        comparisons = applicable.get('comparisons', [])
        if status not in ('matched', 'unresolved', 'not_applicable') or not isinstance(reason, str) or not reason.strip():
            findings.append(_finding('applicability-'+cid, claim.get('location', ''), 'semantic_pending',
                                     'Applicability status/rationale needs explicit adjudication', claim_ids=[cid]))
            continue
        if status == 'unresolved' or context is not True:
            findings.append(_finding('applicability-'+cid, claim.get('location', ''), 'scope_or_time_ambiguity',
                                     reason or 'Original-page context/qualifications not established', claim_ids=[cid]))
        if not isinstance(comparisons, list):
            findings.append(_finding('scope-comparisons-'+cid, claim.get('location', ''), 'semantic_pending',
                                     'Applicability comparisons are not structured', claim_ids=[cid]))
            continue
        for index, comparison in enumerate(comparisons):
            if not isinstance(comparison, dict):
                findings.append(_finding(f'scope-{cid}-{index}', claim.get('location', ''), 'semantic_pending',
                                         'Scope comparison needs adjudication', claim_ids=[cid]))
                continue
            field = comparison.get('field')
            if field not in SCOPE_FIELDS or comparison.get('result') not in ('matched', 'unknown', 'mismatch', 'not_applicable') or not comparison.get('reason'):
                findings.append(_finding(f'scope-{cid}-{index}', claim.get('location', ''), 'semantic_pending',
                                         'Scope comparison lacks a recognized field, result or reason', claim_ids=[cid]))
            elif comparison['result'] in ('unknown', 'mismatch'):
                findings.append(_finding(f'scope-{cid}-{index}', claim.get('location', ''), 'scope_or_time_ambiguity',
                                         comparison['reason'], claim_ids=[cid]))
            elif (comparison['result']=='matched' and 'claim_value' in comparison and 'source_value' in comparison
                  and comparison['claim_value']!=comparison['source_value']):
                findings.append(_finding(f'scope-{cid}-{index}',claim.get('location',''),'scope_or_time_ambiguity',
                                         'Reported matching scope values differ; equivalence/exception needs adjudication',claim_ids=[cid]))
        # If both sides provide typed applicability metadata, inconsistent
        # values are a gap requiring judgment, not an automatically false fact.
        declared = claim.get('applicability')
        if isinstance(declared, dict):
            cited = [s for s in data.get('sources', []) if isinstance(s, dict) and s.get('source_id') in check.get('source_ids', [])]
            for field in SCOPE_FIELDS:
                values = [s.get('applicability', {}).get(field) for s in cited if isinstance(s.get('applicability'), dict)]
                values = [v for v in values if v is not None]
                expected = declared.get(field)
                if expected is not None and (not values or all(v != expected for v in values)):
                    findings.append(_finding('typed-scope-'+cid+'-'+field, claim.get('location', ''),
                                             'scope_or_time_ambiguity', 'Typed claim/source '+field+' differ or source scope is unknown; evidence applicability needs adjudication', claim_ids=[cid]))
        ratio=claim.get('quantitative_assertion')
        if ratio is not None:
            valid=(isinstance(ratio,dict) and all(type(ratio.get(k)) is int for k in
                   ('numerator','denominator','threshold_numerator','threshold_denominator'))
                   and ratio['denominator']>0 and ratio['threshold_denominator']>0
                   and ratio.get('relation') in ('lt','le','eq','ge','gt'))
            if not valid:
                findings.append(_finding('ratio-'+cid,claim.get('location',''),'semantic_pending',
                                         'Structured proportion/denominator/relation needs adjudication',claim_ids=[cid]))
            else:
                actual=Fraction(ratio['numerator'],ratio['denominator'])
                threshold=Fraction(ratio['threshold_numerator'],ratio['threshold_denominator'])
                holds={'lt':actual<threshold,'le':actual<=threshold,'eq':actual==threshold,'ge':actual>=threshold,'gt':actual>threshold}[ratio['relation']]
                if not holds:findings.append(_finding('ratio-'+cid,claim.get('location',''),'semantic_contradiction',
                                                     'Structured proportion contradicts its stated relation/denominator; correct the affected assertion',claim_ids=[cid]))
    # A wording label is no exemption. Only an explicitly nonsemantic, located
    # suggestion with a reason can be treated as nonblocking without triage.
    for index, suggestion in enumerate(data.get('wording_suggestions', [])):
        path = f'wording_suggestions[{index}]'
        if not isinstance(suggestion, dict) or suggestion.get('meaning_changed') is not False or not suggestion.get('reason') or not suggestion.get('location') or suggestion.get('severity') not in ('nonblocking', 'editorial') or suggestion.get('blocking') is True:
            ident = suggestion.get('id', 'wording-'+str(index)) if isinstance(suggestion, dict) else 'wording-'+str(index)
            findings.append(_finding('triage-'+ident, suggestion.get('location', path) if isinstance(suggestion, dict) else path,
                                     'semantic_pending', 'Wording advice may change meaning or is insufficiently classified; adjudication required',
                                     claim_ids=suggestion.get('claim_ids', []) if isinstance(suggestion, dict) else []))
    closed_ids=set()
    for index, finding in enumerate(data.get('semantic_findings', [])):
        path = f'semantic_findings[{index}]'
        if not isinstance(finding, dict):
            findings.append(_finding('semantic-'+str(index), path, 'semantic_pending', 'Semantic finding is not structured'))
            continue
        ident = finding.get('id', 'semantic-'+str(index))
        severity = finding.get('severity')
        changed = finding.get('meaning_changed')
        resolution = finding.get('resolution')
        # A closed substantive issue requires version-bound independent proof;
        # merely writing status=resolved does not establish a closure.
        closed = (finding.get('status') == 'resolved' and isinstance(resolution, dict)
                  and resolution.get('candidate_sha256') == data.get('candidate_sha256')
                  and resolution.get('independently_verified') is True
                  and resolution.get('reason') and resolution.get('source_ids')
                  and set(resolution['source_ids']) <= {s['source_id'] for s in data.get('sources', []) if isinstance(s, dict)})
        if closed:
            cited=[s for s in data.get('sources',[]) if s['source_id'] in resolution['source_ids']]
            closed=any(s.get('access_status') in ('full_text','partial_text') and s.get('quote','').strip() and s.get('locator') for s in cited)
            required=finding.get('affected_outputs',[]);checked=resolution.get('checked_outputs',[])
            closed=bool(closed and isinstance(required,list) and isinstance(checked,list)
                        and all(isinstance(x,str) for x in required+checked) and set(required)<=set(checked))
            affected_claims=finding.get('claim_ids',[])
            if changed is True or severity=='blocking':closed=bool(closed and affected_claims)
            if affected_claims:
                closed=bool(closed and isinstance(affected_claims,list) and all(isinstance(x,str) and x in checks for x in affected_claims)
                            and all(checks[x].get('result')=='verified' and checks[x].get('blocking') is False
                                    and any(x in s.get('supports_claim_ids',[]) for s in cited) for x in affected_claims))
            if closed:closed_ids.add(ident)
        editorial = (severity in ('nonblocking', 'editorial') and changed is False and finding.get('reason') and finding.get('location'))
        if not closed and not editorial:
            findings.append(_finding(ident, finding.get('location', path), finding.get('kind', 'semantic_pending'),
                                     finding.get('reason', 'Semantic issue needs adjudication/independent version-bound closure'),
                                     claim_ids=finding.get('claim_ids', []), evidence=finding.get('source_ids', [])))
    # Preserve the original wording advice while closing its stable issue only
    # through the same exact-version independent evidence-backed finding.
    findings=[x for x in findings if not (x['id'].startswith('triage-') and x['id'][7:] in closed_ids)]
    return {'blocking': bool(findings), 'findings': findings, 'checked_claim_ids': list(claims),
            'automatic_semantic_proof': False}


def process_member_content(data, envelope, candidate=None, *, allowed_domains=None,
                           source_artifact_ids=(), contributors=None):
    """One local adaptation/check report; no external call or original mutation."""
    from .research import check_research, check_review, check_sources
    adapted, repairs, errors = bind_member_content(data, envelope, candidate)
    skipped = []
    if not isinstance(adapted, dict):
        skipped.append({'location': 'content', 'reason': 'JSON object prerequisite missing'})
        return {'data': adapted, 'repairs': repairs, 'errors': errors, 'skipped': skipped,
                'semantic_gate': {'blocking': True, 'findings': [], 'automatic_semantic_proof': False}}
    if envelope['phase'] == 'producer':
        check_research(adapted, envelope['provider'], allowed_domains, protocol=MEMBER_CONTENT, issues=errors, skipped=skipped)
        gate = {'blocking': False, 'findings': [], 'automatic_semantic_proof': False}
    else:
        _semantic_structure(adapted,errors)
        refs = list(source_artifact_ids)+[s['source_id'] for s in adapted.get('sources', [])
                                        if isinstance(s, dict) and isinstance(s.get('source_id'), str)] if isinstance(adapted.get('sources', []), list) else list(source_artifact_ids)
        _review_categories(adapted, envelope, refs, errors)
        check_review(adapted, candidate or {}, envelope['provider'], protocol=MEMBER_CONTENT,
                     contributors=contributors, enforce_semantics=False, issues=errors, skipped=skipped,
                     domains=allowed_domains)
        if errors:
            skipped.append({'location': 'semantic_gate', 'reason': 'Identity/source/review structure prerequisite failed; no semantic verdict inferred'})
            gate = {'blocking': True, 'findings': [], 'automatic_semantic_proof': False}
        else:
            gate = semantic_gate(adapted, candidate or {}, contributors)
    from .handoff_hardening import diagnose_root_causes
    diagnostics = diagnose_root_causes({'data': adapted, 'errors': errors})
    return {'data': adapted, 'repairs': repairs, 'errors': errors, 'skipped': skipped,
            'diagnostics': diagnostics, 'semantic_gate': gate, 'adapter_version': ADAPTER_VERSION,'adapter_code_hashes':ADAPTER_CODE_HASHES}
