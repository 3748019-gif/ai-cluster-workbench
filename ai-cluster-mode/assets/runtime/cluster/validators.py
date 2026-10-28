"""Deterministic checks. Reference answers stay local and never enter a task package."""
import csv
import io
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal

def validate_roster(source, candidate, spec):
    rows = list(csv.DictReader(io.StringIO(source)))
    groups = defaultdict(list)
    problems = []
    def bad(code, location, message): problems.append({'code': code, 'location': location, 'message': message})
    for r in rows:
        if not r.get('id'): bad('source_id_missing', 'source', 'Source ID missing')
        groups[r.get('id', '')].append(r)
    expected = {}
    for ident, group in groups.items():
        statuses = {r['status'] for r in group}
        venues = {r['venue'].strip() for r in group}
        if len(statuses) != 1:
            category = 'pending'
        elif statuses == {spec.get('approved', 'approved')}:
            category = 'executable' if len(venues) == 1 and '' not in venues else 'pending'
        elif statuses == {spec.get('excluded', 'rejected')}:
            category = 'excluded'
        else:
            category = 'pending'
        expected[ident] = {'id': ident, 'name': group[0]['name'], 'category': category,
                           'venues': sorted(venues), 'sources': sorted(r['source'] for r in group)}
    actual = candidate.get('records', [])
    ids = [r.get('id') for r in actual]
    for ident, count in Counter(map(str, ids)).items():
        if count > 1: bad('duplicate_id', ident, f'ID appears {count} times')
    for ident in expected.keys() - set(ids): bad('omission', ident, 'Missing source ID')
    for idx, row in enumerate(actual):
        ident = row.get('id')
        if not isinstance(ident, str): bad('id_type', str(idx), 'IDs must retain string type and leading zeros')
        if ident not in expected:
            bad('unsupported_record', str(ident), 'ID absent from source'); continue
        for key in ['name', 'category', 'venues', 'sources']:
            if row.get(key) != expected[ident][key]: bad('mismatch', f'{ident}.{key}', f'Expected {expected[ident][key]!r}')
    counts = Counter(r['category'] for r in expected.values())
    expected_counts = {k: counts[k] for k in ['executable', 'pending', 'excluded']}
    expected_counts['total'] = len(expected)
    if candidate.get('counts') != expected_counts: bad('counts', 'counts', f'Expected {expected_counts}')
    if spec.get('deadline') and candidate.get('deadline') != spec['deadline']:
        bad('deadline', 'deadline', f"Expected {spec['deadline']}")
    notice = candidate.get('notice', '')
    if not isinstance(notice, str): bad('notice_type','notice','Notice must be text'); notice=''
    if len(notice) > spec.get('max_notice_chars', 100): bad('length', 'notice', f'{len(notice)} characters')
    for r in expected.values():
        if r['name'] in notice or r['id'] in notice: bad('privacy', 'notice', 'Contains name or ID')
    for text in spec.get('notice_required', []):
        if text not in notice: bad('notice_omission', 'notice', f'Missing {text}')
    return problems, {'source_rows':len(rows), 'unique_ids':len(expected), 'counts':expected_counts, 'notice_characters':len(notice)}

def validate_text(text, spec):
    errors=[]
    def bad(code,loc,msg): errors.append({'code':code,'location':loc,'message':msg})
    if 'max_characters' in spec and len(text) > spec['max_characters']: bad('length','text',str(len(text)))
    for token in spec.get('required', []):
        if token not in text: bad('required','text',f'Missing {token}')
    for token in spec.get('forbidden', []):
        if token in text: bad('forbidden','text',f'Unexpected {token}')
    if 'heading_count' in spec:
        count=len(re.findall(r'^#{1,6}\s',text,re.M))
        if count != spec['heading_count']: bad('headings','text',f'{count} headings')
    for date in spec.get('dates', []):
        try: datetime.strptime(date,'%Y-%m-%d')
        except ValueError: bad('date_format','spec',f'Invalid date {date}')
        if date not in text: bad('date_missing','text',date)
    return errors, {'characters':len(text)}

