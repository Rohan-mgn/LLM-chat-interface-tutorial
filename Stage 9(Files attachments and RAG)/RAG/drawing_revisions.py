"""Ruled revision-table corroboration, never a largest-number/date selector."""
import re
from .drawing_geometry import contains


def cross_check(page):
    from .drawing_extract import ruled_cell,identifier
    words=page['words'];tables=[]
    lines=[p['bbox'] for p in page['paths'] if p['kind']=='line' and abs(p['bbox'][0]-p['bbox'][2])<.5]
    for path in page['paths']:
        if path['kind']=='rectangle':
            x,y,r,b=path['bbox'];lines.extend(([x,y,x,b],[r,y,r,b]))
    for anchor in words:
        if anchor['text'].strip('.:').upper() not in ('REV','REVISION'):continue
        cell=ruled_cell(page,anchor['bbox'])
        if not cell:continue
        height=max(3,anchor['bbox'][3]-anchor['bbox'][1])
        aligned=[w for w in words if cell[2]<w['bbox'][0]<cell[2]+height*60 and abs(w['bbox'][1]-anchor['bbox'][1])<height*.5]
        labels={w['text'].strip('.:').upper() for w in aligned}
        if 'DATE' not in labels or not labels.intersection({'DESCRIPTION','REVISION','CHANGE','CHANGES'}):continue
        sides=[]
        for x in (cell[0],cell[2]):
            spans=[p for p in lines if abs(p[0]-x)<.5 and p[1]<=anchor['bbox'][1] and p[3]>=anchor['bbox'][3]]
            if not spans:break
            sides.append(max(spans,key=lambda p:p[3]-p[1]))
        if len(sides)!=2:continue
        top=max(p[1] for p in sides);bottom=min(p[3] for p in sides)
        column=[cell[0],top,cell[2],bottom]
        entries=[]
        for word in words:
            if word['id']==anchor['id'] or not contains(column,word['bbox'],.5) or contains(cell,word['bbox'],.5):continue
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,9}',word['text']):continue
            entry_cell=ruled_cell(page,word['bbox'])
            if not entry_cell or abs(entry_cell[0]-cell[0])>.5 or abs(entry_cell[2]-cell[2])>.5:continue
            entries.append({'value':word['text'],'source_id':word['id'],'bbox':word['bbox'],'cell_bbox':entry_cell})
        tables.append({'id':identifier('REVISIONS',[anchor['id'],column]),'column_bbox':column,
            'header_source_ids':[anchor['id'],*[w['id'] for w in aligned if w['text'].strip('.:').upper() in ('DATE','DESCRIPTION','REVISION','CHANGE','CHANGES')]],
            'entries':entries,'method':'ruled_column_with_revision_date_description_headers',
            'current_revision_convention':'not inferred from row order, dates or revision magnitude'})
    page['revision_tables']=tables
    for field in page['fields']:
        if field['key']!='revision':continue
        field['revision_table_check']={'status':'unavailable','table_ids':[t['id'] for t in tables]}
        if field['value'] is None:continue
        entries=[entry for table in tables for entry in table['entries']]
        if not entries:continue
        matches=[entry for entry in entries if entry['value']==field['value']]
        field['revision_table_check'].update(status='corroborated' if matches else 'conflicting',entries=entries)
        if matches:
            field['source_ids']=list(dict.fromkeys([*field['source_ids'],*[m['source_id'] for m in matches]]))
            field['corroborating_regions']=[{'page':page['page'],'bbox':m['bbox'],'label':'revision table entry'} for m in matches]
        else:
            field['unresolved_title_block_value']=field['value'];field['value']=None;field['status']='conflicting'
            field['limitation']='Title-block revision is absent from the structurally recognized revision table; human review is required.'
    return page
