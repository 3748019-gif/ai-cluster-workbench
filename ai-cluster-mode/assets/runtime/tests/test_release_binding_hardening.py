"""Release association fixtures use isolated test state, no clients/network."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from cluster.core import Cluster, GuardError, digest, js
from cluster.release_checks import build_manifest


class AcceptedReleaseBindingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.c = Cluster(self.root, 'test')
        self.spec = {'objective':'Synthetic accepted release association', 'task_type':'document',
            'rules':['Exact accepted bytes; unknown remains unknown'], 'acceptance':['READY'],
            'validators':[{'kind':'text','required':['READY']}], 'review_required':False}
        self.source = self.root/'source.txt'; self.source.write_text('Original synthetic authority')
        self.release = self.root/'release'; self.release.mkdir()

    def tearDown(self):
        self.c.close(); self.temp.cleanup()

    def prepared(self, output='READY unknown applicability remains unknown', files=None, sources=None):
        task = self.c.create(self.spec)['task_id']
        self.c.add_source(task, self.source); self.c.package(task); self.c.start_local(task)
        path = self.root/(task+'.txt')
        path.write_text(js({'task_id':task,'draft_markdown':output,'sources':sources}) if sources is not None else task+" "+output)
        candidate = self.c.local_output(task, path)
        frozen=[]
        for name, content in (files or {}).items():
            target=self.root/'exchange'/task/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_text(content)
        if files: frozen=self.c.freeze_candidate_files(task,[self.root/'exchange'/task/name for name in files])
        validation=self.c.validate(task)
        self.assertTrue(validation['passed'])
        return task,candidate,frozen,self.c.get(task)['validation'][-1]

    def register(self, content, filename='report.txt', options=None):
        (self.release/filename).write_text(content)
        (self.release/'MANIFEST_SHA256.json').write_text(js(build_manifest(self.release)))
        return self.c.register_release(self.task,self.release,options)

    def bindings(self, artifact=None, filename='report.txt', transform=None):
        artifact=artifact or self.c.artifact(self.c.get(self.task),self.c.get(self.task)['candidate'])
        return [{'acceptance_id':self.validation['artifact_id'], 'input_task_id':self.task,
                 'input_artifact_id':artifact['artifact_id'], 'input_sha256':artifact.get('hash',artifact.get('sha256')),
                 'output_path':filename, 'output_sha256':digest((self.release/filename).read_bytes()),
                 'transform':transform or {'type':'identity_v1','version':1}}]

    def setup_release(self):
        self.task,self.candidate,_,self.validation=self.prepared()
        self.register((self.root/(self.task+'.txt')).read_text())
        return self.c.get(self.task)['releases'][0]

    def zip(self):
        path=self.root/'release.zip'
        with zipfile.ZipFile(path,'w') as archive:
            for f in self.release.rglob('*'):
                if f.is_file():archive.write(f,'package/'+f.relative_to(self.release).as_posix())
        return path

    def test_unrelated_structural_B_cannot_deliver_accepted_A(self):
        self.task,_,_,self.validation=self.prepared()
        r=self.register('READY unrelated B')
        self.assertEqual(r['status'],'structurally_registered')
        self.c.finalize(self.task)
        with self.assertRaises(GuardError):self.c.deliver(self.task)
        with self.assertRaises(GuardError):self.c.bind_release(self.task,r['release_id'],self.bindings())
        self.assertEqual(self.c.get(self.task)['status'],'FINALIZING')

    def test_byte_exact_A_binding_delivers_and_retry_is_idempotent(self):
        r=self.setup_release(); bindings=self.bindings(); z=self.zip()
        first=self.c.bind_release(self.task,r['release_id'],bindings,z,'package')
        second=self.c.bind_release(self.task,r['release_id'],bindings,z,'package')
        self.assertEqual(first,second)
        self.assertEqual(len([x for x in self.c.events(self.task) if x['event']=='RELEASE_BOUND']),1)
        self.c.finalize(self.task); output=self.c.deliver(self.task)
        self.assertEqual(output.read_bytes(),(self.release/'report.txt').read_bytes())
        status=self.c.current_release_status(self.task,r['release_id'])
        self.assertEqual(status['task_status'],'DELIVERED')
        self.assertEqual(status['user_delivery'],'not_yet_confirmed')
        self.assertIn('unknown',status['factual_status_policy'])

    def test_frozen_candidate_file_whitelist_and_unchanged_unknown(self):
        self.task,_,files,self.validation=self.prepared(files={'full.md':'READY unknown remains unknown'})
        r=self.register('READY unknown remains unknown')
        self.c.bind_release(self.task,r['release_id'],self.bindings(files[0]))
        self.c.finalize(self.task); self.c.deliver(self.task)
        self.assertEqual(self.c.get(self.task)['status'],'DELIVERED')

    def test_arbitrary_registered_source_cannot_be_claimed_as_reviewed(self):
        r=self.setup_release(); source=self.c.artifact(self.c.get(self.task),self.c.get(self.task)['source_files'][0])
        (self.release/'report.txt').write_bytes(self.c.read_artifact(self.c.get(self.task),source['artifact_id']))
        (self.release/'MANIFEST_SHA256.json').write_text(js(build_manifest(self.release)))
        r=self.c.register_release(self.task,self.release)
        with self.assertRaises(GuardError):self.c.bind_release(self.task,r['release_id'],self.bindings(source))

    def test_fake_acceptance_or_input_hash_rejected(self):
        r=self.setup_release()
        for field,value in [('acceptance_id','unknown'),('input_sha256','0'*64)]:
            with self.subTest(field=field):
                b=self.bindings(); b[0][field]=value
                with self.assertRaises(GuardError):self.c.bind_release(self.task,r['release_id'],b)

    def test_changed_tree_even_updated_manifest_rejected_at_actual_deliver(self):
        r=self.setup_release();self.c.bind_release(self.task,r['release_id'],self.bindings());self.c.finalize(self.task)
        (self.release/'report.txt').write_text('READY changed facts')
        (self.release/'MANIFEST_SHA256.json').write_text(js(build_manifest(self.release)))
        with self.assertRaises(GuardError):self.c.deliver(self.task)

    def test_changed_zip_member_or_container_rejected_at_actual_deliver(self):
        for mode in ['member','container']:
            with self.subTest(mode=mode):
                r=self.setup_release();z=self.zip();self.c.bind_release(self.task,r['release_id'],self.bindings(),z,'package');self.c.finalize(self.task)
                if mode=='member':
                    with zipfile.ZipFile(z,'w') as archive:archive.writestr('package/report.txt','READY wrong')
                else:
                    with z.open('ab') as f:f.write(b'container changed')
                with self.assertRaises(GuardError):self.c.deliver(self.task)

    def test_zip_extra_duplicate_or_wrong_prefix_rejected(self):
        r=self.setup_release()
        for kind in ['extra','duplicate','prefix']:
            with self.subTest(kind=kind):
                z=self.zip()
                with zipfile.ZipFile(z,'a') as archive:
                    archive.writestr('package/'+('report.txt' if kind=='duplicate' else 'extra.txt'),'unexpected')
                with self.assertRaises(GuardError):self.c.bind_release(self.task,r['release_id'],self.bindings(),z,'wrong' if kind=='prefix' else 'package')

    def test_unbound_extra_file_and_duplicate_output_rejected(self):
        r=self.setup_release()
        b=self.bindings();
        with self.assertRaises(GuardError):self.c.bind_release(self.task,r['release_id'],b+b)
        (self.release/'extra.txt').write_text('READY');(self.release/'MANIFEST_SHA256.json').write_text(js(build_manifest(self.release)))
        r=self.c.register_release(self.task,self.release)
        with self.assertRaises(GuardError):self.c.bind_release(self.task,r['release_id'],b)

    def test_arbitrary_body_rewrite_never_mechanical(self):
        r=self.setup_release();b=self.bindings();b[0]['transform']={'type':'script_layout_or_body','version':1}
        with self.assertRaises(GuardError):self.c.bind_release(self.task,r['release_id'],b)

    def test_exact_registered_source_id_map_is_replayable(self):
        self.task,_,files,self.validation=self.prepared(files={'full.md':'READY [S001] unknown remains unknown','source.txt':'Synthetic original'},
            sources=[{'source_id':'S001','source_namespace':'exact-task','url':'https://example.org/original'}])
        (self.release/'report.txt').write_text('READY [REL-WB-001] unknown remains unknown')
        (self.release/'source.txt').write_text('Synthetic original')
        mapping=[{'release_source_id':'REL-WB-001','namespace':'exact-task','source_id':'S001','relative_paths':['source.txt'],'source_version':'2026','original_url':'https://example.org/original'}]
        (self.release/'MANIFEST_SHA256.json').write_text(js(build_manifest(self.release)))
        r=self.c.register_release(self.task,self.release,{'source_map':mapping})
        transform={'type':'utf8_reference_map_v1','version':1,'mapping':[{'from':'S001','to':'REL-WB-001','namespace':'exact-task'}]}
        b=self.bindings(files[0],transform=transform)+self.bindings(files[1],filename='source.txt')
        self.c.bind_release(self.task,r['release_id'],b);self.c.finalize(self.task);self.c.deliver(self.task)

    def test_map_cannot_delete_unknown_or_change_facts(self):
        r=self.setup_release();b=self.bindings();b[0]['transform']={'type':'utf8_reference_map_v1','version':1,'mapping':[{'from':'unknown','to':'verified','namespace':'pretend'}]}
        with self.assertRaises(GuardError):self.c.bind_release(self.task,r['release_id'],b)

    def test_zip_symlink_and_absolute_prefix_rejected(self):
        r=self.setup_release();z=self.zip()
        with self.assertRaises(GuardError):self.c.bind_release(self.task,r['release_id'],self.bindings(),z,'/package')
        with zipfile.ZipFile(z,'w') as archive:
            for f in self.release.iterdir():
                info=zipfile.ZipInfo('package/'+f.name)
                info.create_system=3;info.external_attr=(0o120777 if f.name=='report.txt' else 0o100644)<<16
                archive.writestr(info,f.read_bytes())
        with self.assertRaises(GuardError):self.c.bind_release(self.task,r['release_id'],self.bindings(),z,'package')

    def test_changed_parent_candidate_cannot_reuse_old_binding(self):
        r=self.setup_release();self.c.bind_release(self.task,r['release_id'],self.bindings());self.c.finalize(self.task)
        # Isolated state injection models a stale binding/candidate projection.
        with self.c._mutate(self.task) as m:
            other=self.c._artifact(m,(self.task+' READY changed scope').encode(),'producer','codex','new.txt',[])
            m['candidate']=other['artifact_id']
        with self.assertRaises(GuardError):self.c.deliver(self.task)

    def test_accepted_child_frozen_scope_requires_exact_acceptance_receipt(self):
        parent=self.c.create(self.spec)['task_id'];self.c.add_source(parent,self.source);self.c.package(parent)
        child_spec={**self.spec,'parent_task_id':parent}
        child=self.c.create(child_spec)['task_id'];self.c.add_source(child,self.source);self.c.package(child);self.c.start_local(child)
        out=self.root/'child.txt';out.write_text(child+' READY')
        self.c.local_output(child,out)
        f=self.root/'exchange'/child/'accepted.md';f.parent.mkdir(parents=True,exist_ok=True);f.write_text('READY child exact unknown')
        frozen=self.c.freeze_candidate_files(child,[f])[0]
        self.c.validate(child);self.c.finalize(child);self.c.deliver(child);self.c.accept_work(parent,child)
        self.c.start_local(parent);po=self.root/'parent.txt';po.write_text(parent+' READY aggregate');self.c.local_output(parent,po);self.c.validate(parent)
        self.task=parent;self.validation=self.c.get(parent)['validation'][-1]
        r=self.register('READY child exact unknown')
        b=self.bindings(frozen);b[0].update(input_task_id=child,acceptance_id=self.c.get(parent)['accepted_work'][0]['receipt_artifact'])
        wrong=[dict(b[0],acceptance_id='unknown-receipt')]
        with self.assertRaises(GuardError):self.c.bind_release(parent,r['release_id'],wrong)
        self.c.bind_release(parent,r['release_id'],b);self.c.finalize(parent);self.c.deliver(parent)


if __name__=='__main__':unittest.main()
