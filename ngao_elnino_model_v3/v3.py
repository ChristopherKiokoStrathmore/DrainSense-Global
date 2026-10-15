import json,warnings,numpy as np,pandas as pd,xarray as xr,geopandas as gpd,lightgbm as lgb
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.decomposition import PCA
from sklearn.metrics import roc_auc_score,f1_score,brier_score_loss,confusion_matrix
warnings.filterwarnings('ignore'); D='/workspace/ngao_ds'; V=D+'/model_v3'; RW=V+'/raw'
TRAIN_END=2015
def mon(da,months,yrs):
    da=da.sel(time=da.time.dt.month.isin(months)); g=da.groupby('time.year').mean('time'); return g.sel(year=[y for y in yrs if y in g.year.values])
YRS=list(range(1951,2026))
sst=xr.open_dataset(RW+'/ersst.nc').sst; u=xr.open_dataset(RW+'/uwnd.nc').uwnd.sel(level=850); slp=xr.open_dataset(RW+'/slp.nc').slp
S=mon(sst,[8,9],YRS); U=mon(u,[7,8,9],YRS); P=mon(slp,[7,8,9],YRS)
def box(da,la0,la1,lo0,lo1):
    la=da.lat; sl=slice(la1,la0) if la[0]>la[-1] else slice(la0,la1); x=da.sel(lat=sl,lon=slice(lo0,lo1)); w=np.cos(np.deg2rad(x.lat)); return x.weighted(w).mean(['lat','lon']).to_series()
# literature indices (Aug-Sep SST, Jul-Sep wind/SLP)
L=pd.DataFrame({'wio':box(S,-10,10,50,70),'eio':box(S,-10,0,90,110),'nino34':box(S,-5,5,190,240),
 'u850_ceio':box(U,-5,5,60,90),'slp_wio':box(P,-10,10,50,70),'slp_eio':box(P,-10,0,90,110)})
L['dmi_ersst']=L.wio-L.eio; L['slp_grad']=L.slp_eio-L.slp_wio
L=L.loc[YRS]
def pcs(da,box_,n,fit_years,nm=None):
    nm=nm or da.name
    la0,la1,lo0,lo1=box_; la=da.lat; sl=slice(la1,la0) if la[0]>la[-1] else slice(la0,la1); x=da.sel(lat=sl,lon=slice(lo0,lo1))
    w=np.sqrt(np.cos(np.deg2rad(x.lat))); a=(x*w).stack(p=('lat','lon')).to_pandas().loc[YRS]; a=a.loc[:,a.notna().all()]
    tr=a.loc[[y for y in fit_years]]; mu=tr.mean(); pca=PCA(n).fit(tr-mu)
    return pd.DataFrame(pca.transform(a-mu),index=a.index,columns=[f'{nm}_pc{i+1}' for i in range(n)]),pca.explained_variance_ratio_
cl=pd.read_csv(D+'/out/merged_ward_monthly.csv',usecols=['ward_id','year','month','rainfall_mm'])
ond=cl[cl.month.isin([10,11,12])].groupby(['ward_id','year']).rainfall_mm.agg(['sum','size']).reset_index(); ond=ond[(ond['size']==3)&(ond.year<=2025)].rename(columns={'sum':'tot'})
c9=ond[(ond.year>=1991)&(ond.year<=2020)].groupby('ward_id').tot
ond=ond.join(c9.mean().rename('clim'),on='ward_id').join(c9.quantile(2/3).rename('q67'),on='ward_id')
ond['yter']=(ond.tot>ond.q67).astype(int); ond['y25']=(ond.tot>1.25*ond.clim).astype(int); ond['src']='CHIRPS'
# GPCC extension: nearest 1deg cell to ward centroid; thresholds from GPCC own 1981-2015 (train-only) per ward
W=gpd.read_file(D+'/raw/KEN_ADM3.geojson'); cc=W.to_crs(32737).centroid.to_crs(4326)
g=xr.open_dataset(RW+'/gpcc.nc').precip; g=g.sel(time=g.time.dt.month.isin([10,11,12])).groupby('time.year').sum('time',min_count=3)
gs=g.sel(lat=xr.DataArray(cc.y.values,dims='w'),lon=xr.DataArray(cc.x.values%360,dims='w'),method='nearest').to_pandas()
gs.columns=W.shapeID.values; gl=gs.stack().rename('tot').reset_index().rename(columns={'level_1':'ward_id'})
gb=gl[(gl.year>=1981)&(gl.year<=2015)].groupby('ward_id').tot
gl=gl.join(gb.mean().rename('clim'),on='ward_id').join(gb.quantile(2/3).rename('q67'),on='ward_id')
gl['yter']=(gl.tot>gl.q67).astype(int); gl['y25']=(gl.tot>1.25*gl.clim).astype(int)
ov=gl[(gl.year>=1981)&(gl.year<=2015)].merge(ond,on=['ward_id','year'],suffixes=('_g','_c'))
consist={'corr_ward_OND_totals':round(float(ov.tot_g.corr(ov.tot_c)),3),
 'median_per_ward_corr':round(float(ov.groupby('ward_id').apply(lambda d:d.tot_g.corr(d.tot_c)).median()),3),
 'kenya_mean_series_corr':round(float(ov.groupby('year').tot_g.mean().corr(ov.groupby('year').tot_c.mean())),3),
 'label_agreement_yter':round(float((ov.yter_g==ov.yter_c).mean()),3),'label_agreement_y25':round(float((ov.y25_g==ov.y25_c).mean()),3)}
