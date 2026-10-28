import copy
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from cluster.core import Cluster, GuardError
from cluster.document_stage import inspect_docx, write_view_docx, check_view_docx


def view():
    return {'tables':[{'id':'courses','columns':['条目','课程','原计量','条件'],
                       'rows':[{'item_ids':['R1'],'cells':['R1','实验课程','≥1门','二选一']},
                               {'item_ids':['R2'],'cells':['R2','研究训练','按选题确定','原件未单列固定学分']} ]},
                      {'id':'groups','columns':['共享组','成员','额度'],
                       'rows':[{'item_ids':['G1'],'cells':['G1','甲、乙','合计≥12学分']}]}],
            'notes':['甲乙共享额度不得重复累计。']}


class DocumentStageTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def file(self, data=None, widths=None):
        p=self.root/'synthetic.docx';write_view_docx(data or view(),p,'合成课程清单',widths);return p
    def transform(self,path,member,old,new):
        with zipfile.ZipFile(path) as z:files={n:z.read(n) for n in z.namelist()}
        self.assertIn(old,files[member]);files[member]=files[member].replace(old,new)
        with zipfile.ZipFile(path,'w') as z:
            for n,b in files.items():z.writestr(n,b)
    def test_T18_column_width_only_preserves_semantics(self):
        p=self.file();a=inspect_docx(p.read_bytes());original=p.read_bytes()
        self.file(widths={'courses':[1700,5000,3200,4900]})
        self.assertNotEqual(p.read_bytes(),original)
        self.assertEqual(inspect_docx(p.read_bytes())['semantic_sha256'],a['semantic_sha256'])
        check_view_docx(view(),inspect_docx(p.read_bytes()),'合成课程清单')
    def test_T19_shared_credit_and_merge_are_not_mechanical(self):
        p=self.file();self.transform(p,'word/document.xml','合计≥12学分'.encode(),'各≥12学分'.encode())
        with self.assertRaisesRegex(ValueError,'drift'):check_view_docx(view(),inspect_docx(p.read_bytes()),'合成课程清单')
        p=self.file();self.transform(p,'word/document.xml',b'<w:tcPr>',b'<w:tcPr><w:gridSpan w:val="2" />')
        with self.assertRaisesRegex(ValueError,'boundaries'):check_view_docx(view(),inspect_docx(p.read_bytes()),'合成课程清单')
    def test_hidden_text_does_not_inherit(self):
        p=self.file();self.transform(p,'word/document.xml',b'<w:r>',b'<w:r><w:rPr><w:vanish /></w:rPr>')
        with self.assertRaisesRegex(ValueError,'Hidden'):inspect_docx(p.read_bytes())
    def test_title_normal_no_direct_or_inherited_border(self):
        p=self.file();self.assertTrue(inspect_docx(p.read_bytes())['repeat_headers'])
        self.transform(p,'word/styles.xml',b'<w:name w:val="Normal" />',b'<w:name w:val="Normal" /><w:pPr><w:pBdr/></w:pPr>')
        with self.assertRaisesRegex(ValueError,'Inherited'):inspect_docx(p.read_bytes())
    def test_T17_legacy_task_cannot_claim_native_stage(self):
        c=Cluster(self.root/'workspace','test')
        try:
            spec={'objective':'synthetic','task_type':'local','rules':['offline'],
                  'acceptance':['test'],'validators':[{'kind':'json','required_fields':['task_id']}]}
            t=c.create(spec)['task_id']
            with self.assertRaisesRegex(GuardError,'not enabled'):c.request_document_export(t,{'key':'full','view':'full','purpose':'final','title':'x'})
            with self.assertRaises(GuardError):c.freeze_content(t)
            self.assertFalse(c.document_stage_status(t)['content_frozen'])
        finally:c.close()
    def test_native_workflow_cannot_disable_content_or_review(self):
        c=Cluster(self.root/'workspace','test')
        try:
            spec={'objective':'synthetic','task_type':'local','rules':['offline'],
                  'acceptance':['test'],'validators':[{'kind':'json','required_fields':['task_id']}],
                  'native_document_workflow_v1':True}
            with self.assertRaises(GuardError):c.create(spec)
        finally:c.close()


if __name__=='__main__':unittest.main()
