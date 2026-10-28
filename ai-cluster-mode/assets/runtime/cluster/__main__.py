import argparse
import json
import sys
from pathlib import Path
from .core import Cluster, GuardError, js
from .handoff_hardening import SessionBudget

def main():
    p=argparse.ArgumentParser(description='Evidence-driven desktop AI cluster; never clicks or calls provider APIs itself.')
    p.add_argument('--root',default=str(Path(__file__).resolve().parents[1]))
    p.add_argument('--mode',choices=['live','test'],default='live')
    sub=p.add_subparsers(dest='cmd',required=True)
    workflow_commands = {
        'canonical-inventory':('register_inventory',True), 'canonical-freeze':('freeze_canonical',True),
        'canonical-views':('generate_canonical_views',True), 'canonical-check':('inspect_canonical',False), 'canonical-candidate':('canonical_candidate',False),
        'review-inputs':('register_review_inputs',True), 'review-cycle':('review_cycle_status',False),
        'review-packet':('review_cycle_packet',False), 'review-batch':('review_batch_status',False),
        'review-freeze':('freeze_review_content',False), 'review-request':('request_review_adjudication',True), 'review-adjudicate':('adjudicate_review_issue',True),
        'review-authorize':('authorize_review_round',True), 'content-freeze':('freeze_content',False),
        'document-export':('request_document_export',True), 'document-register':('register_document',True),
        'document-qa':('record_native_document_qa',True), 'document-status':('document_stage_status',False)}
    for name, (_, needs_file) in workflow_commands.items():
        q=sub.add_parser(name);q.add_argument('task')
        if needs_file:q.add_argument('--file',required=True)
    q=sub.add_parser('assignment-plan');q.add_argument('--file',required=True)
    q=sub.add_parser('cancel-prepared');q.add_argument('task');q.add_argument('--submission',required=True);q.add_argument('--reason',required=True)
    q=sub.add_parser('isolated-review-prepare');q.add_argument('task')
    for name in ('isolated-review-begin','isolated-review-confirm','isolated-review-capture'):
        q=sub.add_parser(name);q.add_argument('task');q.add_argument('--submission',required=True);q.add_argument('--proof',required=True)
        if name=='isolated-review-capture':q.add_argument('--file',required=True)
    for name in ('assignment-status','assignment-handoff'):
        q=sub.add_parser(name);q.add_argument('task')
        if name=='assignment-handoff':q.add_argument('--phase',choices=['producer','reviewer'],default='producer')
    for name in ('material-contract','material-visual','material-restore','material-convert','material-render','material-impact','material-check'):
        q=sub.add_parser(name);q.add_argument('task')
        if name in ('material-contract','material-visual'):q.add_argument('--file',required=True)
        if name=='material-restore':q.add_argument('--key',required=True);q.add_argument('--output',required=True)
        if name=='material-convert':q.add_argument('--reading',required=True);q.add_argument('--output',required=True);q.add_argument('--renderer',required=True)
        if name=='material-render':q.add_argument('--artifact',required=True);q.add_argument('--directory',required=True);q.add_argument('--renderer',required=True);q.add_argument('--dpi',type=int,default=144)
        if name=='material-impact':q.add_argument('--previous',required=True)
    q=sub.add_parser('material-preflight');q.add_argument('task');q.add_argument('--phase',choices=['producer','reviewer'],default='producer')
    q=sub.add_parser('handoff-diagnostics');q.add_argument('task')
    q=sub.add_parser('handoff-contract');q.add_argument('kind',choices=['research','review','download','receipt'])
    for name in ('attachment-batch','attachment-result-batch','attachment-consume'):
        q=sub.add_parser(name);q.add_argument('task');q.add_argument('--file',required=True)
    for name in ('session-budget-init','session-budget-event','session-budget-show'):
        q=sub.add_parser(name);q.add_argument('session')
        if name!='session-budget-show':q.add_argument('--file',required=True)
    for name in ('attachment-request','attachment-result','attachment-publish','optional-action','release-register','release-bind'):
        q=sub.add_parser(name);q.add_argument('task')
        if name!='attachment-publish':q.add_argument('--file',required=True)
        if name=='attachment-result':q.add_argument('--request',required=True)
    q=sub.add_parser('original-register');q.add_argument('task');q.add_argument('--request',required=True);q.add_argument('--file',required=True);q.add_argument('--index',required=True);q.add_argument('--index-sha256',required=True)
    q=sub.add_parser('release-status');q.add_argument('task');q.add_argument('--release',required=True)
    q=sub.add_parser('freeze-files');q.add_argument('task');q.add_argument('--files',nargs='+',required=True)
    for name in ['create','list','source','configure-validators','package','local-start','local-output','assessment','gui-acquire','gui-release','prepare','begin','confirm','running','receive','not-sent','validate','resolve','correct','finalize','deliver','fail','pause','resume','degrade','recover','audit','metrics','show','decision','timer','cost','new-work','import','adopt','capture','ingest','effective-policy','rollup','reassign','stage','compact','accept-work','resolve-from-work','select-inputs','roles','set-role','configure-roles','register-member','verify-member','controller-model-presets','controller-model-config','set-controller-model','observe-controller-model']:
        q=sub.add_parser(name)
        if name not in ['create','list','roles','set-role','configure-roles','register-member','verify-member','controller-model-presets','controller-model-config','set-controller-model']:q.add_argument('task')
        if name in ['create','source','configure-validators','local-output','receive','cost','assessment','new-work','capture','configure-roles','register-member','observe-controller-model']:q.add_argument('--file',required=True)
        if name=='set-controller-model':q.add_argument('--preset',required=True);q.add_argument('--authority',required=True)
        if name in ('set-role','verify-member'):q.add_argument('--member',required=True)
        if name=='set-role':q.add_argument('--role',required=True,choices=['primary_executor','secondary_executor'])
        if name in ('set-role','configure-roles'):q.add_argument('--authority',required=True)
        if name=='verify-member':q.add_argument('--pilot-task',required=True)
        if name=='select-inputs':q.add_argument('--artifacts',nargs='+',required=True)
        if name=='new-work':q.add_argument('--previous')
        if name in ['import','adopt']:q.add_argument('--origin',required=True);q.add_argument('--artifact',required=True)
        if name in ['accept-work','resolve-from-work']:q.add_argument('--child',required=True)
        if name=='resolve-from-work':q.add_argument('--issue',required=True);q.add_argument('--reason',required=True)
        if name=='reassign':q.add_argument('--provider',required=True);q.add_argument('--reason',required=True)
        if name=='stage':q.add_argument('--name',required=True);q.add_argument('--action',required=True,choices=['start','stop']);q.add_argument('--note',default='')
        if name=='assessment':q.add_argument('--evidence',nargs='+',required=True)
        if name=='source':q.add_argument('--name');q.add_argument('--background',action='store_true');q.add_argument('--reference-only',action='store_true',help='Disclose the complete verified original by exact path and hash')
        if name in ['package','prepare']:q.add_argument('--phase',choices=['producer','reviewer'],default='producer')
        if name=='package':q.add_argument('--background',nargs='*',default=[])
        if name in ['begin','confirm','running','receive','not-sent','capture','ingest','compact']:q.add_argument('--submission',required=True)
        if name in ['gui-release','begin','confirm','receive','not-sent','capture']:q.add_argument('--token',required=True)
        if name in ['begin','confirm','receive','not-sent','capture']:q.add_argument('--proof',required=True)
        if name in ['resolve','correct','fail','pause','degrade']:q.add_argument('--reason',required=True)
        if name=='resolve':q.add_argument('--issue',required=True);q.add_argument('--evidence',nargs='+',required=True)
        if name=='fail':q.add_argument('--state',required=True)
        if name=='timer':q.add_argument('--name',required=True);q.add_argument('--seconds',type=float,required=True);q.add_argument('--description',required=True)
    a=p.parse_args();c=Cluster(a.root,a.mode)
    try:
        cmd=a.cmd; t=getattr(a,'task',None)
        if cmd in workflow_commands:
            method,needs_file=workflow_commands[cmd]
            if cmd in ('canonical-views','review-request'):result=getattr(c,method)(t,**json.loads(Path(a.file).read_text()))
            else:result=getattr(c,method)(t,json.loads(Path(a.file).read_text())) if needs_file else getattr(c,method)(t)
        elif cmd=='assignment-plan':result=c.assignment_plan(json.loads(Path(a.file).read_text()))
        elif cmd=='cancel-prepared':result=c.cancel_prepared_submission(t,a.submission,a.reason)
        elif cmd=='isolated-review-prepare':result=c.prepare_isolated_review(t)
        elif cmd.startswith('isolated-review-'):result=c.isolated_review_event(t,a.submission,a.proof,cmd.split('-')[-1],getattr(a,'file',None))
        elif cmd=='assignment-status':result=c.assignment_status(t)
        elif cmd=='assignment-handoff':result=c.assignment_handoff(t,a.phase)
        elif cmd=='material-contract':result=c.configure_material_contract(t,json.loads(Path(a.file).read_text()))
        elif cmd=='material-check':result=c.inspect_materials(t)
        elif cmd=='material-visual':result=c.record_material_visual(t,json.loads(Path(a.file).read_text()))
        elif cmd=='material-restore':result=c.restore_material(t,a.key,a.output)
        elif cmd=='material-convert':result=c.convert_material_pdf(t,a.reading,a.output,a.renderer)
        elif cmd=='material-render':result=c.render_material_pages(t,a.artifact,a.directory,a.renderer,a.dpi)
        elif cmd=='material-impact':result=c.material_impact(t,a.previous)
        elif cmd=='material-preflight':result=c.material_preflight(t,a.phase)
        elif cmd=='handoff-diagnostics':result=c.captured_output_diagnostics(t)
        elif cmd=='handoff-contract':result=c.handoff_contract(a.kind)
        elif cmd=='attachment-batch':result=c.request_attachment_batch(t,json.loads(Path(a.file).read_text()))
        elif cmd=='attachment-result-batch':result=c.attachment_result_batch(t,json.loads(Path(a.file).read_text()))
        elif cmd=='attachment-consume':result=c.acknowledge_attachment_consumption(t,**json.loads(Path(a.file).read_text()))
        elif cmd=='session-budget-init':result=SessionBudget(c.root).init(a.session,**json.loads(Path(a.file).read_text()))
        elif cmd=='session-budget-event':result=SessionBudget(c.root).apply(a.session,**json.loads(Path(a.file).read_text()))
        elif cmd=='session-budget-show':result=SessionBudget(c.root).read(a.session)
        elif cmd=='attachment-request':result=c.request_attachment(t,json.loads(Path(a.file).read_text()))
        elif cmd=='original-register':result=c.register_existing_original(t,a.request,a.file,a.index,a.index_sha256)
        elif cmd=='freeze-files':result=c.freeze_candidate_files(t,a.files)
        elif cmd=='attachment-result':result=c.attachment_result(t,a.request,json.loads(Path(a.file).read_text()))
        elif cmd=='attachment-publish':result=c.publish_attachment_receipt(t)
        elif cmd=='optional-action':result=c.record_optional_action(t,**json.loads(Path(a.file).read_text()))
        elif cmd=='release-register':
            r=json.loads(Path(a.file).read_text());result=c.register_release(t,r['root'],r.get('options'))
        elif cmd=='release-bind':
            r=json.loads(Path(a.file).read_text());result=c.bind_release(t,r['release_id'],r['bindings'],r.get('zip_path'),r.get('archive_prefix',''))
        elif cmd=='release-status':result=c.current_release_status(t,a.release)
        elif cmd=='roles':result=c.roles()
        elif cmd=='controller-model-presets':result=c.controller_model_presets()
        elif cmd=='controller-model-config':result=c.controller_model_config()
        elif cmd=='set-controller-model':result=c.configure_controller_model(a.preset,a.authority)
        elif cmd=='observe-controller-model':result=c.record_controller_model_observation(t,json.loads(Path(a.file).read_text()))
        elif cmd=='set-role':result=c.set_role(a.role,a.member,a.authority)
        elif cmd=='configure-roles':result=c.configure_roles(json.loads(Path(a.file).read_text()),a.authority)
        elif cmd=='register-member':result=c.register_member(json.loads(Path(a.file).read_text()))
        elif cmd=='verify-member':result=c.verify_member(a.member,a.pilot_task)
        elif cmd=='create':result=c.create(json.loads(Path(a.file).read_text()))
        elif cmd=='select-inputs':result=c.select_inputs(t,a.artifacts)
        elif cmd=='new-work':result=c.new_work(t,json.loads(Path(a.file).read_text()),a.previous)
        elif cmd=='import':result=c.import_artifact(t,a.origin,a.artifact)
        elif cmd=='adopt':result=c.adopt_candidate(t,a.origin,a.artifact)
        elif cmd=='capture':result=c.capture_output(t,a.submission,a.file,a.token,a.proof)
        elif cmd=='ingest':result=c.ingest_output(t,a.submission)
        elif cmd=='effective-policy':result=c.get(t).get('effective_policy')
        elif cmd=='rollup':result=c.rollup(t)
        elif cmd=='reassign':result=c.reassign(t,a.provider,a.reason)
        elif cmd=='stage':result=c.stage(t,a.name,a.action,a.note)
        elif cmd=='compact':result=c.compact_prepared_prompt(t,a.submission)
        elif cmd=='accept-work':result=c.accept_work(t,a.child)
        elif cmd=='resolve-from-work':result=c.resolve_from_work(t,a.issue,a.child,a.reason)
        elif cmd=='list':result=[{'task_id':m['task_id'],'status':m['status'],'objective':m['objective']} for m in c.list_tasks()]
        elif cmd=='source':result=c.add_source(t,a.file,a.name,a.background,reference_only=a.reference_only)
        elif cmd=='configure-validators':result=c.configure_validators(t,json.loads(Path(a.file).read_text()))
        elif cmd=='package':result=c.package(t,a.phase,a.background)
        elif cmd=='local-start':result=c.start_local(t)
        elif cmd=='local-output':result=c.local_output(t,a.file)
        elif cmd=='assessment':result=c.add_assessment(t,a.file,a.evidence)
        elif cmd=='gui-acquire':result={'token':c.acquire_gui(t)}
        elif cmd=='gui-release':result=c.release_gui(t,a.token)
        elif cmd=='prepare':result=c.prepare_submission(t,a.phase)
        elif cmd=='begin':result=c.begin_submission(t,a.submission,a.token,a.proof)
        elif cmd=='confirm':result=c.confirm_submission(t,a.submission,a.token,a.proof)
        elif cmd=='running':result=c.mark_running(t,a.submission)
        elif cmd=='receive':result=c.receive(t,a.submission,a.file,a.token,a.proof)
        elif cmd=='not-sent':result=c.reconcile_not_sent(t,a.submission,a.token,a.proof)
        elif cmd=='validate':result=c.validate(t)
        elif cmd=='resolve':result=c.resolve(t,a.issue,a.reason,a.evidence)
        elif cmd=='correct':result=c.begin_correction(t,a.reason)
        elif cmd=='finalize':result=c.finalize(t)
        elif cmd=='deliver':result={'path':str(c.deliver(t))}
        elif cmd=='fail':result=c.fail(t,a.state,a.reason)
        elif cmd=='pause':result=c.pause(t,a.reason)
        elif cmd=='resume':result=c.resume(t)
        elif cmd=='degrade':result=c.degrade(t,a.reason)
        elif cmd=='recover':result=c.recover(t)
        elif cmd=='audit':result=c.audit(t)
        elif cmd=='metrics':result=c.metrics(t)
        elif cmd=='decision':result=c.decision_packet(t)
        elif cmd=='show':result=c.get(t)
        elif cmd=='timer':result=c.record_timer(t,a.name,a.seconds,a.description)
        elif cmd=='cost':result=c.record_cost(t,json.loads(Path(a.file).read_text()))
        print(js(result if result is not None else {'ok':True}))
        return 0
    except (GuardError,ValueError,KeyError,OSError) as exc:
        print(js({'ok':False,'error':str(exc),'action':'Stop; inspect manifest and evidence before retry.'}),file=sys.stderr)
        return 2
    finally:c.close()

if __name__=='__main__':sys.exit(main())
