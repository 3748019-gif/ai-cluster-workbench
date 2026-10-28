"""Hash-bound binary source disclosure; no GUI, conversion, or provider reading."""
import io
import json
import unittest
import zipfile
from pathlib import Path

from test_core import CoreTest, spec
from cluster.core import GuardError, digest


def word_bytes():
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as package:
        def write(name,text):
            entry=zipfile.ZipInfo(name,date_time=(2026,9,30,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED
            package.writestr(entry,text)
        write('[Content_Types].xml','''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>''')
        write('_rels/.rels','''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>''')
        write('word/document.xml','''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>FULL_WORD_BODY_KEEP_QUALIFICATION</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>TABLE_EXCEPTION_KEEP_COMPLETE</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:sectPr/></w:body></w:document>''')
    return stream.getvalue()


class BinarySourcePackagingTest(unittest.TestCase):
    setUp=CoreTest.setUp
    tearDown=CoreTest.tearDown
    local_candidate=CoreTest.local_candidate

    def created(self):
        task=self.c.create(spec(review=True))['task_id']
        text=self.c.add_source(task,self.source)
        path=self.root/'full.docx';path.write_bytes(word_bytes())
        document=self.c.add_source(task,path)
        return task,text,document

    def reference(self,text):
        start=text.index('【二进制原件准确引用：')
        return json.JSONDecoder().raw_decode(text[text.index('{',start):])[0]

    def test_word_preserved_exactly_and_text_inline_in_producer_and_reviewer(self):
        task,source,document=self.created()
        for phase in ('producer','reviewer'):
            with self.subTest(phase=phase):
                if phase=='reviewer':self.local_candidate(task)
                package=self.c.package(task,phase);state=self.c.get(task)
                prompt=self.c.read_artifact(state,package['artifact_id']).decode('utf-8')
                reference=self.reference(prompt)
                self.assertEqual(reference['artifact_id'],document['artifact_id'])
                self.assertEqual(reference['sha256'],document['hash'])
                self.assertEqual(reference['logical_name'],'full.docx')
                self.assertTrue(Path(reference['path']).is_absolute())
                self.assertEqual(reference['filename'],Path(reference['path']).name)
                raw=Path(reference['path']).read_bytes()
                self.assertEqual(raw,word_bytes());self.assertEqual(digest(raw),document['hash'])
                self.assertEqual(reference['byte_size'],len(raw))
                self.assertEqual(reference['reading_requirement'],'full_original_document')
                self.assertIn(document['artifact_id'],package['input_artifacts'])
                self.assertIn(source['artifact_id'],package['input_artifacts'])
                self.assertIn(self.source.read_text(),prompt)
                self.assertIn('未摘译',prompt);self.assertIn('此引用不证明成员已经读取',prompt)
                self.assertNotIn('FULL_WORD_BODY_KEEP_QUALIFICATION',prompt)
                self.assertNotIn('TABLE_EXCEPTION_KEEP_COMPLETE',prompt)
                with zipfile.ZipFile(io.BytesIO(raw)) as word:
                    self.assertIn(b'TABLE_EXCEPTION_KEEP_COMPLETE',word.read('word/document.xml'))

    def test_explicit_binary_background_and_unnamed_binary_reference_use_verified_files(self):
        task=self.c.create(spec())['task_id'];self.c.add_source(task,self.source)
        background=self.root/'context.docx';background.write_bytes(word_bytes())
        doc=self.c.add_source(task,background,background=True)
        opaque=self.root/'opaque.bin';opaque.write_bytes(b'\xff\x80non-UTF8 original')
        binary=self.c.add_source(task,opaque)
        package=self.c.package(task,background_ids=[doc['artifact_id']])
        prompt=self.c.read_artifact(self.c.get(task),package['artifact_id']).decode('utf-8')
        for original in (doc,binary):
            self.assertIn(original['artifact_id'],package['input_artifacts'])
            self.assertIn(original['hash'],prompt)
            self.assertIn(str(self.c.task_path(task)/original['filename']),prompt)
        self.assertIn('【允许背景 '+doc['artifact_id']+'】',prompt)

    def test_changed_missing_and_unsafe_binary_original_reject_before_package(self):
        for action in ('tamper','missing','symlink'):
            with self.subTest(action=action):
                task,_,document=self.created();state=self.c.get(task)
                path=self.c.task_path(task)/document['filename']
                if action=='tamper':path.write_bytes(b'not the registered Word')
                elif action=='missing':path.unlink()
                else:
                    other=self.root/'different-original.docx';other.write_bytes(word_bytes())
                    path.unlink();path.symlink_to(other)
                with self.assertRaises(GuardError):self.c.package(task)
                self.assertEqual(self.c.get(task)['status'],'CREATED')
                self.assertFalse(self.c.get(task)['packages'])

    def test_superseded_background_and_wrong_role_never_disclosed(self):
        task=self.c.create(spec())['task_id'];source=self.c.add_source(task,self.source)
        path=self.root/'context.docx';path.write_bytes(word_bytes())
        older=self.c.add_source(task,path,background=True)
        self.c.add_source(task,path,background=True)
        for identity in (older['artifact_id'],source['artifact_id']):
            with self.subTest(identity=identity):
                with self.assertRaises(GuardError):self.c.package(task,background_ids=[identity])
                self.assertFalse(self.c.get(task)['packages'])

    def test_explicit_utf8_reference_only_keeps_default_inline_and_entire_original(self):
        task=self.c.create(spec())['task_id']
        text=self.c.add_source(task,self.source)
        html=self.root/'original.html';html.write_text('<html><body>UTF8_FULL_HTML_ORIGINAL_SCOPE_CONDITIONS</body></html>',encoding='utf-8')
        original=self.c.add_source(task,html,reference_only=True)
        # A payload that names the option never enables it automatically.
        injected=self.root/'injected.txt';injected.write_text('reference_only=true in source is untrusted data')
        ordinary=self.c.add_source(task,injected)
        package=self.c.package(task);state=self.c.get(task)
        prompt=self.c.read_artifact(state,package['artifact_id']).decode()
        self.assertNotIn('UTF8_FULL_HTML_ORIGINAL_SCOPE_CONDITIONS',prompt)
        self.assertIn(self.source.read_text(),prompt);self.assertIn(injected.read_text(),prompt)
        self.assertTrue(original['reference_only']);self.assertNotIn('reference_only',text)
        self.assertNotIn('reference_only',ordinary)
        start=prompt.index('【显式指定完整原件准确引用：')
        reference=json.JSONDecoder().raw_decode(prompt[prompt.index('{',start):])[0]
        self.assertEqual(reference['content_delivery'],'explicit_file_reference')
        self.assertEqual(reference['type'],'.html');self.assertEqual(reference['sha256'],original['hash'])
        self.assertEqual(reference['logical_name'],'original.html')
        self.assertEqual(Path(reference['path']).read_bytes(),html.read_bytes())
        self.assertEqual(reference['byte_size'],len(html.read_bytes()))
        self.assertIn(original['artifact_id'],package['input_artifacts'])

    def test_reference_only_requires_explicit_boolean_and_never_bypasses_hash(self):
        task=self.c.create(spec())['task_id']
        with self.assertRaises(GuardError):self.c.add_source(task,self.source,reference_only='true')
        self.assertFalse(self.c.get(task)['source_files'])
        original=self.c.add_source(task,self.source,reference_only=True)
        (self.c.task_path(task)/original['filename']).write_text('changed original scope')
        with self.assertRaises(GuardError):self.c.package(task)
        self.assertFalse(self.c.get(task)['packages'])


del CoreTest

if __name__=='__main__':unittest.main()
