"""Benchmark records and conservative aggregation. Missing evidence stays missing."""
import json
from pathlib import Path
from .core import atomic, js, digest

ARMS={'A':'Codex','B':'Codex + Producer','C':'Codex + Reviewer','D':'Codex + Producer + Reviewer','E':'Codex + Specialist'}
METRICS=['accuracy','critical_errors','omissions','unsupported_claims','human_edits','elapsed_seconds','gui_active_seconds','model_wait_seconds','external_calls_confirmed','retries','failures','quota_cost']

def result_template(case,arm):
    if arm not in ARMS:raise ValueError('Unknown benchmark arm')
    return {'case_id':case['case_id'],'arm':arm,'task_id':None,'status':'not_run','origin':case['origin'],
            'source_hash':digest(js(case).encode()),'metrics':{k:None for k in METRICS},'evidence':[],
            'adjudicator':None,'notes':['Synthetic fixtures are not real de-identified production tasks.']}

def aggregate(records):
    """Only report measured records, never fabricate an optimal route from placeholders."""
    result={'completed':0,'not_run':0,'by_arm':{},'routing_recommendations':[],'status':'insufficient_evidence'}
    for r in records:
        if r['status']!='completed':result['not_run']+=1;continue
        if not r.get('task_id') or not r.get('evidence') or not r.get('adjudicator'):raise ValueError('Completed benchmark requires task, evidence and adjudication')
        result['completed']+=1
        result['by_arm'].setdefault(r['arm'],[]).append(r)
    for arm,rows in list(result['by_arm'].items()):
        summary={'n':len(rows),'real_deidentified':sum(r['origin']=='real_deidentified' for r in rows),'means':{}}
        for k in METRICS:
            nums=[r['metrics'][k] for r in rows if isinstance(r['metrics'].get(k),(int,float))]
            summary['means'][k]=sum(nums)/len(nums) if nums else None
        result['by_arm'][arm]=summary
    # Route changes require a reviewed comparison, not automatic extrapolation from a tiny sample.
    return result

def score_faults(truth,review,mapping):
    """Explicit adjudication mapping: each confirmed issue -> truth ID, false_positive, or duplicate:<ID>."""
    expected={x['error_id'] for x in truth['errors']}
    findings=review['confirmed_errors'];ids={x['id'] for x in findings}
    if set(mapping)!=ids:raise ValueError('Every confirmed finding needs explicit adjudication')
    found=set();fp=[];duplicates=[]
    for issue in findings:
        target=mapping[issue['id']]
        if target=='false_positive':fp.append(issue['id'])
        elif target.startswith('duplicate:'):
            if target.split(':',1)[1] not in expected:raise ValueError('Unknown duplicate target')
            duplicates.append(issue['id'])
        elif target in expected:
            if target in found:raise ValueError('Mark duplicate reports explicitly')
            found.add(target)
        else:raise ValueError('Unknown planted error')
    denominator=len(found)+len(fp)
    return {'planted_errors':len(expected),'true_positive':len(found),'false_positive':len(fp),
            'missed':sorted(expected-found),'recall':len(found)/len(expected) if expected else None,
            'precision':len(found)/denominator if denominator else None,'raw_confirmed_findings':len(findings),
            'duplicate_findings':duplicates,'unverified':review['unverified'],
            'unit':'Predefined fault units, explicitly adjudicated; duplicate reports counted once',
            'scope':'One fixture batch, not general reviewer accuracy'}