ext=gl[(gl.year>=1951)&(gl.year<=1980)].dropna(subset=['tot']).assign(src='GPCC')
reg=pd.read_csv(D+'/model_v2/ward_regions.csv')[['ward_id','region']]
I=pd.read_csv(D+'/out/indices_monthly_merged.csv')
def v2idx():
    pv=lambda c:I.pivot(index='year',columns='month',values=c)
    n,dm,o=pv('nino34'),pv('dmi'),pv('oni')
    f=pd.DataFrame({'v2_nino_L0':n[9],'v2_nino_trend':n[9]-n[6],'v2_oni':o[8],'v2_dmi_L0':dm[9],'v2_dmi_trend':dm[9]-dm[6]}); f['v2_oni_x_dmi']=f.v2_oni*f.v2_dmi_L0; return f.loc[YRS]
V2=v2idx()
lit=['wio','eio','dmi_ersst','nino34','u850_ceio','slp_grad']
def mk_lr(C): return make_pipeline(StandardScaler(),LogisticRegression(C=C,max_iter=3000))
def mk_gbm(): return lgb.LGBMClassifier(n_estimators=150,learning_rate=0.03,max_depth=2,num_leaves=3,min_child_samples=3000,reg_lambda=10,subsample=0.7,subsample_freq=1,verbose=-1,random_state=0)
def blocks(yrs,k=5): return np.array_split(sorted(set(yrs)),k)
def oof(mk,X,y,yrs):
    o=np.zeros(len(y))
    for b in blocks(yrs):
        i=np.isin(yrs,b); o[i]=mk().fit(X[~i],y[~i]).predict_proba(X[i])[:,1]
    return o
def thr(y,p): ts=np.linspace(0.05,0.9,86); return float(ts[np.argmax([f1_score(y,p>=t,zero_division=0) for t in ts])])
def ev(y,p,yrs,t):
    pr=p>=t; tn,fp,fn,tp=confusion_matrix(y,pr,labels=[0,1]).ravel(); rng=np.random.default_rng(0); U=np.unique(yrs); gi={u:np.flatnonzero(yrs==u) for u in U}; A=[];F=[]
    for _ in range(1000):
        i=np.concatenate([gi[u] for u in rng.choice(U,len(U))])
        if len(set(y[i]))>1: A.append(roc_auc_score(y[i],p[i])); F.append(f1_score(y[i],p[i]>=t,zero_division=0))
    lo=[f1_score(y[yrs!=u],pr[yrs!=u],zero_division=0) for u in U]; loa=[roc_auc_score(y[yrs!=u],p[yrs!=u]) for u in U]
    py=pd.DataFrame({'yr':yrs,'y':y,'p':p}).groupby('yr').mean()
    return dict(acc=(tp+tn)/len(y),prec=tp/(tp+fp) if tp+fp else 0,rec=tp/(tp+fn),f1=f1_score(y,pr),auc=roc_auc_score(y,p),brier=brier_score_loss(y,np.clip(p,0,1)),
        tn=int(tn),fp=int(fp),fn=int(fn),tp=int(tp),thr=t,auc_ci90=[np.percentile(A,5),np.percentile(A,95)],f1_ci90=[np.percentile(F,5),np.percentile(F,95)],
        loyo_f1=[min(lo),max(lo)],loyo_auc=[min(loa),max(loa)],yearcorr=float(np.corrcoef(py.y,py.p)[0,1]) if py.p.std()>0 else None,
        per_year={int(k):[round(a,3),round(b,3)] for k,(a,b) in py.iterrows()})
