"""Small guard-removal mutation campaign, disposable isolated source copies.

Valid semantic mutations only enter the kill-rate denominator. Syntax-invalid
and explicitly equivalent comment-only variants are reported separately.
No production source or authoritative task state is ever mutated.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent


def main():
    p=argparse.ArgumentParser();p.add_argument('--source-root',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();source=Path(a.source_root).resolve();output=Path(a.output).resolve()
    if output.exists():raise SystemExit('New output required: mutation evidence must be preserved')
    output.mkdir(parents=True)
    command=[sys.executable,'-m','unittest','test_adversarial_hardening','-v']
    def test_env(tree):
        return dict(os.environ,CLUSTER_TEST_SOURCE=str(tree),PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(HERE))
    baseline=subprocess.run(command,cwd=source,env=test_env(source),capture_output=True,text=True)
    (output/'unmutated-baseline.stdout.log').write_text(baseline.stdout)
    (output/'unmutated-baseline.stderr.log').write_text(baseline.stderr)
    if baseline.returncode!=0:
        (output/'results.json').write_text(json.dumps({'valid_mutations':0,'killed_valid_mutations':0,
            'passed':False,'reason':'Unmutated exact baseline must pass before any kill count','baseline_exit_code':baseline.returncode},indent=2)+'\n')
        raise SystemExit('Unmutated exact baseline failed; mutation campaign not run')
    mutations=[
        ('M01-stale-statement','cluster/document_review.py',
         "if check.get('semantic_sha256')!=document_statement_hash(statements[cid]):",'if False:', 'valid'),
        ('M02-missing-review-coverage','cluster/document_review.py','if covered!=expected:','if False:','valid'),
        ('M03-self-review','cluster/document_review.py',
         "if any(statements[cid].get('producer')==provider for cid in expected):",'if False:','valid'),
        ('M04-unknown-blocking','cluster/document_review.py',
         "if failed and data.get('blocking') is not True:",'if False:','valid'),
        ('M05-automatic-resend','cluster/core.py',"'automatic_resend_allowed':False","'automatic_resend_allowed':True",'valid'),
        ('M06-final-content-binding','cluster/core.py',
         "if final['hash']!=candidate['hash'] or candidate['artifact_id'] not in final['input_artifacts']:",'if False:','valid'),
        ('M09-numeric-semantic-guard','cluster/handoff.py','if not holds:findings.append(', 'if False:findings.append(', 'valid'),
        ('M10-release-association-delivery','cluster/core.py','self._release_delivery_gate(m)', 'None  # isolated removed association delivery guard', 'valid'),
        ('M07-equivalent-comment','cluster/document_review.py','PROTOCOL = ', '# Equivalent commentary only\nPROTOCOL = ','equivalent'),
        ('M08-invalid-syntax','cluster/document_review.py','PROTOCOL = ', 'PROTOCOL !!! ', 'invalid'),
    ]
    records=[]
    for identifier,relative,needle,replacement,classification in mutations:
        mutant=output/identifier;mutant.mkdir();tree=mutant/'source';tree.mkdir()
        for name in ('cluster','templates','tests'):
            if (source/name).is_dir():shutil.copytree(source/name,tree/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        target=tree/relative;text=target.read_text();matches=text.count(needle)
        rec={'mutation_id':identifier,'file':relative,'declared_classification':classification,
             'source_sha256':hashlib.sha256((source/relative).read_bytes()).hexdigest(),'anchor_count':matches}
        if matches!=1:
            rec.update(classification='invalid',reason='No unique mutation anchor; not counted');records.append(rec);continue
        target.write_text(text.replace(needle,replacement,1));rec['mutant_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
        compile_ok=True
        try:compile(target.read_text(),str(target),'exec')
        except SyntaxError as exc:
            compile_ok=False;rec.update(classification='invalid',reason=str(exc))
        rec['compiles']=compile_ok
        if not compile_ok:
            records.append(rec);continue
        env=test_env(tree)
        run=subprocess.run(command,cwd=tree,env=env,capture_output=True,text=True)
        (mutant/'stdout.log').write_text(run.stdout);(mutant/'stderr.log').write_text(run.stderr)
        rec.update(classification=classification,command=command,exit_code=run.returncode,
                   killed=(run.returncode!=0) if classification=='valid' else None,
                   equivalent_remains_passing=(run.returncode==0) if classification=='equivalent' else None)
        records.append(rec)
    valid=[x for x in records if x['classification']=='valid'];killed=sum(x['killed'] is True for x in valid)
    result={'source_root':str(source),'campaign':'guard_removal_only; not comprehensive mutation adequacy',
            'unmutated_baseline_passed':True,
            'valid_mutations':len(valid),'killed_valid_mutations':killed,'survived_valid_mutations':len(valid)-killed,
            'equivalent_mutations':sum(x['classification']=='equivalent' for x in records),
            'invalid_mutations':sum(x['classification']=='invalid' for x in records),
            'kill_fraction':f'{killed}/{len(valid)}','records':records,
            'real_external_calls':0,'production_source_mutated':False}
    (output/'results.json').write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False))
    return 0 if all(x.get('equivalent_remains_passing') is not False for x in records) else 1


if __name__=='__main__':raise SystemExit(main())
