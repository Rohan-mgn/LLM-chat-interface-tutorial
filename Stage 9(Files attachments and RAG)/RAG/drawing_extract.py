"""Coordinate-preserving native PDF analysis. Uploaded instructions are data."""
import hashlib
import io
import json
import re
from .drawing_geometry import geometry, box, contains

VERSION='drawing-native-v3'
MAX_PRIMITIVES=150000


def identifier(kind,data):
    return kind+'_'+hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()[:24]


def extract_page(path,page_number):
    import pdfplumber
    from pypdf import PdfReader
    reader=PdfReader(path,strict=True)
    if reader.is_encrypted:raise ValueError('Encrypted PDFs are unsupported.')
    if not 1<=page_number<=len(reader.pages)<=200:raise ValueError('Invalid PDF page or page limit exceeded.')
    raw=reader.pages[page_number-1]
    geo=geometry(list(map(float,raw.mediabox)),list(map(float,raw.cropbox)),raw.rotation,float(raw.get('/UserUnit',1)))
    warnings=[]
    content=raw.get_contents()
    clipped=bool(content and any(op in (b'W',b'W*') for _,op in content.operations))
    if clipped:warnings.append('Clipping paths are present; clipped visibility is not independently reconstructed. Review the original rendered page.')
    with pdfplumber.open(path) as document:
        page=document.pages[page_number-1]
        # pdfplumber uses rotated MediaBox coordinates; convert to displayed CropBox.
        ox,oy=geo['display_crop'][:2];u=geo['user_unit']
        base_x,base_y=page.bbox[:2]
        def transform(bounds):
            x0,y0,x1,y1=bounds
            return [(x0-base_x-ox)*u,(y0-base_y-oy)*u,(x1-base_x-ox)*u,(y1-base_y-oy)*u]
        def locate(bounds):
            display=transform(bounds)
            return {'bbox':display,'pdf_bbox':box(geo['page_to_pdf'],display)}
        words=[]
        for word in page.extract_words(x_tolerance=1,y_tolerance=2,keep_blank_chars=False):
            location=locate([word[k] for k in ('x0','top','x1','bottom')])
            if contains([0,0,geo['width'],geo['height']],location['bbox'],.5):
                words.append({'text':word['text'],**location,'id':identifier('TEXT',[page_number,word['text'],location])})
        characters=[[char.get('text',''),transform([char[k] for k in ('x0','top','x1','bottom')]),
                     char.get('fontname',''),char.get('size',0)*u] for char in page.chars[:100000]]
        if len(page.chars)>100000:warnings.append('Character geometry budget exceeded; native character coverage is partial.')
        paths=[];segments=0
        for kind,objects in (('line',page.lines),('curve',page.curves),('rectangle',page.rects)):
            for obj in objects:
                pts=obj.get('pts') or [(obj['x0'],obj['top']),(obj['x1'],obj['bottom'])]
                points=[[(x-base_x-ox)*u,(y-base_y-oy)*u] for x,y in pts]
                segments+=len(points)
                if segments>MAX_PRIMITIVES:
                    warnings.append('Vector segment budget exceeded; geometry coverage is partial.');break
                item={'kind':kind,**locate([obj[k] for k in ('x0','top','x1','bottom')]),
                    'points':points,'path':obj.get('path') if any(part[0] not in ('m','l','h') for part in obj.get('path',[])) else None,
                    'stroke':obj.get('stroke',True),'fill':obj.get('fill',False),
                    'width':obj.get('linewidth',0)*u,'dash':obj.get('dash'),'color':obj.get('stroking_color')}
                item['id']=identifier('PATH',[page_number,len(paths),item['bbox']]);paths.append(item)
            if segments>MAX_PRIMITIVES:break
        images=[{'id':identifier('IMAGE',[page_number,i]),**locate([o[k] for k in ('x0','top','x1','bottom')]),
                 'method':'native_image_region','interpretation':'unavailable_without_model'} for i,o in enumerate(page.images)]
        if not words:warnings.append('No native text. Raster-only text recognition is unsupported in model-free mode.')
        text_rows=[]
        for word in sorted(words,key=lambda w:(round(w['bbox'][1]/3),w['bbox'][0])):
            match=next((row for row in reversed(text_rows[-8:]) if abs(row['bbox'][1]-word['bbox'][1])<2*u),None)
            if match is None:
                match={'text':'','bbox':word['bbox'].copy(),'word_ids':[]};text_rows.append(match)
            match['text']+=' '+word['text'];match['word_ids'].append(word['id'])
            match['bbox']=[min(match['bbox'][0],word['bbox'][0]),min(match['bbox'][1],word['bbox'][1]),max(match['bbox'][2],word['bbox'][2]),max(match['bbox'][3],word['bbox'][3])]
        result={'version':VERSION,'page':page_number,'page_label':reader.page_labels[page_number-1],
            'page_count':len(reader.pages),'geometry':geo,'words':words,'paths':paths,'images':images,
            'characters':{'columns':['text','page_bbox','font','size'],'rows':characters},
            'text_rows':text_rows,'fields':[], 'coverage':{'native_text':bool(words),'vectors_complete':not any('budget' in w for w in warnings),
                'raster_text_read':False,'limitations':warnings,'clipping_verified':not clipped},
            'not_to_scale':bool(re.search(r'\bNTS\b|NOT\s+TO\s+SCALE',' '.join(w['text'] for w in words),re.I))}
        result['fields']=title_fields(result)
        from .drawing_revisions import cross_check
        cross_check(result)
        from .drawing_measurements import extract as explicit_dimensions
        result['dimensions']=explicit_dimensions(result)
        return result


