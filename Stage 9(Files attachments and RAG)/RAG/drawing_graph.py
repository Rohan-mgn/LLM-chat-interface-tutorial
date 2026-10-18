"""Conservative vector-line graph. Geometric continuity is not process semantics."""
from collections import deque
import math
from .drawing_extract import identifier


def build(page):
    edges=[];nodes={};locations={};tolerance=.25
    def node(p):
        key=(round(p[0]/tolerance),round(p[1]/tolerance))
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                old=locations.get((key[0]+dx,key[1]+dy))
                if old and math.dist(nodes[old]['point'],p)<=tolerance:return old
        nid=identifier('NODE',[page['page'],p]);nodes[nid]={'id':nid,'point':p,'page':page['page']};locations[key]=nid;return nid
    for path in page['paths']:
        if path['kind']!='line' or len(path['points'])!=2:continue
        a,b=path['points']
        if math.dist(a,b)<3:continue
        edges.append({'id':identifier('EDGE',path['id']),'a':node(a),'b':node(b),'points':[a,b],
            'source_ids':[path['id']],'kind':'dashed_unclassified' if path.get('dash') else 'solid_unclassified',
            'direction':None,'page':page['page'],'independently_verified':False})
    bins={};pairs=set();crossings=[]
    for index,edge in enumerate(edges):
        a,b=edge['points']
        for x in range(math.floor(min(a[0],b[0])/64),math.floor(max(a[0],b[0])/64)+1):
            for y in range(math.floor(min(a[1],b[1])/64),math.floor(max(a[1],b[1])/64)+1):
                cell=bins.setdefault((x,y),[])
                for other in cell:pairs.add((other,index))
                cell.append(index)
    for i,j in pairs:
        e,f=edges[i],edges[j]
        if {e['a'],e['b']}&{f['a'],f['b']}:continue
        (x1,y1),(x2,y2)=e['points'];(x3,y3),(x4,y4)=f['points']
        den=(x1-x2)*(y3-y4)-(y1-y2)*(x3-x4)
        if abs(den)<1e-9:continue
        t=((x1-x3)*(y3-y4)-(y1-y3)*(x3-x4))/den
        u=-((x1-x2)*(y1-y3)-(y1-y2)*(x1-x3))/den
        if 0<=t<=1 and 0<=u<=1:
            crossings.append({'point':[x1+t*(x2-x1),y1+t*(y2-y1)],'edges':[e['id'],f['id']],
                'status':'unresolved_crossing','connected':False})
    return {'nodes':list(nodes.values()),'edges':edges,'crossings':crossings,
        'limitations':['Crossings are not joined without explicit reviewed junction evidence.',
            'Line continuity does not establish process/signal class or flow direction.']}


def trace(graph,start,end):
    nodes={n['id'] for n in graph['nodes']}
    if start not in nodes or end not in nodes:raise ValueError('Select two existing graph endpoints.')
    adjacency={n:[] for n in nodes}
    for edge in graph['edges']:
        adjacency[edge['a']].append((edge['b'],edge['id']));adjacency[edge['b']].append((edge['a'],edge['id']))
    queue=deque([(start,[])]);seen={start}
    while queue:
        node,path=queue.popleft()
        if node==end:return {'edges':path,'status':'geometric_path','direction':'unverified',
            'limitations':graph['limitations'],'engineering_connection_verified':False}
        for neighbor,edge in adjacency[node]:
            if neighbor not in seen:seen.add(neighbor);queue.append((neighbor,path+[edge]))
    return {'edges':[],'status':'unresolved','limitations':['No continuous supported path; crossings or missing sheets may be involved.']}
