"""Production routing for source-bound drawing questions, before text counters."""
import json
import re
from .drawings import Drawings
from .operations import previous_reply
from .claim_gate import escape


def intent(question):
    q=question.lower()
    if re.search(r'\b(height|width|diameter|dimensions?|distance between)\b',q):return 'dimension'
    if re.search(r'\b(rev\.?|revision|drawing number|drawing title|project title)\b',q):return 'metadata'
    if re.search(r'\b(symbol|valves?|transmitters?|instrument|p&id|drawing|diagram|figure|vessel|pump|tank|pipeline)\b',q):
        if re.search(r'\b(count|how many|occurrences|appear)\b',q):return 'symbols'
        if re.search(r'\b(height|width|length|diameter|dimension|distance|measure)\b',q):return 'dimension'
        if re.search(r'\b(connect|connects|connected|trace|upstream|downstream|discharge|branches|flow path)\b',q):return 'connectivity'
        return 'inspect'
    return None


def metadata_answer(data,question):
    patterns={'drawing_number':r'drawing\s+(?:no\.?|number)',
              'project_title':r'project(?:\s+title)?','drawing_title':r'drawing\s+title',
              'revision':r'\b(?:rev\.?|revision)\b'}
    requested=[key for key,pattern in patterns.items() if re.search(pattern,question,re.I)] or ['revision']
    answers=[];regions=[];results={}
    for field in requested:
        candidates=[f for f in data['fields'] if f['key']==field]
        observed=[f for f in candidates if f['value'] is not None and f['status']=='observed']
        if observed and len({f['value'] for f in observed})==1:
            value=observed[0]['value']
            answer=f"{field.replace('_',' ').capitalize()}: **{escape(value)}**."
            if len(requested)==1 and re.search(r'what does\s+\w+\s+mean',question,re.I):answer=f"**{escape(value)}** is the {field.replace('_',' ')} shown in this drawing's title block."
            regions.extend(observed+[region for finding in observed for region in finding.get('corroborating_regions',[])])
        else:
            value=None
            answer=f"The {field.replace('_',' ')} is unresolved. The native title-block and revision-table evidence does not identify one consistent, structurally supported value."
        answers.append(answer);results[field]={'value':value,'fields':candidates}
    details={'field_results':results}
    if len(requested)==1:details.update(field=requested[0],**results[requested[0]])
    return '\n\n'.join(answers),regions,details


