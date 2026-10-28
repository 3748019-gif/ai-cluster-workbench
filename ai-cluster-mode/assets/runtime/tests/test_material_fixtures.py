"""Replay supplied synthetic fixtures in the native offline adapters, not site claims."""
import copy,json,tempfile,unittest,zipfile
from pathlib import Path
from cluster.materials import unpack,sha,whitespace,render_reading
from cluster.release_checks import check_release,build_manifest
from cluster.release_binding import inspect_binding
F=Path(__file__).parent/'fixtures/materials'
class SuppliedFixtureTests(unittest.TestCase):
 def test_dynamic_two_components_exact_cells_and_missing_component(self):
  d=json.loads((F/'dynamic/components.json').read_text());docs=[];tables=[]
  for c in d['components']:
   rows=[c['headers']]+c['rows']+[[c['note']]]
   docs.append(json.dumps({'caption':c['id'],'thead':{'rows':[]},'tbody':{'rows':[{'cells':[{'content':str(v)} for v in r]} for r in rows]}}).encode())
   tables.append({'row_widths':[len(r) for r in rows],'cells':[{'row':ri,'col':ci,'text':whitespace(str(v))} for ri,r in enumerate(rows) for ci,v in enumerate(r)]})
  b={'tables':tables};r,e,_=unpack('eth_table_v1',docs,b);self.assertEqual(e,[])
  html=render_reading(r,'eth_table_v1','Test only').decode();self.assertIn('SIM-105 OR SIM-125',html);self.assertIn('AND',html)
  self.assertIn('table_component_mismatch',unpack('eth_table_v1',docs[:1],b)[1])
  self.assertIn('missing_dynamic_components',unpack('static_html_v1',[(F/'dynamic/shell.html').read_bytes()],{'required_tables':2})[1])
  bad=json.loads(docs[0]);bad['tbody']['rows'][1]['cells'].reverse()
  self.assertIn('table_position_or_symbol_mismatch',unpack('eth_table_v1',[json.dumps(bad).encode(),docs[1]],b)[1])
 def test_snapshot_161_unique_four_pages_partial_duplicate_loop_version(self):
  d=json.loads((F/'pagination/snapshot.json').read_text());pages=[];requests=[]
  for i,p in enumerate(d['pages']):
   x={'data':{'node':{'id':d['snapshot_id'],'numRatings':p['reported_total'],'ratings':{'edges':[{'node':{'id':r['id'],'comment':r['text']}} for r in p['records']],'pageInfo':{'hasNextPage':p['next_cursor'] is not None,'endCursor':p['next_cursor']}}}}}
   raw=json.dumps(x).encode();pages.append(raw);requests.append({'cursor':None if i==0 else p['cursor'],'query':d['snapshot_id'],'source_sha256':sha(raw)})
  b={'entity':d['snapshot_id'],'expected_count':161,'fields':['comment'],'query':d['snapshot_id'],'page_requests':requests}
  r,e,detail=unpack('cursor_v1',pages,b);self.assertEqual(e,[]);self.assertEqual(detail['unique_count'],161);self.assertTrue(detail['terminal'])
  self.assertIn('pagination_not_exhausted',unpack('cursor_v1',pages[:1],b)[1])
  bad=json.loads(pages[-1]);bad['data']['node']['ratings']['edges'][0]['node']['id']='R001'
  self.assertIn('duplicate_item_id',unpack('cursor_v1',pages[:-1]+[json.dumps(bad).encode()],b)[1])
  b2=copy.deepcopy(b);b2['page_requests'][2]['cursor']='P2';self.assertIn('duplicate_cursor',unpack('cursor_v1',pages,b2)[1])
  bad=json.loads(pages[1]);bad['data']['node']['id']='different-snapshot'
  self.assertIn('entity_drift',unpack('cursor_v1',[pages[0],json.dumps(bad).encode()]+pages[2:],b)[1])
 def test_supplied_error_pages_symbols_and_distinct_editions(self):
  self.assertNotIn('error_document',unpack('static_html_v1',[(F/'errors/legitimate_login_lesson.html').read_bytes()],{})[1])
  self.assertIn('error_document',unpack('static_html_v1',[(F/'errors/login_200.html').read_bytes()],{})[1])
  d=json.loads((F/'symbols/negative_pairs.json').read_text())
  for a,b in d['must_not_treat_as_equivalent']:self.assertNotEqual(whitespace(a),whitespace(b))
  editions=json.loads((F/'versions/editions.json').read_text());self.assertNotEqual(editions['primary']['revision'],editions['alternative']['revision'])
  # Explicit version-scoped IDs refuse stitched A8+B10 coverage as an A15 list.
  rows=[{'id':'A-'+str(i),'text':'A 20:00'} for i in range(8)]+[{'id':'B-'+str(i),'text':'B 19:00'} for i in range(10)]
  basis={'id_field':'id','fields':['text'],'expected_count':15,'expected_ids':['A-'+str(i) for i in range(15)]}
  _,errs,_=unpack('catalog_v1',[json.dumps({'listLength':18,'data':rows}).encode()],basis)
  self.assertIn('coverage_id_set_mismatch',errs);self.assertIn('coverage_count_mismatch',errs)
 def test_required_chinese_empty_directory_survives_exact_zip(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)/'release';root.mkdir();(root/'正文.txt').write_text('accepted');(root/'中文空目录').mkdir()
   (root/'MANIFEST_SHA256.json').write_text(json.dumps(build_manifest(root)))
   options={'required_directories':['中文空目录']};self.assertTrue(check_release(root,**options)['passed'])
   self.assertFalse(check_release(root,required_directories=['缺少'])['passed']);self.assertFalse(check_release(root,required_directories=['../escape'])['passed'])
   h=sha((root/'正文.txt').read_bytes());binding=[{'output_path':'正文.txt','output_sha256':h,'transform':{'type':'identity_v1','version':1}}]
   def accepted(_):return b'accepted',{}
   z=Path(tmp)/'out.zip'
   with zipfile.ZipFile(z,'w') as a:
    for p in root.iterdir():
     if p.is_file():a.write(p,p.name)
   with self.assertRaisesRegex(ValueError,'Required directory'):inspect_binding(root,'MANIFEST_SHA256.json',binding,accepted,options,zip_path=z)
   with zipfile.ZipFile(z,'a') as a:a.writestr('中文空目录/','')
   self.assertTrue(inspect_binding(root,'MANIFEST_SHA256.json',binding,accepted,options,zip_path=z)['passed'])
if __name__=='__main__':unittest.main(verbosity=2)
