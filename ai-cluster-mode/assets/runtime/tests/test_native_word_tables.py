"""Real native-Word failure replay and minimal portable-template regression.

The stored actual Word bytes must continue failing canonical consistency. Tests
here prove structural prevention; native GUI save/export/page QA is separate.
"""
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from cluster.document_stage import inspect_docx,write_view_docx,check_view_docx

W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
FIXTURES=Path(__file__).parent/'fixtures/native_word_adjacent_tables'


class NativeWordTablesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.before=(FIXTURES/'before_native.docx').read_bytes()
        self.failed=(FIXTURES/'word_merged_failure.docx').read_bytes()
        self.inspection=inspect_docx(self.before)
        self.title=self.inspection['paragraphs'][0]
        self.view={'tables':[{'id':'table-'+str(i),'columns':rows[0],
            'rows':[{'cells':row} for row in rows[1:]]} for i,rows in enumerate(self.inspection['tables'])],
            'notes':self.inspection['paragraphs'][1:]}
    def tearDown(self):self.tmp.cleanup()
    def xml(self,data,name):
        import io
        with zipfile.ZipFile(io.BytesIO(data)) as z:return E.fromstring(z.read(name))

    def test_actual_failed_native_bytes_and_hashes_remain_rejected(self):
        record=json.loads((FIXTURES/'provenance.json').read_text())
        for item in record['files']:
            data=(FIXTURES/item['fixture']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),item['sha256'])
            self.assertEqual(len(data),item['bytes'])
        before=inspect_docx(self.before);after=inspect_docx(self.failed)
        self.assertEqual([len(t) for t in before['tables']],[9,3])
        self.assertEqual([len(t) for t in after['tables']],[12])
        self.assertTrue(any(after['relations']))
        check_view_docx(self.view,before,self.title)
        with self.assertRaisesRegex(ValueError,'drift'):
            check_view_docx(self.view,after,self.title)

    def test_writer_separates_native_tables_without_changing_business_text(self):
        path=write_view_docx(self.view,self.root/'fixed-template.docx',self.title)
        data=path.read_bytes();body=self.xml(data,'word/document.xml').find(W+'body')
        children=list(body);table_indices=[i for i,x in enumerate(children) if x.tag==W+'tbl']
        self.assertEqual(len(table_indices),2)
        self.assertGreater(table_indices[1]-table_indices[0],1)
        for element in children[table_indices[0]+1:table_indices[1]]:
            self.assertEqual(element.tag,W+'p')
            self.assertFalse(''.join(x.text or '' for x in element.iter(W+'t')))
        actual=inspect_docx(data)
        self.assertEqual(actual['tables'],self.inspection['tables'])
        self.assertEqual([p for p in actual['paragraphs'] if p],self.inspection['paragraphs'])
        self.assertFalse(any(actual['relations']))
        check_view_docx(self.view,actual,self.title)

    def test_default_font_cell_wrapping_fixed_grid_and_plain_title(self):
        path=write_view_docx(self.view,self.root/'fixed-template.docx',self.title)
        data=path.read_bytes();doc=self.xml(data,'word/document.xml');styles=self.xml(data,'word/styles.xml')
        normal=styles.find(W+'style')
        self.assertEqual(normal.get(W+'styleId'),'Normal')
        self.assertEqual(normal.find(W+'rPr/'+W+'rFonts').get(W+'eastAsia'),'宋体')
        self.assertEqual(normal.find(W+'rPr/'+W+'sz').get(W+'val'),'18')
        title=doc.find(W+'body/'+W+'p')
        self.assertIsNone(title.find(W+'pPr/'+W+'pBdr'))
        self.assertIsNone(normal.find(W+'pPr/'+W+'pBdr'))
        for table in doc.findall(W+'body/'+W+'tbl'):
            self.assertEqual(table.find(W+'tblPr/'+W+'tblLayout').get(W+'type'),'fixed')
            for cell in table.iter(W+'tc'):
                self.assertIsNone(cell.find(W+'tcPr/'+W+'noWrap'))
                # Microsoft Open XML WordWrap example: off permits Latin
                # character-level breaking; true would preserve whole words.
                self.assertEqual(cell.find(W+'p/'+W+'pPr/'+W+'wordWrap').get(W+'val'),'off')
        self.assertTrue(inspect_docx(data)['repeat_headers'])

if __name__=='__main__':unittest.main(verbosity=2)
