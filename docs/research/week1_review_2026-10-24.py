"""Week-1 actual vs projected, per fantasy team and player (2026-10-24 review). Run from repo root:
python docs/research/week1_review_2026-10-24.py [pre|ins]
Lineups = morning Yahoo roster captures (in-day swaps missed); stats = ESPN box scores."""
import json

import numpy as np
import pandas as pd

R=''
g=pd.read_csv(R+'data/raw/espn_boxscores/player_games_2027.csv.gz')
pre=pd.read_csv(R+'data/processed/projections_2026_27.csv').set_index('player_id')
ins=pd.read_csv(R+'data/processed/projections_2026_27.inseason.csv').set_index('player_id')
caps={'2026-10-20':'league_rosters_2026-10-20T0240Z','2026-10-21':'league_rosters_2026-10-21',
      '2026-10-22':'league_rosters_2026-10-22','2026-10-23':'league_rosters_2026-10-23'}
S=['fgm','fga','ftm','fta','fg3m','pts','reb','ast','stl','blk','tov']
rows=[]
for day,f in caps.items():
    d=json.load(open(R+f'data/raw/yahoo/{f}.json'))
    gd=g[g.game_date==day].set_index('player_id')
    for yt,t in d['teams'].items():
        for p in t['players']:
            pid=p['player_id']; active=p['slot'] not in ('BN','IL','IL+')
            has=pid in gd.index
            r=dict(day=day,yt=int(yt),team=t['name'],pid=pid,name=p['name'],slot=p['slot'],active=active,
                   nba_game=has, played=bool(has and gd.loc[pid,'played']==True))
            for s in S:
                r[s]=float(gd.loc[pid,s]) if r['played'] else 0.0
                r['pre_'+s]=pre[s].get(pid,np.nan) if has else 0.0   # proj per game, if their team had a game
                r['ins_'+s]=ins[s].get(pid,np.nan) if has else 0.0
            rows.append(r)
df=pd.DataFrame(rows)
df.to_csv('var/wk1_rows.csv',index=False)
import sys

sys.path.insert(0,'src')
from fantasy.model.noise import game_var

pd.set_option('display.width',250); pd.set_option('display.max_rows',200)
df=pd.read_csv('var/wk1_rows.csv')
names={1:'Shalom',2:'jesse',3:'Jeffrey',4:'Christian',5:'Joshua',6:'Andrew',7:'Haagen',8:'Scottier',9:'Justin',10:'Yeet',11:'ME',12:'Ed'}
df['tm']=df.yt.map(names)
P=sys.argv[1] if len(sys.argv)>1 else 'pre'
C=['pts','reb','ast','fg3m','stl','blk','tov']
a=df[df.active & df.nba_game].copy()
pl=a[a.played]
# team-level z per cat, over games actually played (separate availability)
out=[]
for tm,x in pl.groupby('tm'):
    r={'tm':tm,'GP':len(x),'missed':int((~a[a.tm==tm].played).sum())}
    for c in C:
        res=x[c].sum()-x[f'{P}_{c}'].sum(); sd=np.sqrt(game_var(c,x[f'{P}_{c}']).sum())
        r[c]=f"{x[c].sum():.0f}/{x[f'{P}_{c}'].sum():.0f} z{res/sd:+.1f}"
    for m,att in [('fgm','fga'),('ftm','fta')]:
        r[m[:2]+'%']=f"{x[m].sum()/x[att].sum():.3f}/{x[f'{P}_{m}'].sum()/x[f'{P}_{att}'].sum():.3f}"
    out.append(r)
print(pd.DataFrame(out).set_index('tm').to_string())
allz=[]
for tm,x in pl.groupby('tm'):
    allz.append((x.pts.sum()-x[f'{P}_pts'].sum())/np.sqrt(game_var('pts',x[f'{P}_pts']).sum()))
print('league pts residual total', round(pl.pts.sum()-pl[f'{P}_pts'].sum()), 'of', round(pl[f'{P}_pts'].sum()),' mean z', np.mean(allz).round(2),' mean z^2',np.mean(np.square(allz)).round(2))
for tm in ['Shalom','ME']:
    x=a[a.tm==tm]
    g=x.groupby('name').agg(G=('played','sum'),sched=('nba_game','sum'),min=('pts','size'),
        pts=('pts','sum'),reb=('reb','sum'),ast=('ast','sum'),fg3m=('fg3m','sum'),fgm=('fgm','sum'),fga=('fga','sum'))
    xp=x[x.played].groupby('name')[[f'{P}_{c}' for c in ['pts','reb','ast','fg3m','fgm','fga']]].sum()
    g=g.join(xp).fillna(0)
    g['pts_res']=g.pts-g[f'{P}_pts']
    g['z']=g.pts_res/np.sqrt(x[x.played].groupby('name')[f'{P}_pts'].apply(lambda m: game_var('pts',m).sum())).reindex(g.index).replace(0,np.nan)
    g['missed_proj_pts']=x[~x.played].groupby('name')[f'{P}_pts'].sum().reindex(g.index).fillna(0)
    g['fg']=(g.fgm.astype(int).astype(str)+'/'+g.fga.astype(int).astype(str))
    g['proj_fg']=(g[f'{P}_fgm']/g[f'{P}_fga'].replace(0,np.nan)).round(3)
    print('\n',tm); print(g[['G','sched','pts',f'{P}_pts','pts_res','z','missed_proj_pts','reb',f'{P}_reb','ast',f'{P}_ast','fg3m',f'{P}_fg3m','fg','proj_fg']].round(1).sort_values('pts_res').to_string())
    bench=df[(df.tm==tm)&(~df.active)&df.played]
    print(' bench pts left:',bench.pts.sum(), bench.groupby('name').pts.sum().to_dict())
