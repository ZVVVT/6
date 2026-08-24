"""Generate independent tail instances from FITC and Stage-6 graph seeds."""
from __future__ import annotations

import argparse, csv, heapq, json
from pathlib import Path
import cv2
import numpy as np

N8=((-1,-1,1.4142),(0,-1,1.),(1,-1,1.4142),(-1,0,1.),
    (1,0,1.),(-1,1,1.4142),(0,1,1.),(1,1,1.4142))

def gray(path):
    im=cv2.imread(str(path),cv2.IMREAD_UNCHANGED)
    if im is None: raise FileNotFoundError(path)
    if im.ndim==3: im=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
    return im.astype(np.float32)

def load_groups(path):
    data=json.loads(path.read_text(encoding='utf-8'))
    data=data['paths'] if isinstance(data,dict) else data
    return [[np.asarray(p,np.int32).reshape(-1,2) for p in group] for group in data]

def seed_geometry(groups,shape):
    owner=np.zeros(shape,np.uint16); tx=np.zeros(shape,np.float32); ty=np.zeros(shape,np.float32)
    for iid,group in enumerate(groups,1):
        for p in group:
            for k,(x,y) in enumerate(p):
                if not (0<=x<shape[1] and 0<=y<shape[0]): continue
                a=p[max(0,k-6)].astype(float); b=p[min(len(p)-1,k+6)].astype(float)
                v=b-a; n=max(float(np.linalg.norm(v)),1.)
                # Stage-6 groups own their seed pixels. Ties retain the first
                # group, so two seeds can never grow through one another.
                if owner[y,x]==0:
                    owner[y,x]=iid; tx[y,x]=v[0]/n; ty[y,x]=v[1]/n
    return owner,tx,ty

def grow(fitc,groups,max_distance,intensity_weight,direction_weight):
    labels,tx,ty=seed_geometry(groups,fitc.shape)
    seed_mask=labels>0
    seed_values=[]; thresholds=np.zeros(len(groups)+1,np.float32); levels=np.ones(len(groups)+1,np.float32)
    for iid in range(1,len(groups)+1):
        vals=fitc[labels==iid]; seed_values.extend(vals.tolist())
        levels[iid]=max(float(np.median(vals)),1.)
        thresholds[iid]=max(6.,float(np.percentile(vals,10))*.42)
    cost=np.full(fitc.shape,np.inf,np.float32); dist=np.full(fitc.shape,np.inf,np.float32)
    heap=[]
    ys,xs=np.where(seed_mask)
    for y,x in zip(ys,xs):
        cost[y,x]=0.; dist[y,x]=0.; heapq.heappush(heap,(0.,int(labels[y,x]),int(y),int(x)))
    while heap:
        d,iid,y,x=heapq.heappop(heap)
        if iid!=labels[y,x] or d>float(cost[y,x])+1e-5: continue
        for dx,dy,step in N8:
            xx,yy=x+dx,y+dy
            if xx<0 or yy<0 or xx>=fitc.shape[1] or yy>=fitc.shape[0]: continue
            # A seed belonging to another graph instance is a hard barrier.
            if seed_mask[yy,xx] and labels[yy,xx]!=iid: continue
            ndist=float(dist[y,x])+step
            if ndist>max_distance or fitc[yy,xx]<thresholds[iid]: continue
            jump=abs(float(fitc[yy,xx]-fitc[y,x]))/levels[iid]
            deficit=max(0.,float(levels[iid]-fitc[yy,xx]))/levels[iid]
            # Growth across the centerline is preferred; travel parallel to
            # its tangent is penalized, limiting switches at crossings.
            align=abs((dx*float(tx[y,x])+dy*float(ty[y,x]))/step)
            nd=d+step*(1.+intensity_weight*(jump+.35*deficit)+direction_weight*align)
            if nd+1e-5<cost[yy,xx]:
                cost[yy,xx]=nd; dist[yy,xx]=ndist; labels[yy,xx]=iid
                tx[yy,xx]=tx[y,x]; ty[yy,xx]=ty[y,x]
                heapq.heappush(heap,(nd,iid,yy,xx))
    return labels,thresholds