async def prepare(docs,job,question,files,_kind=None):
    kind=_kind or intent(question);pdfs=[f for f in files if f['mime_type']=='application/pdf']
    if not pdfs:return None
    if _kind is None:
        clean=question.casefold()
        for file in files:clean=clean.replace(file['original_filename'].casefold(),'')
        requested=[]
        if re.search(r'\b(rev|revision|drawing number|drawing title|project title)\b',clean):requested.append('metadata')
        if re.search(r'\b(count|how many|occurrences)\b',clean) and re.search(r'\b(symbols?|valves?|transmitters?|instruments?)\b',clean):requested.append('symbols')
        if re.search(r'\b(height|width|length|diameter|dimensions?|distance)\b',clean):requested.append('dimension')
        if re.search(r'\b(connects?|connected|trace|upstream|downstream|discharge|flow path)\b',clean):requested.append('connectivity')
        if len(requested)>1:
            parts=[await prepare(docs,job,question,files,k) for k in requested]
            tools=[tool for part in parts for tool in part['tools']]
            versions={}
            for tool in tools:
                if tool['file_id'] in versions and versions[tool['file_id']]!=tool['drawing_revision']:
                    from .evidence_release import SourceInvalidated
                    raise SourceInvalidated()
                versions[tool['file_id']]=tool['drawing_revision']
            Drawings(docs).check(job['conversation_id'],versions);job['drawing_versions']=versions
            return {'sources':[],'tools':tools,'direct':'\n\n'.join(p['direct'] for p in parts),'instructions':'',
                'debug':{'route':'DRAWING_MIXED','requested_operations':requested,'drawing_context':parts[-1]['debug'].get('drawing_context'),
                    'claim_gate':{'before_release':True,'method':'deterministic_mixed_drawing_tools'}}}
    previous=previous_reply(docs,job)
    context=json.loads(previous['details']).get('drawing_context') if previous and previous.get('details') else None
    if not kind and context and re.search(r'\b(what does|what about|same|that|its)\b',question,re.I):kind='metadata'
    if not kind:return None
    named=[f for f in pdfs if f['original_filename'].casefold() in question.casefold()]
    selected=named
    if not selected and context:
        selected=[f for f in pdfs if f['id'] in context['files']]
        if any(f['sha256']!=context['files'][f['id']] for f in selected):
            raise ValueError('The earlier drawing changed. Identify the current drawing and operation.')
    selected=selected or pdfs
    if len(selected)>1 and not re.search(r'\b(all|across|each|these|drawings|pdfs)\b',question,re.I):
        return {'sources':[],'tools':[],'direct':'Which selected drawing should I inspect? '+', '.join(escape(f['original_filename']) for f in selected),
            'instructions':'','debug':{'route':'DRAWING_AMBIGUITY'}}
    clean=question
    for f in files:clean=clean.replace(f['original_filename'],'')
    page_match=re.search(r'\bpage\s+(\d+)\b',clean,re.I)
    page=int(page_match[1]) if page_match else (context.get('page',1) if context and not named else 1)
    service=Drawings(docs);answers=[];tools=[]
    for file in selected:
        job['queue'].put_nowait({'type':'status','message':'Inspecting native drawing geometry and title-block evidence...'})
        data=await service.analyze(job['conversation_id'],file['id'],page)
        evidence_revision=service.revision(job['conversation_id'],file['id'])
        from .drawing_annotations import latest,trace_reviewed
        corrections=latest(service,job['conversation_id'],file['id'])
        if corrections:
            corrections={**corrections,'objects':[obj for obj in corrections['objects'] if obj['page']==page]}
            data['dimensions']+= [{'id':d['id'],'page':d['page'],'bbox':d['bbox'],'label':d['label'].lower(),'object_id':d['object_id'],'value_text':d['value'],'unit':d['unit'],'status':'observed','origin':'human_review_attestation','verification_id':corrections['id']} for d in corrections['objects'] if d['kind']=='dimension']
        location=f"{file['original_filename']} · PDF page {page}"
        regions=[];result={'page':page,'analysis_id':data['id'],'geometry_processed':True,'coverage':data['coverage']}
        if kind=='metadata':
            answer,regions,fields=metadata_answer(data,question);result.update(fields)
        elif kind=='symbols':
            templates=service.templates(job['conversation_id'],file['id'])
            matching=[key for key,t in templates.items() if any(alias in question.casefold() for alias in [t['name'].casefold(),*[a.casefold() for a in t.get('aliases',[])]])]
            if len(matching)!=1:
                answer='**Unverified:** select a symbol reference or define a supported template in the drawing viewer. No symbol total has been established.'
                result.update(label='Unverified',count=None)
            else:
                pages=list(range(1,data['page_count']+1)) if re.search(r'\b(all pages|across)\b',question,re.I) else [page]
                counted=await service.count(job['conversation_id'],file['id'],pages,matching[0])
                answer=f"**{counted['label']}: {counted['count']}** matches for {escape(templates[matching[0]]['name'])} in PDF page(s) {', '.join(map(str,pages))}."
                if counted['label']!='Exact':answer+=' This is not an independently verified total; missed or misclassified symbols remain possible.'
                result.update(counted);regions=counted['detections']
        elif kind=='dimension':
            result.update(dimensions=data['dimensions'],not_to_scale=data['not_to_scale']);regions=data['dimensions']
            dimensions=[d for d in data['dimensions'] if d.get('object_id') and d['object_id'].casefold() in question.casefold()
                        and d['label'] in question.casefold() and d['status']=='observed']
            if dimensions and len({(d['value_text'],d['unit']) for d in dimensions})==1:
                d=dimensions[0];answer=f"The explicitly stated {escape(d['label'])} of {escape(d['object_id'])} is **{escape(d['value_text'])} {escape(d['unit'])}**."
                if d.get('origin')=='human_review_attestation':answer+=' This is a published human-reviewed annotation; see the linked reviewer record.'
                target=re.search(r'\bin\s+(mm|cm|m|in|ft)\b',question,re.I)
                if target and target[1].lower()!=d['unit']:
                    from .drawing_validation import convert
                    converted=convert(d['value_text'],d['unit'],target[1].lower())
                    answer+=f" Converted: **{converted} {target[1].lower()}**."
                    result['calculation']={'value':converted,'unit':target[1].lower(),'source_id':d['id']}
            else:answer='A dimension for the requested object has not been established from an unambiguous associated specification or dimension line.'
            if data['not_to_scale']:answer+=' This drawing is marked NTS (not to scale), so image distances cannot establish physical dimensions.'
            if data['dimensions'] and not dimensions:answer+=' Native dimension annotations are available in the evidence viewer; select a supported equipment label and dimension.'
        elif kind=='connectivity':
            reviewed=trace_reviewed(corrections,question)
            result.update(graph=data['graph'],reviewed_path=reviewed)
            if reviewed and reviewed['status']=='human_reviewed_path':
                names=[e['label'] for e in reviewed['edges']]
                answer=f"The published human review connects **{escape(reviewed['start'])}** to **{escape(reviewed['end'])}** through "+', '.join(escape(n) for n in names)+'.'
                answer+=' '+reviewed['limitation']
                if reviewed['direction']!='supported':answer+=' Flow direction is not established along the entire path.'
                regions=reviewed['edges']
            else:answer='The native linework was inspected, but the requested engineering connection is unverified. Review and publish the relevant objects and process connections in the drawing viewer. Unresolved crossings are not treated as junctions, and flow direction is not assumed.'
        else:
            observed=[f for f in data['fields'] if f['value'] is not None]
            answer='\n'.join(f"{f['key'].replace('_',' ').capitalize()}: **{escape(f['value'])}**." for f in observed)
            answer+='\n\nNative text and vector geometry were processed. General image interpretation, raster-only text recognition, and unspecified engineering classifications are unavailable in model-free mode.'
            result['fields']=observed;regions=observed
        evidence_revision=result.get('analysis_revision',evidence_revision)
        service.check(job['conversation_id'],{file['id']:evidence_revision})
        answers.append(answer+'\n\nSource: '+escape(location)+'. Open the drawing evidence to inspect the original region.')
        tools.append({'operation':'drawing_'+kind,'file_id':file['id'],'files':[file['id']],'filename':file['original_filename'],
            'fingerprint':file['sha256'],'arguments':{'question':question,'page':page},'result':result,
            'source_regions':[{'page':r.get('page',page),'bbox':r['bbox'],'label':r.get('class',r.get('key',r.get('label','evidence')))} for r in regions],
            'drawing_revision':evidence_revision})
    job['drawing_versions']={t['file_id']:t['drawing_revision'] for t in tools}
    service.check(job['conversation_id'],job['drawing_versions'])
    return {'sources':[],'tools':tools,'direct':'\n\n'.join(answers),'instructions':'',
        'debug':{'route':'DRAWING_'+kind.upper(),'original_query':question,
            'drawing_context':{'files':{f['id']:f['sha256'] for f in selected},'page':page,'kind':kind},
            'claim_gate':{'before_release':True,'method':'deterministic_drawing_tool','independent_semantic_verification':False}}}
