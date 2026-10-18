"""Explicit, unit-bearing native dimensions; never derive P&ID scale from pixels."""
import re
from .drawing_geometry import contains

TAG=re.compile(r'\b(?:\d{2,}-)?[A-Z]{1,5}-\d{2,}[A-Z]?\b')
MEASURE=re.compile(r'(?P<label>(?:ID\s*[Xx]\s*(?:TT\s*)?)?(?:HEIGHT|LENGTH)|WIDTH|DIAMETER|ELEVATION)\s*[:=( ]+\s*(?P<unit>mm|cm|m|in|ft)?\)?\s*[:=]?\s*(?P<value>[+-]?\d+(?:\.\d+)?(?:\s*[xX\u00d7]\s*\d+(?:\.\d+)?)?)\s*(?P<suffix>mm|cm|m|in|ft)?\b',re.I)

def extract(page):
    from .drawing_extract import ruled_cell,identifier
    words=page['words'];findings=[];seen=set()
    for word in words:
        if not re.fullmatch(r'(?:HEIGHT|LENGTH|WIDTH|DIAMETER|ELEVATION)[:.]?',word['text'],re.I):continue
        bounds=word['bbox'];height=max(3,bounds[3]-bounds[1]);cell=ruled_cell(page,bounds)
        # A ruled containing cell establishes table scope. Otherwise only an
        # explicit same-row annotation is inspected; proximity is not identity.
        eligible=[w for w in words if (not cell or contains(cell,w['bbox'],.5)) and abs(w['bbox'][1]-bounds[1])<height*.35]
        eligible.sort(key=lambda w:w['bbox'][0])
        index=next(i for i,w in enumerate(eligible) if w['id']==word['id'])
        lo=index
        while lo>0 and eligible[lo]['bbox'][0]-eligible[lo-1]['bbox'][2]<height*4:lo-=1
        hi=index+1
        while hi<len(eligible):
            if eligible[hi]['bbox'][0]-bounds[2]>height*30:break
            if re.fullmatch(r'HEIGHT|LENGTH|WIDTH|DIAMETER|ELEVATION',eligible[hi]['text'],re.I):break
            hi+=1
        row=eligible[lo:hi];text=' '.join(w['text'] for w in row)
        match=MEASURE.search(text)
        if not match:continue
        unit=(match['unit'] or match['suffix'] or '').lower()
        values=re.split(r'\s*[xX\u00d7]\s*',match['value'])
        label=match['label'].lower();axis=label.split()[-1]
        # A pair is interpretable only when the source explicitly gives its order.
        if len(values)>1 and not label.startswith('id'):continue
        value=values[-1];raw_labels=TAG.findall(text)
        association='explicit_same_row_tag'
        if not raw_labels and cell:
            raw_labels=list(dict.fromkeys(tag for w in words if contains(cell,w['bbox'],.5) for tag in TAG.findall(w['text'])))
            association='unique_tag_in_same_ruled_specification_cell'
        objects=list(dict.fromkeys(raw_labels));obj=objects[0] if len(objects)==1 else None
        object_evidence=[w for w in words if obj and obj in TAG.findall(w['text']) and
                         (w in row or (cell and contains(cell,w['bbox'],.5)))]
        bbox=[min(w['bbox'][0] for w in row),min(w['bbox'][1] for w in row),max(w['bbox'][2] for w in row),max(w['bbox'][3] for w in row)]
        key=(word['id'],axis,value,unit)
        if key in seen:continue
        seen.add(key)
        findings.append({'id':identifier('DIM',key),'kind':'explicit_specification' if cell else 'explicit_annotation',
            'label':axis,'value_text':value,'unit':unit or None,'original_text':match[0],
            'source_ids':list(dict.fromkeys(w['id'] for w in row+object_evidence)),
            'object_source_ids':[w['id'] for w in object_evidence],'bbox':bbox,'page':page['page'],
            'object_id':obj,'object_candidates':objects,'object_association':association if obj else 'unresolved',
            'status':'observed' if unit else 'unresolved_unit','independently_verified':False})
        if len(values)==2:
            findings.append({**findings[-1],'id':identifier('DIM',[key,'diameter']),'label':'diameter','value_text':values[0]})
    return findings
