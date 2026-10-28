"""Code/template rollback rehearsal against copied test state only.

It never restores production SQLite or downgrades a real task. Closed after
simulation evidence is copied, then old controller is asked to recover/audit.
Unknown new receipt metadata is not reinterpreted as a new acceptance.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def inventory(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and p.name!='manifest.json' and p.name!='events.jsonl'
            and not p.name.endswith(('.sqlite3','.sqlite3-wal','.sqlite3-shm'))}


def main():
    p=argparse.ArgumentParser();p.add_argument('--before-source',required=True);p.add_argument('--after-source',required=True)
    p.add_argument('--after-state',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    old=Path(a.before_source).resolve();new=Path(a.after_source).resolve();state=Path(a.after_state).resolve();out=Path(a.output).resolve()
    if out.exists():raise SystemExit('Use new rehearsal output; never overwrite')
    out.mkdir(parents=True);code=out/'code';code.mkdir();copy_state=out/'test-state'
    shutil.copytree(state,copy_state,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*-wal','*-shm'))
    after_code={}
    for folder in ('cluster','templates'):
        shutil.copytree(new/folder,code/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        after_code.update({folder+'/'+k:v for k,v in inventory(code/folder).items()})
        shutil.rmtree(code/folder)
        shutil.copytree(old/folder,code/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    restored={folder+'/'+k:v for folder in ('cluster','templates') for k,v in inventory(code/folder).items()}
    old_expected={folder+'/'+k:v for folder in ('cluster','templates') for k,v in inventory(old/folder).items()}
    assert restored==old_expected
    evidence_before=inventory(copy_state/'tasks')
    task=json.loads((state/'outcome.json').read_text())['task_id']
    command=[sys.executable,'-m','cluster','--root',str(copy_state),'--mode','test','recover',task]
    run=subprocess.run(command,cwd=code,capture_output=True,text=True)
    (out/'stdout.log').write_text(run.stdout);(out/'stderr.log').write_text(run.stderr)
    recovered=json.loads(run.stdout) if run.returncode==0 else None
    evidence_after=inventory(copy_state/'tasks');unchanged=evidence_before==evidence_after
    report={'scope':'isolated code/template + copied closed test task; no production data migration',
            'before_source':str(old),'after_source':str(new),'closed_test_task_id':task,
            'code_and_templates_restored_exactly':restored==old_expected,
            'new_task_raw_artifacts_preserved':unchanged,'old_controller_recovery_exit':run.returncode,
            'old_controller_recovery':recovered,'command':command,'restored_hashes':restored,
            'after_hashes':after_code,'evidence_before':evidence_before,'evidence_after':evidence_after,
            'compatibility_limits':'Read/recover/audit of closed test task only; no downgrade of live pending binding/budget tasks, no old code re-publishing new tasks.'}
    passed=restored==old_expected and unchanged and run.returncode==0 and recovered['audit']['passed']
    report['passed']=passed;(out/'results.json').write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'passed':passed,'output':str(out)},ensure_ascii=False));return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
