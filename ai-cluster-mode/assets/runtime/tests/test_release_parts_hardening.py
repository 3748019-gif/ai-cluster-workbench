"""DOCX reference parts/split runs and explicit archive roles, no rendering."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from cluster.release_checks import build_manifest, check_release
from cluster.release_binding import transformed_bytes, content_matches


class ReleasePartsHardeningTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'source.txt').write_text('Synthetic original')
        self.mapping=[{'release_source_id':'REL-WB-001','namespace':'exact','source_id':'S001','source_version':'2026','relative_paths':['source.txt']}]

    def tearDown(self):self.temp.cleanup()

    def docx(self, parts, name='current.docx'):
        path=self.root/name
        with zipfile.ZipFile(path,'w') as archive:
            for part,texts in parts.items():
                body=''.join('<w:p>'+''.join('<w:r><w:t>'+escape(run)+'</w:t></w:r>' for run in (text if isinstance(text,list) else [text]))+'</w:p>' for text in texts)
                archive.writestr('word/'+part,"<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body>"+body+'</w:body></w:document>')
        return path

    def freeze(self):
        (self.root/'MANIFEST_SHA256.json').write_text(json.dumps(build_manifest(self.root)))

    def report(self,**options):
        self.freeze();return check_release(self.root,source_map=self.mapping,**options)

    def codes(self,r):return {x['code'] for x in r['issues']}

    def test_split_runs_fullwidth_contiguous_legal_special_ID(self):
        self.docx({'document.xml':[['［REL-','WB-001］【S001】(REL-WB-001)']]})
        self.assertTrue(self.report()['passed'])

    def test_unknown_new_ID_and_bare_short_already_fixed(self):
        self.docx({'document.xml':['[REL-WB-999] [S999]']})
        r=self.report();self.assertIn('source_reference_unknown',self.codes(r))
        self.assertEqual({x.get('reference') for x in r['issues']},{'REL-WB-999','S999'})

    def test_header_footer_footnote_endnote_location(self):
        self.docx({'document.xml':['[REL-WB-001]'], 'header1.xml':['[REL-WB-999]'],
                   'footer1.xml':['[S999]'], 'footnotes.xml':['[REL-WB-998]'], 'endnotes.xml':['[S998]']})
        r=self.report();self.assertEqual(len([x for x in r['issues'] if x['code']=='source_reference_unknown']),4)
        locations=' '.join(x['location'] for x in r['issues'])
        for part in ('header1.xml','footer1.xml','footnotes.xml','endnotes.xml'):self.assertIn(part,locations)

    def test_table_and_hyperlink_visible_text(self):
        self.docx({'document.xml':['[REL-WB-001]']})
        p=self.root/'current.docx'
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('word/document.xml',"<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:tbl><w:tr><w:tc><w:p><w:hyperlink><w:r><w:t>［REL-WB-999］</w:t></w:r></w:hyperlink></w:p></w:tc></w:tr></w:tbl></w:body></w:document>")
        self.assertIn('source_reference_unknown',self.codes(self.report()))

    def test_incomplete_source_markers_rejected(self):
        self.docx({'document.xml':['[REL-WB-] [REL-] [S01] [WB-S] ［S001extra］ 【REL-WB-001_】']})
        r=self.report();malformed=[x.get('reference') for x in r['issues'] if x['code']=='source_reference_malformed']
        self.assertEqual(set(malformed),{'REL-WB-','REL-','S01','WB-S','S001extra','REL-WB-001_'})

    def test_unique_namespace_works_and_ambiguous_bare_rejects(self):
        self.mapping.append({'release_source_id':'REL-WB-002','namespace':'other','source_id':'S001','source_version':'2026','relative_paths':['source.txt']})
        self.docx({'document.xml':['[exact::S001]']})
        self.assertTrue(self.report()['passed'])
        self.docx({'document.xml':['[S001]']})
        self.assertIn('source_reference_ambiguous',self.codes(self.report()))

    def test_explicit_hash_bound_history_excluded_but_current_cannot(self):
        self.docx({'document.xml':['[REL-WB-001]']})
        old=self.docx({'document.xml':['[REL-WB-999]']},'history.docx')
        import hashlib
        role={'history.docx':{'role':'historical','sha256':hashlib.sha256(old.read_bytes()).hexdigest(),'reason':'Preserved original audit counterexample'}}
        self.assertTrue(self.report(required_documents=['current.docx'],document_roles=role)['passed'])
        self.assertIn('current_document_exclusion',self.codes(self.report(document_roles=role)))
        old.write_bytes(old.read_bytes()+b'changed')
        self.assertIn('document_role_hash_mismatch',self.codes(self.report(required_documents=['current.docx'],document_roles=role)))

    def test_actual_scope_names_parts_and_unhandled_field_warning(self):
        self.docx({'document.xml':['[REL-WB-001]']})
        r=self.report()
        scan=next(x for x in r['classifications'] if x['kind']=='docx_reference_scan_scope')
        self.assertEqual(scan['parts'],['word/document.xml'])
        self.assertTrue(any('altChunk' in x and 'drawing' in x for x in r['limitations']))

    def test_docx_reference_replay_preserves_all_other_parts_and_unknown(self):
        original=self.docx({'document.xml':[['READY [S','001] unknown remains unknown']], 'footer1.xml':['Footer unchanged']}).read_bytes()
        transform={'type':'docx_reference_map_v1','version':1,'mapping':[{'from':'S001','to':'REL-WB-001','namespace':'exact'}]}
        mapping=[dict(r,original_url='https://example.org/original') for r in self.mapping]
        authority=[{'namespace':'exact','source_id':'S001','original_url':'https://example.org/original'}]
        expected=transformed_bytes(original,transform,{'source_map':mapping},source_authority=authority)
        import io
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for name,data in expected.items():archive.writestr(name,data)
        self.assertTrue(content_matches(original,stream.getvalue(),transform,{'source_map':mapping},source_authority=authority))
        other=io.BytesIO()
        with zipfile.ZipFile(other,'w') as archive:
            for name,data in expected.items():archive.writestr(name,data.replace(b'unknown remains unknown',b'verified fact'))
        self.assertFalse(content_matches(original,other.getvalue(),transform,{'source_map':mapping},source_authority=authority))

    def test_semantic_word_map_rejected_even_registered(self):
        fake=[{'release_source_id':'必修','namespace':'fake','source_id':'任选','relative_paths':['source.txt']}]
        with self.assertRaises(ValueError):transformed_bytes('任选'.encode(),{'type':'utf8_reference_map_v1','version':1,'mapping':[{'from':'任选','to':'必修','namespace':'fake'}]},{'source_map':fake})


if __name__=='__main__':unittest.main()