def title_fields(page):
    words=page['words'];paths=list(page['paths']);fields=[]
    # Rectangle borders are ruled boundaries, like separate line objects.
    for p in page['paths']:
        if p['kind']=='rectangle':
            x,y,r,b=p['bbox']
            paths.extend({'kind':'line','bbox':v} for v in ([x,y,r,y],[x,b,r,b],[x,y,x,b],[r,y,r,b]))
    def word_label(word):return re.sub(r'[^a-z]','',word['text'].lower())
    anchors=[]
    for word in words:
        label=word_label(word)
        if label in ('rev','revision'):anchors.append(('revision',word))
        if label=='drawing':
            near=[w for w in words if 0<=w['bbox'][0]-word['bbox'][2]<30 and abs(w['bbox'][1]-word['bbox'][1])<3]
            if any(word_label(w) in ('no','number') for w in near):anchors.append(('drawing_number',word))
        if label in ('scale','project') and word['text'].endswith(':'):anchors.append((label+'_title',word))
    drawing_labels=[a for key,a in anchors if key=='drawing_number']
    for key,anchor in anchors:
        x0,y0,x1,y1=anchor['bbox'];h=max(5,y1-y0)
        if key=='revision' and not any(abs(a['bbox'][1]-y0)<h*2 for a in drawing_labels):continue
        vertical=[p['bbox'] for p in paths if p['kind']=='line' and abs(p['bbox'][2]-p['bbox'][0])<.5 and p['bbox'][1]<=y0+1 and p['bbox'][3]>=y1+h]
        left=max([p[0] for p in vertical if p[0]<=x0] or [x0-h])
        right=min([p[0] for p in vertical if p[0]>=x1] or [x1+h*3])
        boundaries=sorted(set(p['bbox'][1] for p in paths if p['kind']=='line' and abs(p['bbox'][3]-p['bbox'][1])<.5 and p['bbox'][0]<=x0 and p['bbox'][2]>=x1 and p['bbox'][1]>y1))
        bottom=min(boundaries[min(1,len(boundaries)-1)] if boundaries else y1+h*7,y1+h*7)
        candidates=[w for w in words if left<=w['bbox'][0] and w['bbox'][2]<=right+1 and y1<w['bbox'][1]<bottom and w['bbox'][3]<=y1+h*7]
        if key=='revision':candidates=[w for w in candidates if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,9}',w['text'])]
        if key=='drawing_number':candidates=[w for w in candidates if len(w['text'])>=5 and any(c.isdigit() for c in w['text']) and re.fullmatch(r'[\w./-]+',w['text'])]
        supported=len(candidates)==1 and len(vertical)>=2
        fields.append({'id':identifier('FIELD',[key,anchor['id']]),'key':key,'value':candidates[0]['text'] if supported else None,
            'bbox':candidates[0]['bbox'] if supported else anchor['bbox'],'label_bbox':anchor['bbox'],
            'source_ids':[anchor['id'],*[w['id'] for w in candidates]],'candidates':candidates,
            'method':'native_text_in_ruled_title_block_column','status':'observed' if supported else 'unresolved',
            'independently_verified':False})
    # Conflicting title-block candidates remain explicit; no max-revision heuristic.
    for key in {f['key'] for f in fields}:
        values={f['value'] for f in fields if f['key']==key and f['value'] is not None}
        if len(values)>1:
            for f in fields:
                if f['key']==key:f['status']='conflicting';f['value']=None
    for word in words:
        label=word_label(word)
        if label not in ('project','drawing'):continue
        nearby=[w for w in words if 0<=w['bbox'][0]-word['bbox'][2]<40 and abs(w['bbox'][1]-word['bbox'][1])<3]
        if label=='project' and any(word_label(w) in ('no','number') for w in nearby):continue
        if label=='drawing' and not any(word_label(w)=='title' for w in nearby):continue
        cell=ruled_cell(page,word['bbox'])
        if cell is None:continue
        values=[w for w in words if contains(cell,w['bbox'],.5) and w['id']!=word['id'] and not(label=='drawing' and word_label(w)=='title')]
        if not values:continue
        values.sort(key=lambda w:(round(w['bbox'][1]/4),w['bbox'][0]))
        key=label+'_title'
        fields.append({'id':identifier('FIELD',[key,word['id']]),'key':key,'value':' '.join(w['text'] for w in values),
            'bbox':cell,'label_bbox':word['bbox'],'source_ids':[word['id'],*[w['id'] for w in values]],
            'method':'native_text_in_enclosing_ruled_cell','status':'observed','independently_verified':False,'candidates':values})
    return fields


