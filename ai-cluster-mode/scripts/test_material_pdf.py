"""Optional PDF/image adapter suite; needs existing pypdf/pdfplumber/Pillow/reportlab.
Run separately from standard-library portable core; no skips or GUI/provider calls.
"""
import copy,io,json,sys,tempfile,unittest
from pathlib import Path
RUNTIME=Path(__file__).resolve().parents[1]/'assets/runtime'
sys.path.insert(0,str(RUNTIME))
from cluster.core import Cluster,GuardError,js
from cluster.materials import pdf_check,sha,whitespace,unpack
from reportlab.pdfgen import canvas
from pypdf import PdfReader,PdfWriter
from PIL import Image,ImageDraw

def pdf_bytes(text='charge = -1 AND x > 6',x=40,blank=False):
    b=io.BytesIO();c=canvas.Canvas(b,pagesize=(400,300))
    if not blank:c.drawString(x,250,text)
    c.showPage();c.save();return b.getvalue()

class PDFAdapterTests(unittest.TestCase):
    def test_text_and_whitespace_preserved(self):
        self.assertEqual(pdf_check(pdf_bytes(),'charge = -1 AND x > 6',{})['status'],'pass')
        self.assertEqual(whitespace('x >\n 6'),'x > 6')
    def test_symbol_negatives_are_not_normalized_away(self):
        for text in ('charge = 1 AND x > 6','charge = -1 OR x > 6','charge = -1 AND x >= 6'):
            with self.subTest(text=text):self.assertEqual(pdf_check(pdf_bytes(text),'charge = -1 AND x > 6',{})['status'],'fail')
    def test_absent_text_and_legitimate_blank_page(self):
        data=pdf_bytes(blank=True)
        self.assertEqual(pdf_check(data,None,{})['status'],'fail')
        self.assertEqual(pdf_check(data,'',{'allowed_blank_pages':[1]})['status'],'pass')
    def test_clipped_text_and_replacement_character(self):
        self.assertEqual(pdf_check(pdf_bytes(x=380),None,{})['status'],'fail')
        self.assertNotEqual(whitespace('x\ufffd'),whitespace('x'))
    def test_page_scope_and_link_set(self):
        self.assertEqual(pdf_check(pdf_bytes(),None,{'expected_pages':2})['status'],'fail')
        self.assertEqual(pdf_check(pdf_bytes(),None,{'expected_links':['https://example.invalid/source']})['status'],'fail')
    def make_task(self,root,pdf=None,image=False,blank_image=False,wrong_image_entity=False):
        c=Cluster(root,'test');t=c.create({'objective':'Synthetic local adapter check','task_type':'file','task_route':'file_processing',
          'rules':['Exact originals'],'acceptance':['Current file check'],'validators':[{'kind':'json','required_fields':['task_id']} ]})['task_id']
        originals=Path(root)/'originals';originals.mkdir();ctx={'entity':'SIM','version':'test-only'}
        if image:
            raw=b'<title>Public lesson</title><p>Diagram</p><img src="diagram.png">';name='raw.html';adapter='static_html_v1'
        else:raw=js({'listLength':1,'data':[{'id':'a','text':'charge = -1 AND x > 6'}]}).encode();name='raw.json';adapter='catalog_v1'
        p=originals/name;p.write_bytes(raw);a=c.add_source(t,p)
        basis={'scope':'One synthetic record','scope_authority':'Isolated test contract','origin':'test_fixture','expected_count':1,'expected_ids':['a'],
             'id_field':'id','fields':['text'],'observed_contexts':[{'source_sha256':sha(raw),'context':ctx}]}
        if image:
            im=Image.new('RGB',(40,40),'white')
            if not blank_image:ImageDraw.Draw(im).rectangle((10,10,30,30),fill='blue')
            p=originals/'diagram.png';im.save(p);c.add_source(t,p)
            basis['images']=[{'src':'diagram.png','source_name':'diagram.png','sha256':sha(p.read_bytes()),'entity':'OTHER' if wrong_image_entity else 'SIM','purpose':'substantive'}]
        p=originals/'basis.json';p.write_text(js(basis));b=c.add_source(t,p)
        item={'key':'one','adapter':adapter,'source_artifacts':[a['artifact_id']],'basis_artifact':b['artifact_id'],'target_context':ctx,'reading_file':'reading.html'}
        if pdf:item.update(pdf_file='reading.pdf',full_visual_required=True)
        c.configure_material_contract(t,{'schema_version':'material_contract_v1','items':[item]});c.package(t);c.start_local(t)
        out=c._output_directory(t,create=True);c.restore_material(t,'one',out/'reading.html');files=[out/'reading.html']
        if image:files.append(out/'diagram.png')
        if pdf:(out/'reading.pdf').write_bytes(pdf);files.append(out/'reading.pdf')
        p=out/'candidate.json';p.write_text(js({'task_id':t,'pass':True}));c.local_output(t,p);c.freeze_candidate_files(t,files)
        return c,t
    def test_substantive_image_positive_blank_and_wrong_entity(self):
        for blank,wrong in ((False,False),(True,False),(False,True)):
            with tempfile.TemporaryDirectory() as root:
                c,t=self.make_task(root,image=True,blank_image=blank,wrong_image_entity=wrong)
                try:self.assertEqual(c.validate(t)['passed'],not(blank or wrong))
                finally:c.close()
    def test_pdf_visual_missing_and_old_hash_rejected_in_controller(self):
        with tempfile.TemporaryDirectory() as root:
            c,t=self.make_task(root,pdf=pdf_bytes())
            try:
                r=c.validate(t);self.assertFalse(r['passed']);self.assertIn('current_full_visual_missing',r['materials']['items'][0]['format']['errors'])
                f=next(f for f in c.get(t)['candidate_files'] if f['logical_filename']=='reading.pdf')
                with self.assertRaises(GuardError):c.record_material_visual(t,{'pdf_artifact_id':f['artifact_id'],'pdf_sha256':'a'*64})
                with self.assertRaises(GuardError):c.finalize(t)
            finally:c.close()
    def test_producer_pass_and_no_render_evidence_cannot_authorize_pdf(self):
        with tempfile.TemporaryDirectory() as root:
            c,t=self.make_task(root,pdf=pdf_bytes())
            try:
                f=next(f for f in c.get(t)['candidate_files'] if f['logical_filename']=='reading.pdf')
                o={'pdf_artifact_id':f['artifact_id'],'pdf_sha256':f['sha256'],'observer':'codex','kind':'actual_page_observation',
                   'pages':[1],'dpi':144,'scope':'all_pages','notes':'Unverified producer claim','passed':True,'render_artifacts':[]}
                with self.assertRaises(GuardError):c.record_material_visual(t,o)
                self.assertFalse(c.validate(t)['passed'])
            finally:c.close()

if __name__=='__main__':unittest.main()
