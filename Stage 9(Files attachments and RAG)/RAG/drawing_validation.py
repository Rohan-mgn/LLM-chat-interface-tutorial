"""Independent checks and evaluation. Automatic checks never establish ground truth."""
import math
from decimal import Decimal
from .drawing_geometry import iou, valid_box, point


def coordinates(page,findings):
    geometry=page['geometry'];errors=[]
    for finding in findings:
        bounds=finding['bbox']
        if not valid_box(bounds,geometry['width'],geometry['height']):errors.append({'id':finding['id'],'reason':'out_of_page'})
        for x,y in ((bounds[0],bounds[1]),(bounds[2],bounds[3])):
            raw=point(geometry['page_to_pdf'],x,y);back=point(geometry['pdf_to_page'],*raw)
            if math.hypot(back[0]-x,back[1]-y)>1e-6:errors.append({'id':finding['id'],'reason':'transform_round_trip'})
    return {'status':'automatically_checked','errors':errors,'independently_verified':False}


def evaluate(predictions,truth,*,verified=False):
    pairs=[]
    for i,pred in enumerate(predictions):
        for j,actual in enumerate(truth):
            if pred['page']==actual['page']:
                score=iou(pred['bbox'],actual['bbox'])
                if score>=.5:pairs.append((score,i,j))
    used_p=set();used_t=set();matches=[]
    for score,i,j in sorted(pairs,reverse=True):
        if i in used_p or j in used_t:continue
        used_p.add(i);used_t.add(j);matches.append((score,i,j))
    correct=sum(predictions[i]['class']==truth[j]['class'] for _,i,j in matches)
    return {'precision':len(matches)/len(predictions) if predictions else (1. if not truth else 0.),
        'recall':len(matches)/len(truth) if truth else (1. if not predictions else 0.),
        'mean_iou':sum(s for s,_,_ in matches)/len(matches) if matches else None,
        'classification_accuracy':correct/len(matches) if matches else None,
        'count_agreement':len(predictions)==len(truth),'false_positives':len(predictions)-len(matches),
        'false_negatives':len(truth)-len(matches),'ground_truth_status':'human_reviewed' if verified else 'not_independently_verified'}


def convert(value,source,target):
    units={'mm':Decimal('.001'),'cm':Decimal('.01'),'m':Decimal(1),'in':Decimal('.0254'),'ft':Decimal('.3048')}
    if source not in units or target not in units:raise ValueError('Unsupported or dimension-incompatible unit.')
    result=Decimal(str(value))*units[source]/units[target]
    if not result.is_finite():raise ValueError('Nonfinite measurement.')
    return str(result)


def measurement(distance,scale,page):
    if page.get('not_to_scale'):raise ValueError('This drawing is marked not to scale. No real-world pixel measurement is available.')
    if not scale or not scale.get('verified') or not scale.get('source_ids'):raise ValueError('A verified applicable scale is required.')
    if scale.get('page')!=page['page']:raise ValueError('Scale belongs to a different page.')
    return str(Decimal(str(distance))*Decimal(str(scale['units_per_point'])))


def raster_connectivity(png,start,end):
    """Independent raster flood connectivity; deliberately does not reuse vector graph construction."""
    import cv2
    import numpy as np
    image=cv2.imdecode(np.frombuffer(png,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
    mask=(image<160).astype(np.uint8)
    _,labels=cv2.connectedComponents(mask,connectivity=8)
    x0,y0=map(int,start);x1,y1=map(int,end)
    if not(0<=x0<mask.shape[1] and 0<=x1<mask.shape[1] and 0<=y0<mask.shape[0] and 0<=y1<mask.shape[0]):raise ValueError('Raster probe outside image.')
    connected=labels[y0,x0]!=0 and labels[y0,x0]==labels[y1,x1]
    return {'connected_pixels':bool(connected),'status':'automatically_checked',
        'limitation':'Pixel connectivity cannot distinguish engineering crossings, symbols, or signal semantics.'}
