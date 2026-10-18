"""Traditional template/contour matching. Scores are not calibrated probabilities."""
import base64
import math
import time
from .drawing_geometry import iou, contains, tiles
from .drawing_extract import identifier


def builtin_templates():
    # Explicit illustrative profiles, not a claim of universal engineering notation.
    return {
        'gate_valve':{'name':'gate valve','version':1,'kind':'geometry','aliases':['gate valves'],
            'vector_profile':'opposed_triangles','ports':[[8,24],[40,24]],
            'distinguishing_features':['two opposed closed triangles','no additional internal marks'],
            'exclusions':['internal circle','internal stroke or filled marker'],
            'lines':[[8,8,24,24],[24,24,8,40],[8,40,8,8],[40,8,24,24],[24,24,40,40],[40,40,40,8]],
            'limitations':['Supported bow-tie outline only; plant conventions require review.']},
        'instrument_bubble':{'name':'instrument bubble','version':1,'kind':'geometry','aliases':['instrument circles'],
            'circle':[24,24,16],'limitations':['A circle alone does not identify an instrument function.']}}


def template_image(template):
    import cv2
    import numpy as np
    if template.get('png'):
        if len(template['png'])>2_000_000:raise ValueError('Reference image too large.')
        img=cv2.imdecode(np.frombuffer(base64.b64decode(template['png'],validate=True),dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
        if img is None or max(img.shape)>512 or min(img.shape)<8:raise ValueError('Reference image must be 8–512 pixels per side.')
        return cv2.threshold(img,180,255,cv2.THRESH_BINARY_INV)[1]
    img=np.zeros((48,48),dtype=np.uint8)
    if 'circle' in template:cv2.circle(img,tuple(template['circle'][:2]),template['circle'][2],255,2)
    for x0,y0,x1,y1 in template.get('lines',[]):cv2.line(img,(x0,y0),(x1,y1),255,2)
    if not img.any():raise ValueError('Empty reference template.')
    return img


def deduplicate(detections):
    ordered=sorted(detections,key=lambda d:(-d['score'],d['id']));result=[]
    for finding in ordered:
        duplicate=next((d for d in result if d['page']==finding['page'] and d['class']==finding['class'] and iou(d['bbox'],finding['bbox'])>.45),None)
        if duplicate:
            duplicate.setdefault('duplicates',[]).append(finding['id'])
        else:result.append(finding)
    return result


def detect_tile(png,template,page_number,bounds,scale,deadline):
    import cv2
    import numpy as np
    image=cv2.imdecode(np.frombuffer(png,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
    image=cv2.threshold(image,180,255,cv2.THRESH_BINARY_INV)[1]
    base=template_image(template);detections=[];complete=True
    for angle in range(0,360,15):
        for factor in (.5,.75,1.,1.5,2.):
            if time.monotonic()>deadline:complete=False;break
            resized=cv2.resize(base,None,fx=factor,fy=factor,interpolation=cv2.INTER_NEAREST)
            h,w=resized.shape;side=math.ceil(math.hypot(w,h))
            matrix=cv2.getRotationMatrix2D((w/2,h/2),angle,1)
            matrix[:,2]+=[(side-w)/2,(side-h)/2]
            rotated=cv2.warpAffine(resized,matrix,(side,side),flags=cv2.INTER_NEAREST)
            nonzero=cv2.findNonZero(rotated)
            if nonzero is None:continue
            x,y,w,h=cv2.boundingRect(nonzero);rotated=rotated[y:y+h,x:x+w]
            if h>=image.shape[0] or w>=image.shape[1]:continue
            score=cv2.matchTemplate(image,rotated,cv2.TM_CCOEFF_NORMED)
            peaks=(score>=.84)&(score==cv2.dilate(score,np.ones((5,5),np.uint8)))
            ys,xs=np.where(peaks)
            if len(xs)>1000:complete=False;continue
            for x,y in zip(xs,ys):
                patch=image[y:y+h,x:x+w]
                # Penalize additional internal marks, not merely missing outline edges.
                union=np.count_nonzero((patch>0)|(rotated>0))
                overlap=np.count_nonzero((patch>0)&(rotated>0))/max(1,union)
                if overlap<.65:continue
                bbox=[bounds[0]+int(x)/scale,bounds[1]+int(y)/scale,bounds[0]+(int(x)+w)/scale,bounds[1]+(int(y)+h)/scale]
                detections.append({'id':identifier('SYMBOL',[page_number,bbox,template['name']]),'page':page_number,'bbox':bbox,
                    'class':template['name'],'template_version':template['version'],'score':float(score[y,x]),
                    'method':'rotated_scaled_binary_template','angle':angle,'scale':factor,'status':'unverified',
                    'limitations':template.get('limitations',[]),'support':{'pixel_overlap':overlap}})
        if not complete and time.monotonic()>deadline:break
    return deduplicate(detections),complete


def match_vectors(page,template):
    """Match a selected single native contour using rotation/scale invariant moments."""
    if template.get('vector_profile')=='opposed_triangles':return opposed_triangles(page,template)
    if not template.get('points'):return []
    import cv2
    import numpy as np
    target=np.asarray(template['points'],dtype=np.float32).reshape(-1,1,2)
    if len(target)<4 or abs(cv2.contourArea(target))<1e-6:return []
    results=[]
    for path in page['paths']:
        if len(path['points'])<4:continue
        contour=np.asarray(path['points'],dtype=np.float32).reshape(-1,1,2)
        if abs(cv2.contourArea(contour))<1e-6:continue
        distance=cv2.matchShapes(target,contour,cv2.CONTOURS_MATCH_I1,0)
        if distance>.01:continue
        results.append({'id':identifier('SYMBOL',[page['page'],path['id'],template['name']]),
            'page':page['page'],'bbox':path['bbox'],'class':template['name'],'template_version':template['version'],
            'score':1/(1+distance),'method':'native_contour_moments','source_ids':[path['id']],
            'status':'unverified','limitations':['Contour similarity is not independently verified classification.']})
    return deduplicate(results)


def opposed_triangles(page,template):
    """Match explicit closed triangle primitives, preserving all interior marks.

    This profile describes one notation only. It is never a completeness verifier.
    A spatial hash bounds candidate pairing; no drawing-specific coordinates.
    """
    triangles=[];bins={};tolerance=.35
    for path in page['paths']:
        pts=path['points']
        if path.get('path') or path.get('fill') or len(pts)!=4 or math.dist(pts[0],pts[-1])>tolerance:continue
        a,b,c=pts[:3]
        area=abs((b[0]-a[0])*(c[1]-a[1])-(c[0]-a[0])*(b[1]-a[1]))/2
        if area<2:continue
        index=len(triangles);triangles.append(path)
        for pt in pts[:3]:bins.setdefault((round(pt[0]),round(pt[1])),[]).append((index,pt))
    candidates=set();results=[]
    for i,left in enumerate(triangles):
        for tip in left['points'][:3]:
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    for j,other in bins.get((round(tip[0])+dx,round(tip[1])+dy),[]):
                        if j<=i or (i,j) in candidates or math.dist(tip,other)>tolerance:continue
                        candidates.add((i,j));right=triangles[j]
                        l=[p for p in left['points'][:3] if math.dist(p,tip)>tolerance]
                        r=[p for p in right['points'][:3] if math.dist(p,other)>tolerance]
                        if len(l)!=2 or len(r)!=2:continue
                        lc=[sum(p[k] for p in l)/2 for k in (0,1)];rc=[sum(p[k] for p in r)/2 for k in (0,1)]
                        lv=[lc[k]-tip[k] for k in (0,1)];rv=[rc[k]-tip[k] for k in (0,1)]
                        length=math.hypot(*lv);other_length=math.hypot(*rv)
                        if min(length,other_length)<1 or not .8<=length/other_length<=1.25:continue
                        if sum(lv[k]*rv[k] for k in (0,1))/(length*other_length)>-.98:continue
                        if not .8<=math.dist(*l)/max(.01,math.dist(*r))<=1.25:continue
                        pts=left['points']+right['points'];bbox=[min(p[0] for p in pts),min(p[1] for p in pts),max(p[0] for p in pts),max(p[1] for p in pts)]
                        ids={left['id'],right['id']}
                        # Any additional nontrivial geometry entirely inside the symbol
                        # is a distinguishing mark, so this outline class must abstain.
                        excluded=any(p['id'] not in ids and contains(bbox,p['bbox'],.1) and
                            max(p['bbox'][2]-p['bbox'][0],p['bbox'][3]-p['bbox'][1])>1 for p in page['paths'])
                        if excluded:continue
                        results.append({'id':identifier('SYMBOL',[page['page'],sorted(ids),template['name']]),
                            'page':page['page'],'bbox':bbox,'class':template['name'],'template_version':template['version'],
                            'score':1.,'method':'native_opposed_triangle_profile','source_ids':sorted(ids),
                            'ports':[lc,rc],'status':'unverified','limitations':template['limitations']})
    return deduplicate(results)


def count_label(scope,coverage,detections,review=None):
    """Never infer exactness from algorithm scores or agreement."""
    if not coverage.get('complete'):return 'Partial'
    if not coverage.get('classification_supported'):return 'Unverified'
    current=coverage.get('analysis_revision')
    if review and type(current) is int and review.get('verification_revision')==current and review.get('published') and review.get('source_hash')==scope.get('source_hash') and review.get('scope')==scope:
        if review.get('independent') and review.get('exhaustive') and not review.get('unresolved') and review.get('reviewer') and review.get('verification_id'):
            reviewed=review.get('objects',[])
            if len({o['id'] for o in reviewed})==len(reviewed):return 'Exact'
    return 'Estimated'