rows=[];explained={}
for tgt in ['yter','y25']:
  for period in ['1981-2015 CHIRPS','1951-2015 GPCC+CHIRPS']:
    lab=pd.concat([ond,ext]) if 'GPCC' in period else ond
    fit_years=list(range(1951 if 'GPCC' in period else 1981,TRAIN_END+1))
    sp,ev1=pcs(S,(-30,30,30,290),5,fit_years); up,ev2=pcs(U,(-20,20,30,150),3,fit_years); pp,ev3=pcs(P,(-30,30,30,290),3,fit_years)
    explained[period]={'sst':ev1.round(3).tolist(),'u850':ev2.round(3).tolist(),'slp':ev3.round(3).tolist()}
    Y=pd.concat([L,V2,sp,up,pp],axis=1); Y.index.name='year'
    F=lab[['ward_id','year',tgt,'src']].merge(Y.reset_index(),on='year').merge(reg,on='ward_id')
    for r in range(6): F[f'r{r}']=(F.region==r).astype(float)
    F=F[F.year>=fit_years[0]]
    tr=F[F.year<=TRAIN_END]; te=F[(F.year>=2016)&(F.src=='CHIRPS')]
    yt,ye,yrt,yre=tr[tgt].values,te[tgt].values,tr.year.values,te.year.values
    R=[f'r{r}' for r in range(6)]
    sets={'v2_6idx':list(V2.columns),'lit':lit,'pcs':list(sp.columns)+list(up.columns)+list(pp.columns),'lit+pcs':lit+list(sp.columns)+list(up.columns)+list(pp.columns)}
    if tgt=='yter' and period.startswith('1981'):
        rows.append(dict(target=tgt,train=period,features='ONI JAS>=0.5 rule',model='rule',**ev(ye,(te.v2_oni.values>=0.5).astype(float),yre,0.5)))
    preds={}
    for nm,fs in sets.items():
        fs=fs+R; Xt,Xe=tr[fs].values,te[fs].values
        # choose C by year-block CV log-loss proxy (Brier) on train only
        best=min([0.003,0.01,0.03,0.1,1],key=lambda C:brier_score_loss(yt,oof(lambda:mk_lr(C),Xt,yt,yrt)))
        for mname,mk in [(f'LR(C={best})',lambda:mk_lr(best)),('GBM',mk_gbm)]:
            o=oof(mk,Xt,yt,yrt); t=thr(yt,o); p=mk().fit(Xt,yt).predict_proba(Xe)[:,1]; preds[(nm,mname[:3])]=(o,p)
            rows.append(dict(target=tgt,train=period,features=nm,model=mname,cv_auc=roc_auc_score(yt,o),**ev(ye,p,yre,t)))
    for nm in ['lit+pcs','lit']:
        o=(preds[(nm,'LR(')][0]+preds[(nm,'GBM')][0]+preds[('v2_6idx','LR(')][0])/3; p=(preds[(nm,'LR(')][1]+preds[(nm,'GBM')][1]+preds[('v2_6idx','LR(')][1])/3
        rows.append(dict(target=tgt,train=period,features=f'ensemble: {nm} LR+GBM + v2 LR',model='mean',cv_auc=roc_auc_score(yt,o),**ev(ye,p,yre,thr(yt,o))))
    print(tgt,period,'done',flush=True)
T=pd.DataFrame(rows)
def r3(v):
    if isinstance(v,(float,np.floating)): return round(float(v),3)
    if isinstance(v,list): return [r3(a) for a in v]
    return v
T=T.map(r3) if hasattr(T,'map') else T.applymap(r3)
T.drop(columns='per_year').to_csv(V+'/comparison_v3.csv',index=False)
json.dump({'rows':T.to_dict('records'),'gpcc_chirps_overlap_1981_2015':consist,'pca_explained_variance':explained,
  'literature_index_corr_with_kenya_yter_share_1981_2015':ond[ond.year<=2015].groupby('year').yter.mean().to_frame().join(L).corr()['yter'].round(3).to_dict()},open(V+'/metrics_v3.json','w'),indent=1,default=float)
pd.set_option('display.width',300); pd.set_option('display.max_colwidth',40)
print(consist); print(T[['target','train','features','model','cv_auc','acc','prec','rec','f1','auc','brier','auc_ci90','loyo_f1','yearcorr']].to_string())