def base_view(fitc):
    lo,hi=np.percentile(fitc,[1,99.8])
    return cv2.cvtColor(np.uint8(np.clip((fitc-lo)*255/max(hi-lo,1),0,255)),cv2.COLOR_GRAY2BGR)

def colors(n):
    rng=np.random.default_rng(6022); return rng.integers(45,256,(n+1,3),dtype=np.uint8)

def path_overlay(fitc,groups,cols):
    out=base_view(fitc)
    for iid,group in enumerate(groups,1):
        c=tuple(int(v) for v in cols[iid])
        for p in group: cv2.polylines(out,[p.reshape(-1,1,2)],False,c,2,cv2.LINE_AA)
    cv2.putText(out,f'before: {len(groups)} Stage6 merged graph seeds',(12,26),0,.65,(255,255,255),2)
    return out

def instance_overlay(fitc,labels,cols):
    out=base_view(fitc)
    for iid in range(1,int(labels.max())+1):
        m=labels==iid
        if not m.any(): continue
        tint=np.empty_like(out); tint[:]=cols[iid]
        out[m]=cv2.addWeighted(out,.5,tint,.5,0)[m]
        cs,_=cv2.findContours(m.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out,cs,-1,tuple(int(v) for v in cols[iid]),1,cv2.LINE_AA)
    cv2.putText(out,f'after: {labels.max()} graph-seeded tail instances',(12,26),0,.65,(255,255,255),2)
    return out

def complex_crop(groups,shape,pad=180):
    # Count independent graph groups locally and choose the densest crossing.
    acc=np.zeros(shape,np.uint16)
    for group in groups:
        m=np.zeros(shape,np.uint8)
        for p in group: cv2.polylines(m,[p.reshape(-1,1,2)],False,1,1)
        acc+=cv2.dilate(m,np.ones((81,81),np.uint8)).astype(np.uint16)
    _,_,_,pt=cv2.minMaxLoc(acc.astype(np.float32)); x,y=pt
    return max(0,x-pad),max(0,y-pad),min(shape[1],x+pad),min(shape[0],y+pad)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--fitc',type=Path,required=True); ap.add_argument('--graph-paths',type=Path,required=True)
    ap.add_argument('--output',type=Path,default=Path('outputs/graph_seeded_region_growing'))
    ap.add_argument('--max-distance',type=float,default=18.); ap.add_argument('--intensity-weight',type=float,default=3.)
    ap.add_argument('--direction-weight',type=float,default=.8)
    a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    fitc=gray(a.fitc); groups=load_groups(a.graph_paths)
    labels,thresholds=grow(fitc,groups,a.max_distance,a.intensity_weight,a.direction_weight)
    cols=colors(len(groups)); before=path_overlay(fitc,groups,cols); after=instance_overlay(fitc,labels,cols)
    cv2.imwrite(str(a.output/'graph_paths_overlay.png'),before)
    cv2.imwrite(str(a.output/'generated_tail_instances.tif'),labels)
    cv2.imwrite(str(a.output/'generated_tail_instances_overlay.png'),after)
    x0,y0,x1,y1=complex_crop(groups,fitc.shape)
    cv2.imwrite(str(a.output/'complex_crossing_before_after.png'),np.hstack([before[y0:y1,x0:x1],after[y0:y1,x0:x1]]))
    rows=[]
    for iid in range(1,len(groups)+1):
        m=labels==iid; rows.append({'instance_id':iid,'area_px':int(m.sum()),'FITC_integrated':float(fitc[m].sum()),
                                    'seed_threshold':float(thresholds[iid]),'source':'Stage6 merged graph path'})
    with (a.output/'instance_metrics.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print({'instances':len(groups),'area_px':int((labels>0).sum()),'complex_roi':[x0,y0,x1-x0,y1-y0]})

if __name__=='__main__': main()