def validate_table(source, candidate, spec):
    rows=list(csv.DictReader(io.StringIO(candidate)))
    errors=[]
    def bad(code,loc,msg): errors.append({'code':code,'location':loc,'message':msg})
    columns=list(rows[0]) if rows else next(csv.reader(io.StringIO(candidate)),[])
    if spec.get('columns') and columns != spec['columns']: bad('columns','header',str(columns))
    if 'rows' in spec and len(rows)!=spec['rows']: bad('rows','table',str(len(rows)))
    key=spec.get('key')
    if key:
        vals=[r.get(key,'') for r in rows]
        if '' in vals or len(vals)!=len(set(vals)): bad('unique_key',key,'Missing or duplicate key')
        if source:
            originals=list(csv.DictReader(io.StringIO(source)))
            lookup={r[key]:r for r in originals}
            for idx,r in enumerate(rows):
                if r.get(key) not in lookup: bad('extra',str(idx),'Unknown key'); continue
                for col in spec.get('match_columns',[]):
                    if r.get(col)!=lookup[r[key]].get(col): bad('row_mismatch',f'{idx}.{col}','Does not match key in source')
            if spec.get('same_keys',True) and set(vals)!=set(lookup): bad('missing_keys','table','Key sets differ')
    for col,total in spec.get('sums',{}).items():
        try:
            actual=sum((Decimal(r[col]) for r in rows), Decimal(0))
            if abs(actual-Decimal(str(total)))>Decimal(str(spec.get('tolerance','0'))): bad('sum',col,str(actual))
        except (ValueError,KeyError,ArithmeticError): bad('number',col,'Non-numeric or missing value')
    return errors, {'rows':len(rows),'columns':columns}

def run_checks(candidate_bytes, source_bytes, specs):
    if not specs: raise ValueError('At least one substantive validator is required')
    text=candidate_bytes.decode('utf-8')
    reports=[]
    for spec in specs:
        kind=spec['kind']
        try:
            if kind=='roster':
                errors,details=validate_roster(source_bytes[spec['source_artifact']].decode('utf-8'),json.loads(text),spec)
            elif kind=='text':
                if not any(k in spec for k in ['required','forbidden','max_characters','heading_count','dates']): raise ValueError('Empty text check')
                errors,details=validate_text(text,spec)
            elif kind=='table':
                if not any(k in spec for k in ['columns','rows','key','sums']): raise ValueError('Empty table check')
                src=source_bytes.get(spec.get('source_artifact'),b'').decode('utf-8')
                errors,details=validate_table(src,text,spec)
            elif kind=='json':
                data=json.loads(text); errors=[]; details={}
                if not spec.get('required_fields'): raise ValueError('Empty JSON check')
                for field in spec['required_fields']:
                    if field not in data: errors.append({'code':'field','location':field,'message':'Missing field'})
                for key,value in spec.get('equals',{}).items():
                    if data.get(key)!=value: errors.append({'code':'value','location':key,'message':'Value mismatch'})
            elif kind=='research':
                from .research import check_research
                details=check_research(json.loads(text));errors=[]
            else: raise ValueError(f'Unregistered validator: {kind}')
        except (KeyError,ValueError,TypeError) as exc:
            errors=[{'code':'invalid_input','location':kind,'message':str(exc)}]; details={}
        reports.append({'kind':kind,'passed':not errors,'errors':errors,'details':details})
    return {'passed':all(r['passed'] for r in reports),'checks':reports,'error_count':sum(len(r['errors']) for r in reports)}

def review_schema(data, task_id, candidate, source_ids):
    required=['task_id','submission_id','candidate_artifact_id','candidate_sha256','confirmed_errors','suspicions','unverified','wording_suggestions','passed_checks','blocking']
    if any(k not in data for k in required): raise ValueError('Reviewer JSON is missing required categories')
    if data['task_id']!=task_id or data['candidate_artifact_id']!=candidate['artifact_id'] or data['candidate_sha256']!=candidate['hash']:
        raise ValueError('Reviewer reviewed a different task or candidate version')
    for k in ['confirmed_errors','suspicions','unverified','wording_suggestions','passed_checks']:
        if not isinstance(data[k],list): raise ValueError(f'{k} must be a list')
    if not isinstance(data['blocking'],bool): raise ValueError('blocking must be boolean')
    seen=set()
    for error in data['confirmed_errors']:
        if not isinstance(error,dict) or not all(error.get(k) for k in ['id','location','description','source_artifacts','evidence','suggested_fix']):
            raise ValueError('Confirmed errors need location, original evidence and correction')
        if not isinstance(error['source_artifacts'],list) or not set(error['source_artifacts']) <= set(source_ids):
            raise ValueError('Review cites unknown sources')
        if error['id'] in seen: raise ValueError('Duplicate review issue ID')
        seen.add(error['id'])
    if data['confirmed_errors'] and not data['blocking']:
        raise ValueError('Confirmed errors cannot be silently marked nonblocking')
    return data
