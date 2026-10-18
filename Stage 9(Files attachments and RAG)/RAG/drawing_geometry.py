"""Explicit affine transforms and drawing geometry; no model inference."""
import math


def point(matrix, x, y):
    a,b,c,d,e,f=matrix
    return [a*x+c*y+e,b*x+d*y+f]


def inverse(matrix):
    a,b,c,d,e,f=matrix;det=a*d-b*c
    if abs(det)<1e-12:raise ValueError('Singular coordinate transform.')
    return [d/det,-b/det,-c/det,a/det,(c*f-d*e)/det,(b*e-a*f)/det]


def box(matrix, bounds):
    x0,y0,x1,y1=bounds
    points=[point(matrix,x,y) for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))]
    return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]


def geometry(media, crop, rotation, user_unit=1):
    l,b,r,t=map(float,media);rotation=rotation%360
    matrices={0:[1,0,0,-1,-l,t],90:[0,1,1,0,-b,-l],180:[-1,0,0,1,r,-b],270:[0,-1,-1,0,t,r]}
    if rotation not in matrices or not math.isfinite(user_unit) or user_unit<=0:
        raise ValueError('Unsupported PDF rotation or UserUnit.')
    matrix=matrices[rotation];display_crop=box(matrix,crop)
    matrix=[v*user_unit for v in matrix]
    matrix[4]-=display_crop[0]*user_unit;matrix[5]-=display_crop[1]*user_unit
    return {'media_box':list(media),'crop_box':list(crop),'rotation':rotation,'user_unit':user_unit,
        'pdf_to_page':matrix,'page_to_pdf':inverse(matrix),
        'width':(display_crop[2]-display_crop[0])*user_unit,'height':(display_crop[3]-display_crop[1])*user_unit,
        'display_crop':display_crop,'coordinate_space':'crop-relative rotated page points, top-left origin'}


def valid_box(bounds,width,height):
    return isinstance(bounds,(list,tuple)) and len(bounds)==4 and all(isinstance(v,(int,float)) and math.isfinite(v) for v in bounds) and 0<=bounds[0]<bounds[2]<=width and 0<=bounds[1]<bounds[3]<=height


def iou(a,b):
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return intersection/max(1e-12,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection)


def contains(outer,inner,tolerance=0):
    return outer[0]-tolerance<=inner[0] and outer[1]-tolerance<=inner[1] and outer[2]+tolerance>=inner[2] and outer[3]+tolerance>=inner[3]


def tiles(width,height,size=2048,overlap=128):
    step=size-overlap
    if step<=0:raise ValueError('Invalid tile overlap.')
    for y in range(0,math.ceil(height),step):
        for x in range(0,math.ceil(width),step):
            yield [x,y,min(x+size,width),min(y+size,height)]
