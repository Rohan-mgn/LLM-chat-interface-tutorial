"""Deterministic research coverage and ranking; no additional model or index."""
import json
import re
from decimal import Decimal
from .evidence import terms, signals

VERSION='research-v1'


def questions(question):
    parts=re.split(r'\?\s+|;\s*|\b(?:and also|as well as)\b',question,flags=re.I)
    return [p.strip(' ?;') for p in parts if p.strip(' ?;')] or [question]


def coverage(question, sources, file_ids):
    entries=[]
    for index,subquestion in enumerate(questions(question)):
        wanted=set(terms(subquestion))
        supporting=[s['id'] for s in sources if len(wanted & set(terms(s['text'])))>=min(2,len(wanted)) and wanted] if index<4 else []
        entries.append({'question':subquestion,'source_ids':supporting,
            'status':'budget_unprocessed' if index>=4 else 'candidate_evidence' if supporting else 'unanswered',
            'semantic_completeness_verified':False})
    return {'subquestions':entries,'missing_files':sorted(set(file_ids)-{s['file_id'] for s in sources}),
        'complete':all(e['source_ids'] for e in entries) and not(set(file_ids)-{s['file_id'] for s in sources}),
        'subquestion_budget':4,'partial':any(not e['source_ids'] for e in entries) or bool(set(file_ids)-{s['file_id'] for s in sources}),
        'policy':'Lexical acquisition coverage only; not final-answer entailment.'}


def rank(query, candidates):
    ranked=[]
    for row in candidates:
        metadata=row.get('metadata',{})
        if isinstance(metadata,str):metadata=json.loads(metadata)
        match=signals(query,row['text'])
        heading=str(metadata.get('heading') or metadata.get('section') or '')
        features={'lexical_overlap':match['lexical_overlap'],'identifier_match':int(match['identifier_match']),
            'heading_overlap':len(set(terms(query)) & set(terms(heading))),
            'structured':int(metadata.get('type') in ('row','field','table_text','dimension')),
            'fusion':row.get('score',0)}
        score=3*features['identifier_match']+2*features['lexical_overlap']+.2*min(features['heading_overlap'],3)+.1*features['structured']+features['fusion']
        ranked.append({**row,**match,'ranking_features':features,'heuristic_score':score})
    return sorted(ranked,key=lambda r:(-r['heuristic_score'],r['id']))


def field_comparison(value):
    """Normalize only explicit, unambiguous scalar units; preserve identifiers."""
    units={'mm':('length','0.001'),'cm':('length','0.01'),'m':('length','1'),
           'in':('length','0.0254'),'ft':('length','0.3048'),
           'g':('mass','0.001'),'kg':('mass','1'),
           's':('time','1'),'min':('time','60'),'h':('time','3600')}
    match=re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*(mm|cm|m|in|ft|g|kg|s|min|h)',value.strip())
    if not match:return ('source_string',value.strip())
    dimension,factor=units[match[2]]
    return (dimension,Decimal(match[1])*Decimal(factor))


def conflicts(sources):
    fields={}
    for source in sources:
        for line in source['text'].splitlines():
            match=re.fullmatch(r'\s*([A-Za-z][A-Za-z /_-]{1,45})\s*:\s*([^:]{1,100})\s*',line)
            if match:fields.setdefault(match[1].casefold(),[]).append({'value':match[2].strip(),'source_id':source['id'],'file_id':source['file_id']})
    result=[]
    for key,values in fields.items():
        comparable={field_comparison(v['value']) for v in values}
        if len(comparable)<=1:continue
        dimensions={value[0] for value in comparable}-{'source_string'}
        result.append({'field':key,'values':values,
            'status':'incompatible_unit_dimensions' if len(dimensions)>1 else 'competing_values_not_resolved',
            'limitation':'Matching field labels do not establish the same object, revision or applicability.'})
    return result
