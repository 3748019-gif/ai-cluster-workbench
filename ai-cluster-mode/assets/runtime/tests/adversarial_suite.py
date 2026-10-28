"""Re-runnable actual-controller developer simulations; never a client test.

Run with --source-root pointing at the frozen before or integrated after tree.
All authority/state is Cluster(mode='test') under the supplied fresh output.
No network, GUI, external AI, private inputs or production state are accessed.
"""
import argparse
import copy
import csv
import hashlib
import io
import json
import os
import random
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORTABLE_INPUT = HERE / 'fixtures' / 'engineering_20261002'
INPUT = Path(os.environ.get('CLUSTER_FIXTURE_ROOT', str(PORTABLE_INPUT if PORTABLE_INPUT.is_dir() else HERE.parents[1] / 'input')))


def fixture_path(relative):
    """Support original task input and a standalone exported fixtures folder."""
    base=INPUT/'fixtures' if (INPUT/'fixtures').is_dir() else INPUT
    return base/relative


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stats(rows):
    required = [row for row in rows if row['required'] is True]
    numerator = sum(row['ai_module'] is True for row in required)
    denominator = len(required)
    comparison = '超过半数' if 2*numerator > denominator else ('达到半数' if 2*numerator == denominator else '不足半数')
    return dict(required_count=denominator,
                practice_ge_12_count=sum(row['practice_hours'] >= 12 for row in rows),
                ai_module_count=sum(row['ai_module'] is True for row in rows),
                average_credits=str((sum(Decimal(row['credits']) for row in rows)/Decimal(len(rows))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
                required_ai_numerator=numerator, required_ai_denominator=denominator,
                required_ai_fraction=f'{numerator}/{denominator}', required_ai_qualifier=comparison)


def csv_bytes(rows):
    stream=io.StringIO(); writer=csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def impact(before, after):
    """Fixture scoring only, NOT a production dependency engine.

    Group-dependent statements retain selectors over ALL rows, so entry into
    required=True is observed even if a row was absent from the old group.
    """
    watchers={
        'SIM-CL01': {'fields':['required'], 'selector':{'required':True}, 'selection_fields':['required']},
        'SIM-CL02': {'fields':['practice_hours'], 'selector':{'practice_hours':'>=12'}, 'selection_fields':['practice_hours']},
        'SIM-CL03': {'fields':['ai_module'], 'selector':{}, 'selection_fields':[]},
        'SIM-CL04': {'fields':['credits'], 'selector':{}, 'selection_fields':[]},
        'SIM-CL05': {'fields':['required','ai_module'], 'selector':{'required':True}, 'selection_fields':['required']},
    }
    old={r['course_id']:r for r in before}; new={r['course_id']:r for r in after}
    changed=[]
    for key in sorted(set(old)|set(new)):
        a=old.get(key,{}); b=new.get(key,{})
        for field in sorted(set(a)|set(b)):
            if a.get(field)!=b.get(field): changed.append({'row_id':key,'field':field,'before':a.get(field),'after':b.get(field)})
    affected=sorted(cid for cid,w in watchers.items() if any(c['field'] in set(w['fields']+w['selection_fields']) for c in changed))
    return {'algorithm':'fixture_field_selector_v1','production_capability_claim':False,'changes':changed,'watchers':watchers,'affected_claim_ids':affected,
            'old_group':[r['course_id'] for r in before if r['required']], 'new_group':[r['course_id'] for r in after if r['required']]}


def statements(s):
    text=[
        f"当前必修课{s['required_count']}门。", f"实践达到12学时的课程{s['practice_ge_12_count']}门。",
        f"有AI模块的课程{s['ai_module_count']}门。", f"六课平均学分{s['average_credits']}。",
        f"必修组AI覆盖{s['required_ai_fraction']}，{s['required_ai_qualifier']}。",
        '物理化学实验和合成实验各组选一；未完成SIM-P01须另修SIM-L02，已选不重复计入。',
        '2026入学化学学院BSc免修外语基础课；其他学院BA外语适用性未知。',
        '《化学数据练习》2025年已出版；具体本科课程指定采用未知。',
        '全校开放AI问答平台；化学本科课程实际使用与效果未知。',
        '课程页面2026年9月更新，首次开设年份未知。',
        '评论编辑时间为Spring 2018；作者修读学期未知。',
    ]
    return [{'claim_id':f'SIM-CL{i:02d}', 'text':value, 'producer':'codex',
             'location':f'brief/{i}', 'evidence_status':'accurate_unknown_disclosure' if i>=7 else 'fixture_supported',
             'underlying_fact_status':'scope_or_time_ambiguity' if i>=7 else 'fixture_supported',
             'source_ids':['SIM-S02'] if i<=5 else ['SIM-S01' if i<=7 else f'SIM-S{(3 if i==8 else 4 if i==9 else 5):02d}']}
            for i,value in enumerate(text,1)]


def native_proof(c, task, sub, root, stage, **extra):
    from cluster.core import now
    d={'kind':'simulated','task_id':task,'submission_id':sub['submission_id'],'provider':sub['provider'],
       'stage':stage,'observed_at':now(),'window':'isolated simulated member; no client',
       'raw_text':sub['prompt'],'input_text':sub['prompt'],'package_hash':sub['package_hash'],
       'unexpected_attachments':False,'client_available':True,'quota_available':True,'message_visible':True,
       'simulated_member':True}
    d.update(extra); p=root/(sub['submission_id']+'-'+stage+'.json');dump(p,d);return p


def fictitious_lifecycle(root, fixture, label, baseline=None, selected=None):
    from cluster.core import Cluster, digest, js
    from cluster.release_checks import build_manifest, check_release
    from cluster.document_review import document_statement_hash
    root.mkdir(parents=True,exist_ok=False);c=Cluster(root,'test')
    data=copy.deepcopy(fixture);rows=data['courses'];calculated=stats(rows)
    spec={'objective':'Virtual six-course brief; simulation, no university research','task_type':'document',
          'risk_level':'low','rules':['Use exact public fixtures, disclose unknowns; no network'],
          'acceptance':['Exact candidate independently simulated-reviewed; preserve unknowns'],
          'validators':[{'kind':'json','required_fields':['task_id','draft_markdown','statements','courses','statistics']}],
          'review_required':True,'result_protocol':'offline_document_v1',
          'policy_overrides':{'network_access':'offline'},'authorization_source':'User-authorized isolated developer simulation',
          'simulated_member':True}
    if selected:spec['document_review_scope']=selected
    task=c.create(spec)['task_id'];csv_path=root/'courses.csv';csv_path.write_bytes(csv_bytes(rows))
    policy_path=root/'policy.md';shutil.copy2(fixture_path('public/原文规则.md'),policy_path)
    courses_source=c.add_source(task,csv_path,'courses.csv');policy_source=c.add_source(task,policy_path,'policy.md')
    if baseline:
        delta_path=root/'delta-notice.json';shutil.copy2(fixture_path('stage2/变更通知.json'),delta_path)
        c.add_source(task,delta_path,'delta-notice.json')
    c.package(task);c.start_local(task)
    claim_rows=statements(calculated)
    brief='（完全虚构，模拟验收）'+''.join(x['text'] for x in claim_rows)
    assert len(brief)<500
    candidate={'task_id':task,'fixture_only':True,'simulated_member':True,'draft_markdown':brief,
               'courses':rows,'statistics':calculated,'statements':claim_rows,'sources':[],
               'unknown_scope_preserved':[x['claim_id'] for x in claim_rows if x['evidence_status']=='accurate_unknown_disclosure']}
    if baseline:
        candidate['unchanged_statement_inheritance']=[{'claim_id':x['claim_id'],'baseline_semantic_sha256':document_statement_hash(x),
            'current_semantic_sha256':document_statement_hash(next(y for y in claim_rows if y['claim_id']==x['claim_id'])),
            'baseline_review_sha256':baseline['review_sha256'], 'status':'unchanged_exact_semantics_only; no underlying unknown upgraded'}
            for x in baseline['statements'] if x['claim_id'] not in selected]
        assert all(x['baseline_semantic_sha256']==x['current_semantic_sha256'] for x in candidate['unchanged_statement_inheritance'])
    candidate_path=root/'candidate.json';dump(candidate_path,candidate);a=c.local_output(task,candidate_path)
    exchange=root/'exchange'/task;report=exchange/'report.md';report.write_text(brief+'\n',encoding='utf-8')
    coursefile=exchange/'courses.csv';coursefile.write_bytes(csv_bytes(rows));policyfile=exchange/'policy.md';policyfile.write_bytes(policy_path.read_bytes())
    unknown=exchange/'unknowns.json';dump(unknown,{'fixture_only':True,'unknowns':[x for x in claim_rows if x['evidence_status']=='accurate_unknown_disclosure']})
    mapping=exchange/'source_map.json';dump(mapping,{'records':[{'release_source_id':f'REL-SIM-{i:03d}', 'namespace':'fictional', 'source_id':f'SIM-S{i:02d}', 'relative_paths':['policy.md'],'file_sha256_by_path':{'policy.md':sha(policyfile.read_bytes())}} for i in range(1,6)]})
    frozen=c.freeze_candidate_files(task,[report,coursefile,policyfile,unknown,mapping]);package=c.package(task,'reviewer')
    sub=c.prepare_submission(task,'reviewer');lock=c.acquire_gui(task)
    c.begin_submission(task,sub['submission_id'],lock,native_proof(c,task,sub,root,'preflight'))
    c.confirm_submission(task,sub['submission_id'],lock,native_proof(c,task,sub,root,'confirmation'))
    # Independently compute reviewer statistics from frozen original CSV rather
    # than candidate's statistics or producer self-evaluation.
    reviewed_rows=[]
    for row in csv.DictReader(io.StringIO(csv_path.read_text())):
        row['required']=row['required']=='True';row['ai_module']=row['ai_module']=='True';row['practice_hours']=int(row['practice_hours']);reviewed_rows.append(row)
    independent=stats(reviewed_rows);assert independent==calculated
    expected_statements={x['claim_id']:x for x in statements(independent)}
    review_rows=[x for x in claim_rows if not selected or x['claim_id'] in selected]
    for x in review_rows:assert x['text']==expected_statements[x['claim_id']]['text']
    review={'task_id':task,'submission_id':sub['submission_id'],'candidate_artifact_id':a['artifact_id'],
            'candidate_sha256':a['hash'],'confirmed_errors':[],'suspicions':[],'unverified':[],
            'wording_suggestions':[],'passed_checks':[{'claim_id':x['claim_id'],'semantic_sha256':document_statement_hash(x),
            'basis':'Simulated reviewer independently recomputed frozen CSV; checked public rule including accurate unknown disclosure.',
            'source_artifacts':[courses_source['artifact_id'],policy_source['artifact_id']]} for x in review_rows],
            'blocking':False,'sources':[],'claim_checks':[],'simulated_member':True,
            'underlying_unknowns_remain_unknown':True,'real_external_call':False}
    review_path=root/'review.json';dump(review_path,review)
    reviewer=c.receive(task,sub['submission_id'],review_path,lock,native_proof(c,task,sub,root,'output',completed=True,output_hash=digest(review_path.read_bytes())))
    c.release_gui(task,lock);assert c.validate(task)['passed'];c.finalize(task)
    release=root/'release';release.mkdir()
    for item in frozen:shutil.copy2(item['frozen_path'],release/item['logical_filename'])
    (release/'MANIFEST_SHA256.json').write_text(js(build_manifest(release)))
    options={'source_map':json.loads(mapping.read_text())};registered=c.register_release(task,release,options)
    archive=root/'release.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(release.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(release).as_posix())
    binding=None
    if hasattr(c,'bind_release'):
        bindings=[{'acceptance_id':reviewer['artifact_id'],'input_task_id':task,'input_artifact_id':x['artifact_id'],
                   'input_sha256':x['sha256'],'output_path':x['logical_filename'],'output_sha256':sha((release/x['logical_filename']).read_bytes()),
                   'transform':{'type':'identity_v1','version':1}} for x in frozen]
        binding=c.bind_release(task,registered['release_id'],bindings,zip_path=archive)
    delivered=c.deliver(task);assert digest(delivered.read_bytes())==a['hash'];assert c.audit(task)['passed']
    extracted=root/'independent-extraction'
    with zipfile.ZipFile(archive) as z:z.extractall(extracted)
    physical_check=check_release(extracted,**options);assert physical_check['passed'],physical_check
    outcome={'task_id':task,'label':label,'run_mode':'test','real_kimi_calls':0,'real_workbuddy_calls':0,
             'controller_simulated_confirmed_submissions':c.metrics(task)['external_calls_confirmed'],
             'statistics':calculated,'candidate_sha256':a['hash'],'review_sha256':reviewer['hash'],
             'review_scope':[x['claim_id'] for x in review_rows], 'statements':claim_rows,'binding':binding,
             'registered_release':registered,'zip_sha256':sha(archive.read_bytes()),'extraction_passed':physical_check['passed'],
             'current_status':c.get(task)['status'],'unknown_scope_preserved':candidate['unknown_scope_preserved'],
             'first_valid_ingest':True,'format_continuations':0,
             'package_bytes':{'producer':len(c.read_artifact(c.get(task),c.get(task)['packages']['producer'])),
                              'reviewer':len(c.read_artifact(c.get(task),package['artifact_id']))},
             'captured_review_bytes':review_path.stat().st_size,
             'cost_and_provider_latency':None,
             'independent_vendor_verification':False,'strict_blind_test':False,'fixture_control_excluded_from_member_package':
             'expected_behavior.json' not in c.read_artifact(c.get(task),package['artifact_id']).decode()}
    dump(root/'outcome.json',outcome);c.close();return outcome


def recovery_sequences(root):
    from cluster.core import Cluster, GuardError, digest
    traces=[]
    for seed in (101,2026,371):
        local=root/f'seed-{seed}';local.mkdir(parents=True);c=Cluster(local,'test');rng=random.Random(seed)
        spec={'objective':'Fixed-seed interruption simulation','task_type':'document','risk_level':'low',
              'rules':['Keep exact intent, no duplicate send'], 'acceptance':['READY'], 'review_required':False,
              'validators':[{'kind':'text','required':['READY']}],
              'external_benefit':{'provider':'kimi','problem':'synthetic recovery','loss_without':'no boundary check','handoff_cost':'test-only',
              'capability':'second_analysis','client_available':True,'quota_available':True,'deterministic_alternative':False}}
        task=c.create(spec)['task_id'];source=local/'source.txt';source.write_text('READY synthetic canary');c.add_source(task,source);c.package(task)
        sub=c.prepare_submission(task,'producer');lock=c.acquire_gui(task);c.begin_submission(task,sub['submission_id'],lock,native_proof(c,task,sub,local,'preflight'))
        actions=['recover','decision','corrupt_projection_then_recover','prepare_must_reject','optional_cleanup_denied']
        rng.shuffle(actions);trace=['create','package','prepare','begin']
        for action in actions:
            if action=='recover':assert c.recover(task)['automatic_resend_allowed'] is False
            elif action=='decision':assert c.decision_packet(task)['next_action']=='inspect_existing_client_record_do_not_resend'
            elif action=='corrupt_projection_then_recover':
                (c.task_path(task)/'manifest.json').write_text('{broken');c.recover(task);assert json.loads((c.task_path(task)/'manifest.json').read_text())['status']=='SUBMISSION_UNCERTAIN'
            elif action=='prepare_must_reject':
                try:c.prepare_submission(task,'producer')
                except GuardError:pass
                else:raise AssertionError('Duplicate send permitted after intent')
            else:assert c.record_optional_action(task,'isolated optional cleanup denied','denied')['decision']=='skip_optional_action'
            assert c.get(task)['status']=='SUBMISSION_UNCERTAIN';assert c.metrics(task)['submission_intents']==1;trace.append(action)
        c.close();c=Cluster(local,'test');r=c.recover(task);assert r['status']=='SUBMISSION_UNCERTAIN'
        c.confirm_submission(task,sub['submission_id'],lock,native_proof(c,task,sub,local,'confirmation'))
        raw=local/'not-json.txt';raw.write_text('completed model result, broken serialization')
        captured=c.capture_output(task,sub['submission_id'],raw,lock,native_proof(c,task,sub,local,'output',completed=True,output_hash=digest(raw.read_bytes())))
        c.release_gui(task,lock)
        try:c.ingest_output(task,sub['submission_id'])
        except GuardError:pass
        else:raise AssertionError('Malformed raw accepted')
        m=c.get(task);assert m['provider_execution_status']=='completed';assert m['artifact_ingestion_status']=='parse_error'
        assert c.read_artifact(m,captured['artifact_id'])==raw.read_bytes();assert c.recover(task)['automatic_resend_allowed'] is False
        trace+=['close','reopen','recover','confirm','capture','release','ingest_reject','recover']
        traces.append({'seed':seed,'sequence':trace,'state':c.get(task)['status'],'native_real_calls':0,'intents':1,
                       'generated_and_captured_preserved':True,'raw_sha256':digest(raw.read_bytes()),'no_automatic_resend':True})
        dump(local/'trace.json',traces[-1]);c.close()
    dump(root/'sequence-results.json',traces);return traces


def root_negative_r03(root):
    from cluster.core import Cluster, GuardError, js, digest
    from cluster.release_checks import build_manifest
    root.mkdir(parents=True);c=Cluster(root,'test')
    spec={'objective':'Exact A versus unrelated B','task_type':'document','rules':['READY'],'acceptance':['exact A'],
          'review_required':False,'validators':[{'kind':'text','required':['READY']}]}
    task=c.create(spec)['task_id'];p=root/'source.txt';p.write_text('READY A');c.add_source(task,p);c.package(task);c.start_local(task)
    p=root/'candidate.txt';p.write_text(task+' READY candidate A');a=c.local_output(task,p);validation=c.validate(task);c.finalize(task)
    release=root/'unrelated_B';release.mkdir();b=release/'payload.txt';b.write_text('UNRELATED B')
    (release/'MANIFEST_SHA256.json').write_text(js(build_manifest(release)));registered=c.register_release(task,release)
    refusal=None
    try:final=c.deliver(task);final_hash=digest(final.read_bytes())
    except GuardError as exc:refusal=str(exc);final_hash=None
    output={'structure_registered':True,'unbound_delivery_rejected':bool(refusal),'rejection':refusal,
            'registered_status':registered['status'],'candidate_sha256':a['hash'],'final_sha256':final_hash,
            'final_was_exact_candidate_if_delivered':final_hash==a['hash'] if final_hash else None,
            'historical_wrong_delivery_asserted':False}
    dump(root/'observation.json',output);c.close();return output


def main():
    global INPUT
    parser=argparse.ArgumentParser();parser.add_argument('--source-root',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--fixture-root',default=str(INPUT))
    args=parser.parse_args();source=Path(args.source_root).resolve();output=Path(args.output).resolve()
    INPUT=Path(args.fixture_root).resolve()
    if output.exists():raise SystemExit('Output must be new: preserve every previous run/failure')
    output.mkdir(parents=True);sys.path.insert(0,str(source))
    began=time.perf_counter();result={'source_root':str(source),'run_mode':'test','real_external_calls':0,
                                    'native_cost':None,'tokens':None,'human_edits':None,'failures':[]}
    try:
        base=json.loads(fixture_path('public/courses_v1.json').read_text())
        baseline=fictitious_lifecycle(output/'fictional-baseline',base,'baseline')
        # Stage2 is read only after baseline candidate and exact review complete.
        notice=json.loads(fixture_path('stage2/变更通知.json').read_text());delta=copy.deepcopy(base)
        row=next(r for r in delta['courses'] if r['course_id']==notice['course_id'])
        for field,value in notice['changes'].items():assert row[field]==value['old'];row[field]=value['new']
        changed=impact(base['courses'],delta['courses']);dump(output/'selector-impact.json',changed)
        assert 'SIM-C04' not in changed['old_group'] and 'SIM-C04' in changed['new_group']
        assert changed['affected_claim_ids']==['SIM-CL01','SIM-CL02','SIM-CL04','SIM-CL05']
        current=fictitious_lifecycle(output/'fictional-delta',delta,'delta',baseline,changed['affected_claim_ids'])
        # Scoring answers are not read until simulated packages are frozen.
        expected=json.loads(fixture_path('control/expected_behavior.json').read_text())
        assert baseline['statistics']==expected['baseline'];assert current['statistics']==expected['delta']
        assert current['statistics']['required_ai_qualifier']=='达到半数'
        result.update(fictional={'baseline':baseline,'delta':current,'selector_impact':changed},
                      recovery=recovery_sequences(output/'recovery'), r03=root_negative_r03(output/'r03'))
    except Exception as exc:
        result['failures'].append({'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()})
        print(traceback.format_exc(),file=sys.stderr)
    result['local_wall_seconds']=time.perf_counter()-began;result['passed']=not result['failures']
    dump(output/'results.json',result);print(json.dumps({'passed':result['passed'],'output':str(output),'seconds':result['local_wall_seconds']},ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