def make_fixtures(root):
    root=Path(root);(root/'cases').mkdir(parents=True,exist_ok=True);(root/'private').mkdir(exist_ok=True)
    catalog=[];truths=[]
    categories=['roster','rules','document','multi_file','analysis']
    for cat in categories:
        for n in range(1,5):
            ident=f'{cat.upper()}-{n:02d}'
            if cat=='roster':
                original={'rows':[{'id':'001','score':60+n},{'id':'002','score':59-n},{'id':'003','score':60+n}]}
                objective='按学号字符串，筛选成绩不低于60的记录；保留同分者；给出数量。'
                expected={'eligible':['001','003'],'count':2}
            elif cat=='rules':
                original={'rule':f'积分达到{n*10}且材料齐全才通过','records':[{'id':'A','points':n*10,'complete':True},{'id':'B','points':n*10+1,'complete':False},{'id':'C','points':n*10-1,'complete':True}]}
                objective='逐项判断资格并引用两个必要条件，不自行增加规则。';expected={'A':'pass','B':'pending','C':'fail'}
            elif cat=='document':
                original={'audience':'已确认参会人员','deadline':f'2026-10-{n+10:02d} 18:00','action':'在示例平台确认','pending':'材料待核实者暂缓','limit':100}
                objective='写不超过100字的通知，保留对象、截止、行动方式和暂缓限制，不增加联系方式。'
                expected={'rubric':['audience','deadline','action','pending','<=100 characters','no invented contacts']}
            elif cat=='multi_file':
                original={'roster':[{'id':'001','name':'甲'},{'id':'002','name':'乙'}],'results':[{'id':'002','amount':n*3},{'id':'001','amount':n*2}]}
                objective='按id跨表匹配，保留名册顺序并计算总数值。';expected={'rows':[['001','甲',n*2],['002','乙',n*3]],'sum':n*5}
            else:
                original={'claims':[{'source':'S1','value':n*10,'date':'2026-09-01'},{'source':'S2','value':n*10+2,'date':'2026-09-20'}],'question':'不同日期数据能否直接推断增长原因？'}
                objective='比较数值差异、标注来源和日期，区分观察与因果推断。';expected={'difference':2,'causal_claim_allowed':False,'required_sources':['S1','S2']}
            case={'case_id':ident,'category':cat,'origin':'synthetic_realistic','objective':objective,'source':original,'allowed_arms':['A','B','C','D'],'reference_kind':'rubric' if cat=='document' else 'structured','status':'ready_not_run'}
            catalog.append(case);truths.append({'case_id':ident,'expected':expected})
            atomic(root/'cases'/f'{ident}.json',js(case).encode())
    atomic(root/'catalog.json',js(catalog).encode());atomic(root/'private/references.json',js(truths).encode())
    records=[result_template(case,arm) for case in catalog for arm in case['allowed_arms']]
    atomic(root/'results_template.json',js(records).encode());atomic(root/'summary.json',js(aggregate(records)).encode())
    sources=[];drafts=[];errors=[]
    faults=[
        ('遗漏','原始名单为001、002，必须逐人保留。','名单：001。','F01'),
        ('多出记录','原始名单只有001、002，禁止新增。','名单：001、002、003。','F02'),
        ('编号重复','原始名单为001、002，每个编号出现一次。','名单：001、002、002。','F03'),
        ('错误分类','003的场地未确定，须归入待确认，不得进入可执行名单。','可执行名单：003。','F04'),
        ('规则反向','成绩达到60分即通过；004成绩80分。','004未通过，理由：80分高于60分。','F05'),
        ('行错配','005甲成绩80，006乙成绩90；按编号匹配。','005甲90；006乙80。','F06'),
        ('算术错误','金额分别为12和18，求和。','合计40。','F07'),
        ('日期错误','截止时间为2026-10-05 18:00。','截止时间为2026-10-06 18:00。','F08'),
        ('格式遗漏','必须包含“结果”和“待确认”两个章节。','结果：已整理完成。','F09'),
        ('无依据事实','材料仅写会议线上举行，没有提供录制、回放安排；不得扩展事实。','会议线上举行，次日会提供录像回放。','F10'),
    ]
    for kind,source,draft,ident in faults:
        sources.append({'case_id':ident,'source_and_rule':source});drafts.append({'case_id':ident,'draft':draft})
        errors.append({'error_id':ident,'kind':kind,'original':source,'wrong':draft})
    sources.extend([{'case_id':'C01','source_and_rule':'金额2和3求和。'},{'case_id':'C02','source_and_rule':'截止2026-10-07 18:00，只通知已确认人员。'}])
    drafts.extend([{'case_id':'C01','draft':'合计5。'},{'case_id':'C02','draft':'请已确认人员于2026-10-07 18:00前完成确认。'}])
    atomic(root/'fault_sources.json',js({'cases':sources}).encode())
    atomic(root/'fault_candidate_template.json',js({'task_id':'REPLACE','drafts':drafts}).encode())
    atomic(root/'private/fault_truth.json',js({'errors':errors,'clean_controls':['C01','C02'],'definition':'Each F case is one predeclared fault unit; swapped row pair is one unit.'}).encode())
    return {'cases':len(catalog),'planned_comparisons':len(records),'planted_errors':len(errors),'clean_controls':2}