def ruled_cell(page,bounds):
    x=(bounds[0]+bounds[2])/2;y=(bounds[1]+bounds[3])/2
    horizontal=[];vertical=[]
    for path in page['paths']:
        a,b,c,d=path['bbox']
        if path['kind']=='rectangle':
            horizontal.extend([[a,b,c,b],[a,d,c,d]]);vertical.extend([[a,b,a,d],[c,b,c,d]])
        elif path['kind']=='line':
            if abs(a-c)<.5:vertical.append([a,b,c,d])
            if abs(b-d)<.5:horizontal.append([a,b,c,d])
    left=[v[0] for v in vertical if v[0]<bounds[0] and v[1]<=y<=v[3]]
    right=[v[0] for v in vertical if v[0]>bounds[2] and v[1]<=y<=v[3]]
    above=[h[1] for h in horizontal if h[1]<bounds[1] and h[0]<=x<=h[2]]
    below=[h[1] for h in horizontal if h[1]>bounds[3] and h[0]<=x<=h[2]]
    if not all((left,right,above,below)):return None
    return [max(left),max(above),min(right),min(below)]


def dimensions(page):
    found=[]
    # Native specifications with explicit units. Association is retained, never inferred from proximity alone.
    pattern=re.compile(r'(?P<label>height|width|length|diameter|elevation|ID\s*X\s*LENGTH)\s*[:=( ]+\s*(?P<unit>mm|cm|m|in|ft)?\)?\s*[:=]?\s*(?P<value>[+-]?\d+(?:\.\d+)?(?:\s*[xX×]\s*\d+(?:\.\d+)?)?)\s*(?P<suffix>mm|cm|m|in|ft)?\b',re.I)
    for row in page['text_rows']:
        for match in pattern.finditer(row['text']):
            unit=match['unit'] or match['suffix']
            found.append({'id':identifier('DIM',[row['word_ids'],match[0]]),'kind':'explicit_annotation',
                'label':match['label'],'value_text':match['value'],'unit':unit,'bbox':row['bbox'],
                'source_ids':row['word_ids'],'original_text':match[0],
                'status':'observed' if unit else 'unresolved_unit','object_association':'unresolved'})
    return found


def render(path,page_number,bounds=None,scale=1):
    import pypdfium2 as pdfium
    from .drawing_geometry import valid_box
    if not .1<=scale<=8:raise ValueError('Render scale outside supported bounds.')
    from pypdf import PdfReader
    raw=PdfReader(path,strict=True).pages[page_number-1]
    user_unit=float(raw.get('/UserUnit',1))
    if not 0<user_unit<=1000:raise ValueError('Unsupported UserUnit for bounded rendering.')
    with pdfium.PdfDocument(path) as document:
        if not 1<=page_number<=len(document):raise ValueError('Invalid page.')
        page=document[page_number-1]
        try:
            native_w,native_h=page.get_size();w,h=native_w*user_unit,native_h*user_unit
            bounds=bounds or [0,0,w,h]
            if not valid_box(bounds,w,h):raise ValueError('Region outside original PDF page.')
            if (bounds[2]-bounds[0])*scale>2048 or (bounds[3]-bounds[1])*scale>2048:
                raise ValueError('Render region exceeds 2048 pixels; request tiles.')
            bitmap=page.render(scale=scale*user_unit,crop=(bounds[0]/user_unit,native_h-bounds[3]/user_unit,native_w-bounds[2]/user_unit,bounds[1]/user_unit))
            try:
                out=io.BytesIO();bitmap.to_pil().save(out,format='PNG');return out.getvalue()
            finally:bitmap.close()
        finally:page.close()
