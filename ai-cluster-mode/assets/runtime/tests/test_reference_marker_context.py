"""Ordinary semester/storage text is not a malformed source citation."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from cluster.release_checks import build_manifest, check_release


class ReferenceMarkerContextTest(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name)
        (self.root/'source.txt').write_text('Original')
        self.mapping=[{'release_source_id':'REL-WB-001','namespace':'exact','source_id':'S001','source_version':'2026','relative_paths':['source.txt']}]

    def tearDown(self):self.t.cleanup()

    def check(self,text):
        with zipfile.ZipFile(self.root/'current.docx','w') as z:
            z.writestr('word/document.xml',"<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:p><w:r><w:t>"+escape(text)+'</w:t></w:r></w:p></w:body></w:document>')
        (self.root/'MANIFEST_SHA256.json').write_text(json.dumps(build_manifest(self.root)))
        return check_release(self.root,source_map=self.mapping)

    def test_semester_S1_S2_and_storage_S3_are_ordinary_prose(self):
        r=self.check('AY23/24 S1、AY24/25 S2、AY25/26 S1。undergraduate.catalog.berkeley.edu（S3直链PDF）。 [REL-WB-001]')
        self.assertTrue(r['passed'],r)

    def test_unbracketed_course_prefix_is_not_a_claimed_source_ID(self):
        r=self.check('课程分组CHEM-S；前缀CAL-S、CAL-Sxxx及短号S1/S2为普通文本。 [REL-WB-001]')
        self.assertTrue(r['passed'],r)

    def test_explicit_short_and_template_citations_still_malformed(self):
        r=self.check('[S1] ［S01］ 【CAL-Sxxx】 [CHEM-S]')
        invalid={x.get('reference') for x in r['issues'] if x['code']=='source_reference_malformed'}
        self.assertEqual(invalid,{'S1','S01','CAL-Sxxx','CHEM-S'})

    def test_namespace_explicit_short_citation_is_malformed(self):
        r=self.check('exact::S01; [exact::S001]')
        invalid=[x for x in r['issues'] if x['code']=='source_reference_malformed']
        self.assertEqual(len(invalid),1)
        self.assertEqual(invalid[0]['reference'],'S01')
        self.assertEqual(invalid[0]['namespace'],'exact')

    def test_complete_unknown_and_incomplete_REL_still_rejected(self):
        r=self.check('S999 UCB-S999 REL-WB-999 REL-WB-')
        refs={x.get('reference'):x['code'] for x in r['issues']}
        self.assertEqual(refs['S999'],'source_reference_unknown')
        self.assertEqual(refs['UCB-S999'],'source_reference_unknown')
        self.assertEqual(refs['REL-WB-999'],'source_reference_unknown')
        self.assertEqual(refs['REL-WB-'],'source_reference_malformed')


if __name__=='__main__':unittest.main()
