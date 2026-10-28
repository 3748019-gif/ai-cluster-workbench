"""Offline material contracts and receipts; no network or release authorization.

Six dimensions stay independent. Historical sources without a contract stay
unknown. Raw data, scope evidence, recovered files and PDFs retain exact hashes.
"""
import copy
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import unicodedata

VERSION = 'material_contract_v1'
DETECTOR = 'material_detector_v1'

def sha(data): return hashlib.sha256(data).hexdigest()
def canonical(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def whitespace(text):
    # Preserve case, punctuation, AND/OR, comparison and minus signs.
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC',text)).strip()

class Visible(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.text=[];self.hidden=0;self.cells=[];self.row=-1;self.col=0;self.cell=None;self.images=[];self.title=False;self.titles=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('script','style'):self.hidden+=1
        if tag=='title':self.title=True
        if tag=='tr':self.row+=1;self.col=0
        if tag in ('td','th'):
            self.cell={'row':self.row,'col':self.col,'rowspan':int(a.get('rowspan','1')),'colspan':int(a.get('colspan','1')),'text':[]};self.col+=self.cell['colspan']
        if tag=='img':self.images.append(a.get('src',''))
        if tag in ('br','p','div','li','tr'):self.handle_data(' ')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
        if tag=='title':self.title=False
        if tag in ('td','th') and self.cell:
            self.cell['text']=whitespace(''.join(self.cell['text']));self.cells.append(self.cell);self.cell=None
    def handle_data(self,data):
        if self.hidden:return
        self.text.append(data)
        if self.title:self.titles.append(data)
        if self.cell is not None:self.cell['text'].append(data)

def visible(text):
    p=Visible();p.feed(text);return p

def clean_markup(value):return whitespace(''.join(visible(str(value)).text))

def unpack(adapter, blobs, basis):
    """Return ordered semantic records and concrete coverage issues."""
    issues=[];records=[];details={}
    if adapter=='static_html_v1':
        if len(blobs)!=1:return [],['unsupported_static_collection'],{}
        p=visible(blobs[0].decode('utf-8'));title=whitespace(''.join(p.titles)).lower()
        if title in ('login','sign in','403 forbidden','access denied'):issues.append('error_document')
        text=whitespace(''.join(p.text))
        if not text and not basis.get('allow_empty'):issues.append('empty_or_unusable_container')
        tables=len(re.findall(rb'<table\b',blobs[0],re.I))
        if basis.get('required_tables',0) and tables!=basis['required_tables']:issues.append('missing_dynamic_components')
        return [{'id':'body','values':[text],'images':p.images,'raw_html':blobs[0].decode('utf-8')}],issues,{'actual_count':1,'images':p.images,'tables':tables}
    docs=[json.loads(b) for b in blobs]
    if adapter=='catalog_v1':
        if len(docs)!=1 or not isinstance(docs[0],dict) or not isinstance(docs[0].get('data'),list):
            return [],['unsupported_catalog_structure'],{}
        d=docs[0];fields=basis.get('fields',[])
        if not fields or basis.get('id_field') is None:return [],['missing_catalog_fields'],{}
        for item in d['data']:
            if basis.get('selected_ids') is not None and str(item.get(basis['id_field'])) not in set(map(str,basis['selected_ids'])):continue
            if not isinstance(item,dict) or any(f not in item for f in fields) or basis['id_field'] not in item:
                issues.append('missing_catalog_component');continue
            records.append({'id':str(item[basis['id_field']]),'values':[clean_markup(item[f]) for f in fields]})
        details['reported_count']=d.get('listLength')
        if d.get('listLength')!=len(d['data']):issues.append('catalog_total_mismatch')
    elif adapter=='eth_table_v1':
        for ti,d in enumerate(docs):
            if not isinstance(d,dict) or not all(isinstance(d.get(s),dict) and isinstance(d[s].get('rows'),list) for s in ('thead','tbody')):
                issues.append('unsupported_table_structure');continue
            rows=d['thead']['rows']+d['tbody']['rows'];cells=[]
            for ri,row in enumerate(rows):
                if not isinstance(row,dict) or not isinstance(row.get('cells'),list):issues.append('missing_table_row');continue
                for ci,cell in enumerate(row['cells']):
                    if not isinstance(cell,dict) or 'content' not in cell:issues.append('missing_table_cell');continue
                    # This adapter supports rectangular nonspanning cells only.
                    if int(cell.get('rowspan',1))!=1 or int(cell.get('colspan',1))!=1:issues.append('unsupported_cell_span')
                    cells.append({'row':ri,'col':ci,'text':clean_markup(cell['content'])})
            records.append({'id':str(ti),'cells':cells,'row_widths':[len(row.get('cells',[])) for row in rows], 'caption':clean_markup(d.get('caption',''))})
        details['component_count']=len(records)
    elif adapter=='cursor_v1':
        used_cursors=[];nodes=[];counts=[];next_expected=None
        requests=basis.get('page_requests',[])
        if len(requests)!=len(docs):issues.append('missing_page_request_evidence')
        for pi,d in enumerate(docs):
            node=d.get('data',{}).get('node',{}) if isinstance(d,dict) else {}
            ratings=node.get('ratings',{});page=ratings.get('pageInfo',{});edges=ratings.get('edges')
            if not isinstance(edges,list) or not isinstance(page.get('hasNextPage'),bool):issues.append('unsupported_cursor_structure');continue
            nodes.append(node.get('id'));counts.append(node.get('numRatings'))
            req=requests[pi] if pi<len(requests) else {}
            if req.get('cursor')!=next_expected:issues.append('cursor_chain_gap')
            if req.get('query')!=basis.get('query'):issues.append('query_drift')
            if req.get('source_sha256')!=sha(blobs[pi]):issues.append('page_request_hash_mismatch')
            if pi and req.get('cursor') in used_cursors:issues.append('duplicate_cursor')
            used_cursors.append(req.get('cursor'))
            for e in edges:
                n=e.get('node',{});rid=n.get('id')
                if not rid or 'comment' not in n:issues.append('missing_list_item');continue
                records.append({'id':str(rid),'values':[clean_markup(n.get(f,'')) for f in basis.get('fields',['comment'])]})
            next_expected=page.get('endCursor')
            if page['hasNextPage'] and (not next_expected or pi==len(docs)-1):issues.append('pagination_not_exhausted')
            if not page['hasNextPage'] and pi!=len(docs)-1:issues.append('pages_after_terminal')
        if len(set(nodes))!=1 or (nodes and nodes[0]!=basis.get('entity')):issues.append('entity_drift')
        if len(set(counts))!=1 or (counts and counts[0]!=basis.get('expected_count')):issues.append('snapshot_total_drift')
        details.update(terminal=bool(docs) and not docs[-1].get('data',{}).get('node',{}).get('ratings',{}).get('pageInfo',{}).get('hasNextPage',True),page_count=len(docs))
    else:return [],['unsupported_adapter'],{}
    ids=[x['id'] for x in records]
    if len(ids)!=len(set(ids)):issues.append('duplicate_item_id')
    if adapter!='eth_table_v1':
        if len(records)!=basis.get('expected_count'):issues.append('coverage_count_mismatch')
        if basis.get('expected_ids') is not None and set(ids)!=set(map(str,basis['expected_ids'])):issues.append('coverage_id_set_mismatch')
    else:
        expected=basis.get('tables')
        if not isinstance(expected,list) or len(expected)!=len(records):issues.append('table_component_mismatch')
        else:
            for r,e in zip(records,expected):
                if r['row_widths']!=e.get('row_widths'):issues.append('table_shape_mismatch')
                if r['cells']!=e.get('cells'):issues.append('table_position_or_symbol_mismatch')
    details.update(actual_count=len(records),unique_count=len(set(ids)),actual_ids=ids)
    return records,sorted(set(issues)),details

def render_reading(records,adapter,title,print_profile=None):
    if adapter=='static_html_v1':return records[0]['raw_html'].encode()
    style=print_profile or {}
    if set(style)-{'font_pt','margin_mm'}:raise ValueError('Unsupported print profile fields')
    font=style.get('font_pt',11);margin=style.get('margin_mm',16)
    if type(font) not in (int,float) or not 7<=font<=24 or type(margin) not in (int,float) or not 5<=margin<=40:raise ValueError('Print values out of supported range')
    out=['<!doctype html><html><head><meta charset="utf-8"><title>'+html.escape(title)+'</title>',
         '<style>@page{size:A4;margin:'+str(margin)+'mm}body{font:'+str(font)+'pt Arial;line-height:1.4}table{border-collapse:collapse;width:100%;table-layout:fixed}td{border:1px solid #888;padding:5px;overflow-wrap:anywhere}tr{break-inside:avoid}</style></head><body>']
    for r in records:
        if adapter=='eth_table_v1':
            out.append('<table data-component="'+r['id']+'">')
            if r['caption']:out.append('<caption>'+html.escape(r['caption'])+'</caption>')
            for ri,width in enumerate(r['row_widths']):
                out.append('<tr>')
                for ci in range(width):out.append('<td>'+html.escape(next(c['text'] for c in r['cells'] if c['row']==ri and c['col']==ci))+'</td>')
                out.append('</tr>')
            out.append('</table>')
        else:
            out.append('<section data-record="'+html.escape(r['id'])+'">')
            out.extend('<p>'+html.escape(v)+'</p>' for v in r['values']);out.append('</section>')
    out.append('</body></html>');return '\n'.join(out).encode()

def compare_reading(data,records,adapter,title,print_profile=None):
    # Deterministic format, including cell positions, units, symbols and footnotes.
    return data==render_reading(records,adapter,title,print_profile)

def pdf_check(data,expected,options):
    try:
        import io
        from pypdf import PdfReader
        import pdfplumber
    except ImportError:return {'status':'unsupported','errors':['pdf_dependencies_unavailable'],'visual':'unknown'}
    errors=[];blank=set(options.get('allowed_blank_pages',[]))
    try:
        reader=PdfReader(io.BytesIO(data));texts=[p.extract_text() or '' for p in reader.pages]
        for i,t in enumerate(texts,1):
            if not t.strip() and i not in blank:errors.append('no_text_layer_page_'+str(i))
            if '\ufffd' in t or '\x00' in t:errors.append('invalid_text_page_'+str(i))
        text=whitespace(' '.join(texts))
        # Only explicitly documented whitespace differences are normalized.
        if expected is not None and text!=whitespace(expected):errors.append('text_or_business_symbol_mismatch')
        if options.get('expected_pages') is not None and len(texts)!=options['expected_pages']:errors.append('page_count_mismatch')
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for i,p in enumerate(pdf.pages,1):
                for c in p.chars+p.images:
                    if c.get('x0',0)<-1 or c.get('x1',0)>p.width+1 or c.get('top',0)<-1 or c.get('bottom',0)>p.height+1:errors.append('out_of_page_content_'+str(i))
        links=[]
        for i,p in enumerate(reader.pages,1):
            for a in p.get('/Annots',[]):
                obj=a.get_object();uri=obj.get('/A',{}).get('/URI')
                if uri:
                    links.append(str(uri))
                    if not re.match(r'^(https?://|mailto:)',str(uri)):errors.append('relative_pdf_link_requires_resolution_'+str(i))
        if options.get('expected_links') is not None and sorted(links)!=sorted(options['expected_links']):errors.append('pdf_link_set_mismatch')
        return {'status':'pass' if not errors else 'fail','pages':len(texts),'text_sha256':sha(text.encode()),'errors':sorted(set(errors)),
                'links':links,'visual':'unknown','semantic_comparison':'exact_whitespace_only' if expected is not None else 'unknown',
                'images': 'geometry_only; visual content not established'}
    except Exception as e:return {'status':'fail','errors':['pdf_unreadable:'+type(e).__name__],'visual':'unknown'}

def dependency_plan(previous,current):
    """Independent stage keys propagate content changes, but not layout upstream."""
    changed=[k for k,v in current.items() if previous.get(k)!=v]
    return {'recheck':changed,'reuse':[k for k in current if k not in changed], 'old_receipts':'retained; stale only for changed dependencies'}

class MaterialMixin:
    def configure_material_contract(self,task,contract):
        from .core import GuardError,js
        with self._mutate(task) as m:
            if m['status']!='CREATED':raise GuardError('Material scope freezes before packaging; changes require a new work round')
            if contract.get('schema_version')!=VERSION or not contract.get('items'):raise GuardError('Invalid material contract')
            keys=[];ids=[]
            for it in contract['items']:
                keys.append(it.get('key'))
                if not it.get('key') or not it.get('source_artifacts') or not it.get('basis_artifact'):raise GuardError('Source IDs and independent scope evidence required')
                if it['basis_artifact'] in it['source_artifacts']:raise GuardError('Scope evidence cannot be the same raw payload')
                for aid in it['source_artifacts']+[it['basis_artifact']]:
                    if aid not in m['source_files']:raise GuardError('Contract evidence must be registered original sources')
                    self._fresh(m,aid);ids.append(aid)
                basis=json.loads(self.read_artifact(m,it['basis_artifact']))
                if not basis.get('scope') or not basis.get('scope_authority') or basis.get('origin') not in ('site_snapshot','frozen_original','user_declared_list','test_fixture'):raise GuardError('Scope needs explicit evidence provenance')
                if basis['origin']=='test_fixture' and self.mode!='test':raise GuardError('Synthetic basis cannot enter live task')
                exception=it.get('limited_delivery')
                if exception and (not exception.get('authorization_source') or not exception.get('missing_scope') or not exception.get('user_impact') or not exception.get('attempts') or not exception.get('claim') or not exception.get('allowed_errors')):raise GuardError('Limited delivery requires explicit gap authorization, attempts, claim, impact and exact allowed_errors')
            if len(set(keys))!=len(keys):raise GuardError('Duplicate contract item')
            c=self._artifact(m,js(contract).encode(),'evidence','codex','material-contract.json',list(dict.fromkeys(ids)))
            if contract.get('reuse_from'):
                old=self.get(contract['reuse_from'])
                if old['status']!='DELIVERED' or old['run_mode']!=m['run_mode'] or not self.audit(old['task_id'])['passed']:raise GuardError('Receipt reuse requires an audited delivered task in this controller')
                self._material_gate(old)
            m['material_contract']=c['artifact_id'];self._event(m,'MATERIAL_CONTRACT_FROZEN','orchestrator','Six-dimension scope and dependencies frozen',c['artifact_id'])
        return c

    def _material_results(self,m):
        from .core import GuardError
        cid=m.get('material_contract')
        if not cid:return {'schema_version':VERSION,'status':'unknown','passed':True,'items':[],'legacy':'No historical completeness inferred'}
        contract=json.loads(self.read_artifact(m,cid));items=[];old=None;previous={}
        if contract.get('reuse_from'):
            old=self.get(contract['reuse_from'])
            self._material_gate(old)
            vals=[v for v in old['validation'] if v['candidate_artifact_id']==old['candidate'] and v['passed']]
            if vals:
                prior=json.loads(self.read_artifact(old,vals[-1]['artifact_id'])).get('materials',{})
                previous={i['key']:i for i in prior.get('items',[])}
        for it in contract['items']:
            blobs=[self.read_artifact(m,a) for a in it['source_artifacts']];basis=json.loads(self.read_artifact(m,it['basis_artifact']))
            try:records,errors,details=unpack(it['adapter'],blobs,basis)
            except (ValueError,TypeError,KeyError,AttributeError):records,errors,details=[],['unsupported_or_unreadable_payload'],{}
            observed=basis.get('observed_contexts',[]);version='unknown'
            if len(observed)==len(blobs) and all(o.get('source_sha256')==sha(b) for o,b in zip(observed,blobs)):
                version='exact' if all(o.get('context')==it.get('target_context') for o in observed) else 'mismatch'
            if it.get('require_version',True) and version!='exact':errors.append('source_version_'+version)
            files=[f for f in m.get('candidate_files',[]) if f['logical_filename'] in (it.get('reading_file'),it.get('pdf_file'))]
            stage_keys=self._material_stage_keys(it,basis,blobs,files)
            fmt={'status':'unknown','errors':[]};access='public_payload' if records else 'unknown'
            if not records:errors.append('empty_or_unusable_container')
            if 'error_document' in errors:access='error_document'
            for required in basis.get('required_attachments',[]):
                matches=[a for a in m['source_files'] if self.artifact(m,a)['logical_name']==required['name']]
                if len(matches)!=1 or sha(self.read_artifact(m,matches[0]))!=required['sha256']:errors.append('missing_required_attachment')
            htmlrec=next((f for f in files if f['logical_filename']==it.get('reading_file')),None)
            if it.get('reading_file'):
                if not htmlrec:fmt['errors'].append('missing_reading_candidate')
                else:
                    raw=self.read_artifact(m,htmlrec['artifact_id'])
                    if not compare_reading(raw,records,it['adapter'],it.get('title','Reading copy'),it.get('print_profile')):fmt['errors'].append('reading_content_or_table_position_mismatch')
                    p=visible(raw.decode('utf-8'))
                    images={i['src']:i for i in basis.get('images',[])}
                    if set(p.images)!=set(images):fmt['errors'].append('image_inventory_mismatch')
                    for src in p.images:
                        info=images.get(src,{})
                        if info.get('purpose')=='decorative' and info.get('exclusion_basis'):continue
                        name=info.get('source_name');source=next((a for a in m['source_files'] if self.artifact(m,a)['logical_name']==name),None)
                        if not source or info.get('sha256')!=sha(self.read_artifact(m,source)) or info.get('entity')!=it['target_context'].get('entity'):
                            fmt['errors'].append('missing_or_wrong_substantive_image');continue
                        try:
                            import io
                            from PIL import Image
                            with Image.open(io.BytesIO(self.read_artifact(m,source))) as im:
                                im.load()
                                if all(a==b for a,b in im.convert('RGB').getextrema()):fmt['errors'].append('blank_substantive_image')
                        except ImportError:fmt['errors'].append('image_checker_unavailable')
                        except Exception:fmt['errors'].append('unreadable_substantive_image')
                        candidate_image=next((f for f in m.get('candidate_files',[]) if f['logical_filename']==src),None)
                        if not candidate_image or candidate_image['sha256']!=info.get('sha256'):fmt['errors'].append('image_not_in_current_candidate_scope')
                        elif candidate_image:self.read_artifact(m,candidate_image['artifact_id'])
                    title=whitespace(''.join(p.titles)).lower()
                    if title in ('403 forbidden','access denied','sign in','login'):access='error_document';fmt['errors'].append('error_document_title')
            pdfrec=next((f for f in files if f['logical_filename']==it.get('pdf_file')),None)
            if it.get('pdf_file'):
                if not pdfrec:fmt['errors'].append('missing_pdf_candidate')
                else:
                    expected=' '.join(' '.join([r['caption']]+[c['text'] for c in r['cells']]) for r in records) if it['adapter']=='eth_table_v1' else ' '.join(v for r in records for v in r['values'])
                    prior=previous.get(it['key'],{})
                    self.read_artifact(m,pdfrec['artifact_id'])
                    if prior.get('stage_keys',{}).get('pdf_text_and_geometry')==stage_keys['pdf_text_and_geometry'] and prior.get('format',{}).get('pdf',{}).get('status')=='pass':
                        pdf=copy.deepcopy(prior['format']['pdf']);fmt['pdf_check_reused_from']=old['task_id']
                    else:pdf=pdf_check(self.read_artifact(m,pdfrec['artifact_id']),expected,it.get('pdf_options',{}))
                    fmt['pdf']=pdf
                    fmt['errors']+=pdf['errors']
                    if it.get('full_visual_required',False):
                        v=self._current_material_visual(m,pdfrec)
                        if not v and old and prior.get('stage_keys',{}).get('visual')==stage_keys['visual']:
                            oldfile=next((f for f in old.get('candidate_files',[]) if f['logical_filename']==it['pdf_file'] and f['sha256']==pdfrec['sha256']),None)
                            if oldfile:
                                v=self._current_material_visual(old,oldfile)
                                if v:fmt['visual_reused_from']=old['task_id']
                        fmt['visual']=v
                        if not v or v['scope']!='all_pages' or not v['passed'] or v['dpi']<it.get('visual_dpi',144):fmt['errors'].append('current_full_visual_missing')
            if it.get('reading_file') or it.get('pdf_file'):fmt['status']='pass' if not fmt['errors'] else 'fail'
            errors=sorted(set(errors));coverage_errors=[e for e in errors if not e.startswith('source_version_')]
            collection='full' if not coverage_errors else ('unsupported' if any('unsupported' in e for e in coverage_errors) else 'partial')
            limits=it.get('limited_delivery')
            exceptionable={'coverage_count_mismatch','coverage_id_set_mismatch','pagination_not_exhausted','missing_required_attachment','source_version_unknown'}
            bounded=bool(limits and errors and set(errors)<=exceptionable and set(errors)<=set(limits['allowed_errors']) and fmt['status']=='pass')
            passed=(not errors and fmt['status']=='pass') or bounded
            upstream=bool(old and old.get('review') and previous.get(it['key'],{}).get('stage_keys',{}).get('content')==stage_keys['content'])
            if it.get('semantic_required') and not m['routing']['review_required'] and not upstream:passed=False;errors.append('semantic_review_route_missing')
            items.append({'key':it['key'],'access':access,'collection':collection,'version':version,
                'applicability':('accepted_upstream_unchanged_scope' if upstream else 'pending_independent_review') if it.get('semantic_required') else 'not_claimed',
                'upstream_review_task':old['task_id'] if upstream else None,
                'format':fmt,'delivery':'limited_delivery_eligible_pending_acceptance' if bounded else 'pending_authoritative_acceptance',
                'scope':basis['scope'],'scope_evidence':it['basis_artifact'],'coverage':details,'errors':errors,
                'limited_delivery':limits if bounded else None,'passed':passed,
                'dependencies':{'contract':sha(self.read_artifact(m,cid)),'detector':sha(Path(__file__).read_bytes()),
                    'sources':[sha(b) for b in blobs],'basis':sha(self.read_artifact(m,it['basis_artifact'])),
                    'files':[{k:f[k] for k in ('artifact_id','sha256','logical_filename')} for f in files]},
                'records_sha256':sha(canonical(records)), 'stage_keys':stage_keys})
        return {'schema_version':VERSION,'detector_version':DETECTOR,'passed':all(x['passed'] for x in items),'items':items,
                'candidate_artifact_id':m.get('candidate'),'candidate_sha256':self.artifact(m,m['candidate'])['hash'] if m.get('candidate') else None,
                'plugin_evidence_only':True,'authority':'Current controller validation/review gates; never producer pass flags'}

    def inspect_materials(self,task):
        m=self.get(task);report=self._material_results(m)
        if m['status']=='DELIVERED' and not m.get('blocking_issue'):
            self._material_gate(m)
            for i in report['items']:
                i['delivery']='accepted_with_limits' if i['limited_delivery'] else 'accepted_current_candidate'
                if i['applicability']=='pending_independent_review' and m.get('review'):i['applicability']='accepted_review_scope_only'
        return report

    def _material_stage_keys(self,it,basis,blobs,files):
        import inspect
        content_contract={k:v for k,v in it.items() if k not in ('reading_file','pdf_file','pdf_options','print_profile','full_visual_required','source_artifacts','basis_artifact')}
        content=sha(canonical({'sources':[sha(b) for b in blobs],'basis':basis,'contract':content_contract,
              'recovery_code':inspect.getsource(unpack)+inspect.getsource(clean_markup)+inspect.getsource(whitespace)}))
        pdfs=[f['sha256'] for f in files if f['logical_filename']==it.get('pdf_file')]
        pdf=sha(canonical({'content':content,'pdfs':pdfs,'print_profile':it.get('print_profile'),
            'options':it.get('pdf_options',{}),'pdf_rule':inspect.getsource(pdf_check)}))
        return {'content':content,'pdf_text_and_geometry':pdf,'visual':sha(canonical({'pdfs':pdfs,'scope':it.get('full_visual_required'),'dpi':it.get('visual_dpi',144)})),
                'links_and_index':sha(canonical([{k:f[k] for k in ('sha256','logical_filename')} for f in files]))}

    def material_impact(self,task,previous):
        from .core import GuardError
        old=self.get(previous);new=self.get(task)
        if old['run_mode']!=new['run_mode'] or old['status']!='DELIVERED' or not self.audit(previous)['passed']:raise GuardError('Reuse requires an exact audited delivered origin')
        self._material_gate(old)
        before=self._material_results(old);after=self._material_results(new);prior={i['key']:i for i in before['items']}
        return {'schema_version':'material_impact_v1','previous_task_id':previous,'task_id':task,
            'items':[{'key':i['key'],**dependency_plan(prior.get(i['key'],{}).get('stage_keys',{}),i['stage_keys'])} for i in after['items']],
            'scope':'Reuse eligibility only; no review/acceptance transferred, no reprint performed'}

    def _material_gate(self,m):
        from .core import GuardError
        if not m.get('material_contract'):return
        current=self._material_results(m)
        vals=[v for v in m['validation'] if v['candidate_artifact_id']==m['candidate'] and v['passed']]
        if not vals:raise GuardError('No passing material validation for exact candidate')
        report=json.loads(self.read_artifact(m,vals[-1]['artifact_id']))
        if not current['passed'] or report.get('materials')!=current:raise GuardError('Material dependencies or detector changed; targeted revalidation required')

    def restore_material(self,task,key,output):
        from .core import GuardError
        m=self.get(task)
        if m['producer']!='codex' or m['status']!='RUNNING':raise GuardError('Restoration uses packaged local work lifecycle')
        c=json.loads(self.read_artifact(m,m['material_contract']));it=next(i for i in c['items'] if i['key']==key)
        basis=json.loads(self.read_artifact(m,it['basis_artifact']));blobs=[self.read_artifact(m,a) for a in it['source_artifacts']]
        records,errors,_=unpack(it['adapter'],blobs,basis)
        if not records or any('unsupported' in e for e in errors):raise GuardError('Unsupported/empty data cannot become a successful reading copy')
        p=Path(output)
        if p.exists() or p.is_symlink():raise GuardError('Derived candidate must use a new file; original preserved')
        if p.name!=it.get('reading_file'):raise GuardError('Output name differs from frozen contract')
        p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(render_reading(records,it['adapter'],it.get('title','Reading copy'),it.get('print_profile')))
        if it['adapter']=='static_html_v1':
            for im in basis.get('images',[]):
                src=im['src']
                if Path(src).name!=src:raise GuardError('Only local flat image paths supported')
                aid=next((a for a in m['source_files'] if self.artifact(m,a)['logical_name']==im.get('source_name')),None)
                if aid:
                    target=p.parent/src
                    if target.exists():raise GuardError('Never overwrite an existing image')
                    target.write_bytes(self.read_artifact(m,aid))
        return {'path':str(p.resolve()),'sha256':sha(p.read_bytes()),'source_hashes':[sha(b) for b in blobs],
                'coverage_errors':errors,'status':'candidate_not_accepted','kind':'derived_reading_copy','school_original':False}

    def convert_material_pdf(self,task,reading,output,renderer,timeout=60):
        from .core import GuardError,js
        m=self.get(task)
        if m['producer']!='codex' or m['status']!='RUNNING' or not m.get('material_contract'):raise GuardError('Conversion requires contracted local running work')
        p=Path(reading).resolve();o=Path(output).resolve();exe=Path(renderer).resolve()
        if o.exists() or (renderer!='reportlab' and not exe.is_file()):raise GuardError('New output and existing explicit renderer required')
        raw=p.read_bytes();parser=visible(raw.decode())
        contract=json.loads(self.read_artifact(m,m['material_contract']));matched=False
        for it in contract['items']:
            if it.get('reading_file')==p.name and it.get('pdf_file')==o.name:
                basis=json.loads(self.read_artifact(m,it['basis_artifact']));records,_,_=unpack(it['adapter'],[self.read_artifact(m,a) for a in it['source_artifacts']],basis)
                matched=compare_reading(raw,records,it['adapter'],it.get('title','Reading copy'),it.get('print_profile'))
        if not matched:raise GuardError('Conversion input is not the exact declared recovery candidate')
        if parser.images or re.search(rb'<(?:script|link|iframe|object|embed)\b|file://|url\s*\(',raw,re.I):raise GuardError('This renderer adapter supports inactive text/tables only; resources need another validated adapter')
        if renderer=='reportlab':
            from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib.pagesizes import A4
            from reportlab.lib import colors
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            font=Path('/System/Library/Fonts/Supplemental/Arial.ttf')
            if not font.is_file():raise GuardError('Local ReportLab adapter requires an available Arial font; no install attempted')
            pdfmetrics.registerFont(TTFont('MaterialArial',str(font)))
            profile=it.get('print_profile',{});margin=profile.get('margin_mm',16)*72/25.4
            style=getSampleStyleSheet()['Normal'];style.fontName='MaterialArial';style.fontSize=profile.get('font_pt',11);style.leading=style.fontSize*1.4
            story=[]
            if it['adapter']=='eth_table_v1':
                for record in records:
                    if record['caption']:story.extend([Paragraph(html.escape(record['caption']),style),Spacer(1,8)])
                    rows=[[Paragraph(html.escape(next(c['text'] for c in record['cells'] if c['row']==ri and c['col']==ci)),style) for ci in range(width)] for ri,width in enumerate(record['row_widths'])]
                    if len({len(row) for row in rows})!=1:raise GuardError('PDF renderer requires rectangular table')
                    table=Table(rows,colWidths=[(A4[0]-2*margin)/len(rows[0])]*len(rows[0]),repeatRows=1)
                    table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.grey),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]));story.extend([table,Spacer(1,8)])
            else:
                for record in records:
                    for value in record['values']:story.extend([Paragraph(html.escape(value),style),Spacer(1,8)])
            o.parent.mkdir(parents=True,exist_ok=True);SimpleDocTemplate(str(o),pagesize=A4,leftMargin=margin,rightMargin=margin,topMargin=margin,bottomMargin=margin).build(story)
            rec={'input_sha256':sha(raw),'output_sha256':sha(o.read_bytes()),'renderer':'existing_local_reportlab','font_path':str(font),
                 'font_sha256':sha(font.read_bytes()),'print_profile':profile,'status':'candidate_not_accepted','no_network_fetch':True}
            with self._mutate(task) as current:
                a=self._artifact(current,js(rec).encode(),'evidence','codex','pdf-conversion.json',[current['material_contract']]);self._event(current,'PDF_DERIVED','orchestrator','Local PDF candidate still requires content and visual acceptance',a['artifact_id'])
            return rec
        import tempfile
        with tempfile.TemporaryDirectory(prefix='cluster-pdf-') as profile:
            cmd=[str(exe),'--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--disable-component-update','--disable-sync',
                 '--host-resolver-rules=MAP * ~NOTFOUND','--user-data-dir='+profile,'--no-pdf-header-footer','--print-to-pdf='+str(o),p.as_uri()]
            o.parent.mkdir(parents=True,exist_ok=True)
            try:run=subprocess.run(cmd,capture_output=True,timeout=timeout)
            except subprocess.TimeoutExpired:raise GuardError('Renderer timeout; output remains unaccepted')
        rec={'input_sha256':sha(raw),'output_sha256':sha(o.read_bytes()) if o.exists() else None,'renderer':str(exe),'renderer_exit':run.returncode,
             'stdout_sha256':sha(run.stdout),'stderr_sha256':sha(run.stderr),'stderr':run.stderr.decode('utf-8',errors='replace')[:3000],'command':cmd,'status':'candidate_not_accepted','no_network_fetch':True}
        with self._mutate(task) as current:
            a=self._artifact(current,js(rec).encode(),'evidence','codex','pdf-conversion.json',[current['material_contract']]);self._event(current,'PDF_DERIVED','orchestrator','Conversion output requires text, coverage and visual checks',a['artifact_id'])
        if run.returncode!=0 or not o.is_file():raise GuardError('Renderer did not produce a successful candidate')
        return rec

    def record_material_visual(self,task,observation):
        from .core import GuardError,js
        with self._mutate(task) as m:
            if m['status']=='DELIVERED':raise GuardError('Delivered visual history is immutable')
            aid=observation.get('pdf_artifact_id');f=next((f for f in m.get('candidate_files',[]) if f['artifact_id']==aid),None)
            if not f or observation.get('pdf_sha256')!=f['sha256']:raise GuardError('Visual observation belongs to a different current PDF')
            if observation.get('observer')!='codex' or observation.get('kind') not in ('actual_page_observation','simulated_page_observation'):raise GuardError('No producer verdict may masquerade as controller visual observation')
            if observation['kind'].startswith('simulated') and self.mode!='test':raise GuardError('No simulated visual observation in live mode')
            if not observation.get('notes') or type(observation.get('passed')) is not bool or not observation.get('pages') or not observation.get('dpi'):raise GuardError('Visual record requires page scope, resolution and observed findings')
            images=observation.get('render_artifacts',[])
            if len(images)!=len(observation['pages']):raise GuardError('One exact rendered image required per inspected page')
            for rid in images:
                r=self._fresh(m,rid)
                if r.get('rendered_pdf')!=aid or r.get('render_dpi')!=observation['dpi']:raise GuardError('Rendered image is not bound to this PDF/resolution')
            if sorted(self.artifact(m,r)['render_page'] for r in images)!=sorted(observation['pages']):raise GuardError('Rendered page identities differ from inspected scope')
            a=self._artifact(m,js(observation).encode(),'evidence','codex','material-visual.json',[aid]+images)
            m.setdefault('material_visual',[]).append(a['artifact_id'])
        return a

    def _current_material_visual(self,m,f):
        for aid in reversed(m.get('material_visual',[])):
            v=json.loads(self.read_artifact(m,aid))
            if v['pdf_artifact_id']==f['artifact_id'] and v['pdf_sha256']==f['sha256']:
                # Count from current bytes, not from the observer's own total.
                import io
                from pypdf import PdfReader
                n=len(PdfReader(io.BytesIO(self.read_artifact(m,f['artifact_id']))).pages)
                if v['scope']=='all_pages' and sorted(v['pages'])!=list(range(1,n+1)):return None
                for rid in v['render_artifacts']:self._fresh(m,rid)
                return v
        return None

    def render_material_pages(self,task,pdf_artifact_id,directory,renderer,dpi=144):
        from .core import GuardError
        m=self.get(task);f=next((f for f in m.get('candidate_files',[]) if f['artifact_id']==pdf_artifact_id),None)
        if m['status']=='DELIVERED':raise GuardError('Delivered task is immutable')
        if not f:raise GuardError('Only exact frozen candidate PDF can be rendered')
        self._fresh(m,pdf_artifact_id);d=Path(directory)
        if d.exists() and any(d.iterdir()):raise GuardError('Render into a new empty directory')
        d.mkdir(parents=True,exist_ok=True);p=self.task_path(task)/self.artifact(m,pdf_artifact_id)['filename']
        run=subprocess.run([str(Path(renderer).resolve()),'-r',str(dpi),'-png',str(p),str(d/'page')],capture_output=True,timeout=60)
        if run.returncode:raise GuardError('PDF page render failed')
        paths=sorted(d.glob('page-*.png'));records=[]
        if not paths:raise GuardError('No rendered pages')
        with self._mutate(task) as current:
            for index,p in enumerate(paths,1):
                a=self._artifact(current,p.read_bytes(),'evidence','codex','material-page-'+str(index)+'.png',[pdf_artifact_id],
                    {'rendered_pdf':pdf_artifact_id,'render_dpi':dpi,'render_page':index})
                records.append({'path':str(p.resolve()),'artifact_id':a['artifact_id'],'page':index,'sha256':a['hash']})
        return records
