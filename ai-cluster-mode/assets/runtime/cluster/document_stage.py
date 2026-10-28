"""Content freeze and native-office receipts in the existing artifact ledger.

This module never drives an office application or manufactures GUI observations.
The authorized Codex controller performs native save/export and supplies evidence.
"""
import io
import json
import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def sha(value):
    return hashlib.sha256(value).hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def inspect_docx(data):
    """Visible content and relationships, deliberately excluding column widths."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = E.fromstring(z.read('word/document.xml'))
        body = root.find(W+'body')
        if body is None:
            raise ValueError('DOCX has no body')
        text = lambda el: ''.join(x.text or '' for x in el.iter(W+'t'))
        paragraphs = [text(x) for x in body if x.tag == W+'p']
        tables = []
        relations = []
        for table in body.findall(W+'tbl'):
            rows = []
            for row in table.findall(W+'tr'):
                rows.append([text(cell) for cell in row.findall(W+'tc')])
                relations.append([E.tostring(p, encoding='unicode') for cell in row.findall(W+'tc')
                                  for tag in ('gridSpan', 'vMerge') for p in cell.findall(W+'tcPr/'+W+tag)])
            tables.append(rows)
        hidden = any(x.tag in (W+'vanish', W+'webHidden') and x.get(W+'val', 'true') not in ('0','false','off') for x in root.iter())
        if hidden:
            raise ValueError('Hidden text cannot inherit semantic acceptance')
        if not paragraphs:
            raise ValueError('DOCX needs a Normal title')
        first = next(x for x in body if x.tag == W+'p')
        style = first.find(W+'pPr/'+W+'pStyle')
        if first.find(W+'pPr/'+W+'pBdr') is not None:
            raise ValueError('Title paragraph border forbidden')
        styles = E.fromstring(z.read('word/styles.xml')) if 'word/styles.xml' in z.namelist() else None
        default_styles=[x for x in styles.findall(W+'style') if x.get(W+'type')=='paragraph'
                        and x.get(W+'default') in ('1','true','on')] if styles is not None else []
        if style is None and len(default_styles)>1:raise ValueError('Ambiguous default paragraph style')
        chain = style.get(W+'val') if style is not None else default_styles[0].get(W+'styleId') if default_styles else 'Normal'
        if chain!='Normal':
            actual_style=next((x for x in styles.findall(W+'style') if x.get(W+'styleId')==chain),None) if styles is not None else None
            name=actual_style.find(W+'name') if actual_style is not None else None
            if name is None or name.get(W+'val') not in ('Normal','正文'):raise ValueError('Document title must use Normal')
        visited = set()
        while styles is not None and chain and chain not in visited:
            visited.add(chain)
            s = next((x for x in styles.findall(W+'style') if x.get(W+'styleId') == chain), None)
            if s is None:
                break
            if s.find(W+'pPr/'+W+'pBdr') is not None:
                raise ValueError('Inherited title border forbidden')
            base = s.find(W+'basedOn'); chain = base.get(W+'val') if base is not None else None
        default = styles.find(W+'docDefaults/'+W+'pPrDefault/'+W+'pPr/'+W+'pBdr') if styles is not None else None
        if default is not None:
            raise ValueError('Default inherited title border forbidden')
        extra = {}
        for n in z.namelist():
            if n.startswith(('word/footnotes', 'word/endnotes', 'word/header', 'word/footer')) and n.endswith('.xml'):
                extra[n] = text(E.fromstring(z.read(n)))
        semantic = {'paragraphs':paragraphs, 'tables':tables, 'relations':relations, 'extra':extra}
        landscape = all(int(x.get(W+'w','0')) > int(x.get(W+'h','0')) for x in root.iter(W+'pgSz'))
        headers = all(t.find(W+'tr/'+W+'trPr/'+W+'tblHeader') is not None for t in body.findall(W+'tbl'))
        return {**semantic, 'semantic_sha256':sha(packed(semantic)), 'landscape':landscape,
                'repeat_headers':headers, 'editable_table_count':len(tables)}


def check_view_docx(view, inspection, title):
    expected = [[t['columns']] + [r['cells'] for r in t['rows']] for t in view['tables']]
    if inspection['tables'] != expected:
        raise ValueError('DOCX cells drift from exact canonical view; return to master facts')
    # No additional prose may silently add a condition, credit or missing scope.
    expected_paragraphs = [title] + list(view.get('notes', []))
    actual = [x for x in inspection['paragraphs'] if x]
    if actual != expected_paragraphs or any(inspection['relations']):
        raise ValueError('DOCX conditions/group boundaries differ from canonical view')
    if any(text and not (name.startswith(('word/header','word/footer')) and text.isdigit())
           for name,text in inspection['extra'].items()):
        raise ValueError('Unmapped footnote/header/footer content requires semantic review')


def write_view_docx(view, path, title, widths=None):
    """Portable editable-table template; this is not native export or visual QA."""
    ns=W[1:-1];E.register_namespace('w',ns)
    tag=lambda name:W+name
    doc=E.Element(tag('document'));body=E.SubElement(doc,tag('body'))
    def para(parent,text,cell=False):
        p=E.SubElement(parent,tag('p'))
        if cell:
            props=E.SubElement(p,tag('pPr'))
            # OOXML wordWrap=off allows character-level Latin word breaks.
            # Do not add tcPr/noWrap, which would inhibit normal cell wrapping.
            # https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.wordwrap
            E.SubElement(props,tag('wordWrap'),{tag('val'):'off'})
        r=E.SubElement(p,tag('r'))
        t=E.SubElement(r,tag('t'));t.text=str(text)
        return p
    para(body,title)
    for table_index,table in enumerate(view['tables']):
        # Native Word merges directly adjacent tables into a single table. A
        # visible ordinary empty paragraph preserves distinct canonical groups.
        if table_index:para(body,'')
        t=E.SubElement(body,tag('tbl'));pr=E.SubElement(t,tag('tblPr'))
        E.SubElement(pr,tag('tblW'),{tag('w'):'0',tag('type'):'auto'})
        borders=E.SubElement(pr,tag('tblBorders'))
        for side in ('top','left','bottom','right','insideH','insideV'):
            E.SubElement(borders,tag(side),{tag('val'):'single',tag('sz'):'4',tag('color'):'808080'})
        E.SubElement(pr,tag('tblLayout'),{tag('type'):'fixed'})
        n=len(table['columns']);ws=(widths or {}).get(table['id']) or [int(14800/n)]*n
        if len(ws)!=n or any(type(v) is not int or v<=0 for v in ws):raise ValueError('Invalid column widths')
        grid=E.SubElement(t,tag('tblGrid'))
        for width in ws:E.SubElement(grid,tag('gridCol'),{tag('w'):str(width)})
        for index,cells in enumerate([table['columns']]+[r['cells'] for r in table['rows']]):
            row=E.SubElement(t,tag('tr'));rp=E.SubElement(row,tag('trPr'))
            if index==0:E.SubElement(rp,tag('tblHeader'))
            E.SubElement(rp,tag('cantSplit'))
            for text,width in zip(cells,ws):
                cell=E.SubElement(row,tag('tc'));cp=E.SubElement(cell,tag('tcPr'))
                E.SubElement(cp,tag('tcW'),{tag('w'):str(width),tag('type'):'dxa'})
                para(cell,text,cell=True)
    for note in view.get('notes',[]):para(body,note)
    section=E.SubElement(body,tag('sectPr'))
    E.SubElement(section,tag('pgSz'),{tag('w'):'16838',tag('h'):'11906',tag('orient'):'landscape'})
    E.SubElement(section,tag('pgMar'),{tag('top'):'720',tag('right'):'720',tag('bottom'):'720',tag('left'):'720'})
    styles=E.Element(tag('styles'));style=E.SubElement(styles,tag('style'),{tag('type'):'paragraph',tag('default'):'1',tag('styleId'):'Normal'})
    E.SubElement(style,tag('name'),{tag('val'):'Normal'});rp=E.SubElement(style,tag('rPr'))
    E.SubElement(rp,tag('rFonts'),{tag('ascii'):'Arial',tag('hAnsi'):'Arial',tag('eastAsia'):'宋体'})
    E.SubElement(rp,tag('sz'),{tag('val'):'18'})
    types='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    rels='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    docrels='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in {'[Content_Types].xml':types,'_rels/.rels':rels,'word/document.xml':E.tostring(doc,encoding='utf-8',xml_declaration=True),'word/styles.xml':E.tostring(styles,encoding='utf-8',xml_declaration=True),'word/_rels/document.xml.rels':docrels}.items():z.writestr(name,data)
    check_view_docx(view,inspect_docx(path.read_bytes()),title)
    return path


class DocumentStageMixin:
    def _document_mutable(self,m):
        from .core import GuardError
        if m['status']=='DELIVERED':raise GuardError('Delivered task is immutable; create authorized followup')

    def _document_enabled(self, m):
        return m['spec'].get('native_document_workflow_v1') is True

    def _document_view(self, m, name):
        self._canonical_gate(m)
        record = self.inspect_canonical(m['task_id'])['views'][name]
        return self._fresh(m, record['artifact_id']), json.loads(self.read_artifact(m, record['artifact_id']))

    def freeze_content(self, task):
        from .core import GuardError, js, now
        with self._mutate(task) as m:
            self._document_mutable(m)
            if not self._document_enabled(m):
                raise GuardError('Native document workflow not enabled')
            self._canonical_gate(m)
            self._canonical_review_binding_gate(m)
            self._review_cycle_gate(m)
            if not m.get('review_cycle'):
                raise GuardError('Content freeze needs an independent business review cycle')
            if any(x['status']=='open' and x['blocking'] for x in m['open_issues']):
                raise GuardError('Open school issues block content freeze')
            views = {n:self._document_view(m,n)[0] for n in ('full','simple')}
            receipt = {'views':{n:{'artifact_id':a['artifact_id'],'sha256':a['hash']} for n,a in views.items()},
                       'frozen_at':now(), 'review_cycle':self.review_cycle_status(task)}
            a=self._artifact(m,js(receipt).encode(),'evidence','codex','content-freeze.json',[x['artifact_id'] for x in views.values()])
            m['content_freeze']={'artifact_id':a['artifact_id'],**receipt}
            self._event(m,'CONTENT_FROZEN','orchestrator','Canonical content independently accepted; native office stage may start',a['artifact_id'])
        return receipt

    def _document_content_gate(self,m):
        from .core import GuardError
        receipt=m.get('content_freeze')
        if not receipt:
            raise GuardError('Content must freeze before final native export')
        self._fresh(m,receipt['artifact_id'])
        self._review_cycle_gate(m)
        for n,r in receipt['views'].items():
            if self._document_view(m,n)[0]['hash'] != r['sha256']:
                raise GuardError('Canonical view changed after content freeze')

    def request_document_export(self,task,request):
        from .core import GuardError, now
        allowed={'key','view','purpose','title'}
        if set(request)-allowed or request.get('purpose') not in ('template_trial','final') or request.get('view') not in ('full','simple'):
            raise GuardError('Invalid document export request')
        if not request.get('key') or not isinstance(request.get('title'),str) or not request['title'].strip():
            raise GuardError('Document export needs key and plain title')
        with self._mutate(task) as m:
            self._document_mutable(m)
            if not self._document_enabled(m):raise GuardError('Native document workflow not enabled')
            if request['purpose']=='final':self._document_content_gate(m)
            elif any(x['purpose']=='template_trial' for x in m.get('document_exports',[])):
                raise GuardError('Only one pre-freeze template trial per business unit')
            a,view=self._document_view(m,request['view'])
            rec={**request,'request_id':m['task_id']+'-DOC-'+str(len(m.get('document_exports',[]))+1),
                 'view_artifact':a['artifact_id'],'view_sha256':a['hash'],'requested_at':now(),'native_export_succeeded':False}
            m.setdefault('document_exports',[]).append(rec)
            self._event(m,'NATIVE_EXPORT_REQUESTED','orchestrator','Native GUI action required; request is not a success',extra=rec)
        return rec

    def register_document(self,task,request):
        from .core import GuardError, js, now
        if set(request) != {'request_id','docx_path'}:raise GuardError('Invalid document registration')
        with self._mutate(task) as m:
            self._document_mutable(m)
            self._document_content_gate(m)
            export=next((x for x in m.get('document_exports',[]) if x['request_id']==request['request_id'] and x['purpose']=='final'),None)
            if not export:raise GuardError('Unknown final export request')
            path=Path(request['docx_path'])
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self._output_directory(task)):
                raise GuardError('DOCX must be in this task output directory')
            viewa,view=self._document_view(m,export['view'])
            if viewa['hash']!=export['view_sha256']:raise GuardError('Export request view is stale')
            data=path.read_bytes();inspection=inspect_docx(data);check_view_docx(view,inspection,export['title'])
            if not inspection['landscape'] or not inspection['repeat_headers']:raise GuardError('Landscape editable tables with repeated headers required')
            a=self._artifact(m,data,'evidence','codex','native-'+export['key']+'.docx',[viewa['artifact_id'],m['content_freeze']['artifact_id']])
            prior=m.setdefault('native_documents',{}).get(export['key'])
            rec={**export,'docx_path':str(path),'docx_artifact':a['artifact_id'],'docx_sha256':a['hash'],
                 'semantic_sha256':inspection['semantic_sha256'],'registered_at':now(),'visual_status':'not_verified',
                 'inherited_semantics':bool(prior and prior['semantic_sha256']==inspection['semantic_sha256'])}
            m['native_documents'][export['key']]=rec
            self._event(m,'NATIVE_DOCUMENT_REGISTERED','orchestrator','Current Word bytes bound; native export and all pages still pending',a['artifact_id'])
        return rec

    def record_native_document_qa(self,task,receipt):
        from .core import GuardError, js, now
        required={'key','application','actor','kind','docx_sha256','pdf_path','native_evidence','pages'}
        if set(receipt)!=required or receipt.get('application') not in ('Word','WPS') or receipt.get('actor')!='codex':raise GuardError('Native QA belongs to Codex in existing Word/WPS')
        if receipt['kind']!=('simulated' if self.mode=='test' else 'observed_gui'):raise GuardError('Simulation cannot enter live native QA')
        with self._mutate(task) as m:
            self._document_mutable(m)
            self._document_content_gate(m)
            rec=m.get('native_documents',{}).get(receipt['key'])
            if not rec or rec['docx_sha256']!=receipt['docx_sha256']:raise GuardError('QA belongs to another DOCX')
            self._fresh(m,rec['docx_artifact'])
            if sha(Path(rec['docx_path']).read_bytes())!=rec['docx_sha256']:raise GuardError('Current DOCX changed after registration')
            pdf=Path(receipt['pdf_path'])
            if pdf.is_symlink() or not pdf.is_file() or not pdf.resolve().is_relative_to(self._output_directory(task)):raise GuardError('PDF outside exact task output')
            from pypdf import PdfReader
            page_count=len(PdfReader(io.BytesIO(pdf.read_bytes())).pages)
            pdf_sha=sha(pdf.read_bytes())
            pages=receipt['pages']
            checks={'fonts','widths','clipping','blank_pages','page_numbers','footnotes','group_boundaries'}
            if [p.get('page') for p in pages]!=list(range(1,page_count+1)) or not page_count:raise GuardError('Every final PDF page must be inspected')
            inputs=[rec['docx_artifact']]
            for evidence in [receipt['native_evidence']]+[p.get('evidence_artifact') for p in pages]:
                if not evidence:raise GuardError('Missing native/page observation evidence')
                a=self._fresh(m,evidence)
                if a['role']!='evidence':raise GuardError('Native/page observations must be retained as evidence')
                inputs.append(evidence)
            for p in pages:
                if set(p.get('checks',{}))!=checks or any(v is not True for v in p['checks'].values()):raise GuardError('Page QA incomplete or failed')
            observation=json.loads(self.read_artifact(m,receipt['native_evidence']))
            if (observation.get('kind')!=receipt['kind'] or observation.get('actor')!='codex'
                    or observation.get('application')!=receipt['application']
                    or observation.get('docx_sha256')!=rec['docx_sha256'] or observation.get('pdf_sha256')!=pdf_sha
                    or observation.get('native_save_and_export_observed') is not True or not observation.get('observed_at')
                    or not observation.get('raw_ui_text')):
                raise GuardError('Native save/export observation missing or stale')
            for p in pages:
                seen=json.loads(self.read_artifact(m,p['evidence_artifact']))
                if (seen.get('kind')!=receipt['kind'] or seen.get('actor')!='codex'
                        or seen.get('page')!=p['page'] or seen.get('pdf_sha256')!=pdf_sha
                        or not seen.get('observed_at') or not seen.get('visual_observation')):
                    raise GuardError('Final-page observation does not bind current PDF page')
            pdfa=self._artifact(m,pdf.read_bytes(),'evidence','codex','native-'+receipt['key']+'.pdf',[rec['docx_artifact']])
            a=self._artifact(m,js({**receipt,'pdf_sha256':pdfa['hash'],'page_count':page_count}).encode(),'evidence','codex','native-qa-'+receipt['key']+'.json',inputs+[pdfa['artifact_id']])
            rec.update(native_export_succeeded=True,visual_status='all_final_pages_observed',qa_artifact=a['artifact_id'],pdf_artifact=pdfa['artifact_id'],pdf_sha256=pdfa['hash'],pdf_path=str(pdf),page_count=page_count,verified_at=now())
            export=next(x for x in m['document_exports'] if x['request_id']==rec['request_id']);export['native_export_succeeded']=True
            self._event(m,'NATIVE_DOCUMENT_QA','orchestrator','Actual current DOCX/PDF and all final pages bound',a['artifact_id'])
        return rec

    def _document_stage_gate(self,m):
        from .core import GuardError
        if not self._document_enabled(m):return
        self._document_content_gate(m)
        docs=m.get('native_documents',{})
        if {x['view'] for x in docs.values()}!={'full','simple'}:raise GuardError('Both canonical document views required')
        for rec in docs.values():
            if rec['visual_status']!='all_final_pages_observed':raise GuardError('Native final pages not verified')
            for name in ('docx_artifact','pdf_artifact','qa_artifact'):self._fresh(m,rec[name])
            if sha(Path(rec['docx_path']).read_bytes())!=rec['docx_sha256'] or sha(Path(rec['pdf_path']).read_bytes())!=rec['pdf_sha256']:raise GuardError('Current native file changed; recheck affected document')

    def document_stage_status(self,task):
        m=self.get(task)
        return {'content_frozen':bool(m.get('content_freeze')),'exports':m.get('document_exports',[]),'documents':m.get('native_documents',{}),'libreoffice_used':False}
