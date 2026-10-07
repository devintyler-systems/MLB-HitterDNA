"""Structure of top finishers vs the field in a DK Showdown standings export (captain type, #pitchers, team splits,
ownership, captain-ownership lift) and where the user finished. usage: python field_study.py <standings.csv> <game_pk> [label]"""
import csv, re, json, sys, unicodedata, collections, urllib.request
import numpy as np
def norm(s): return ''.join(c for c in unicodedata.normalize('NFD',s) if unicodedata.category(c)!='Mn').lower().replace('.','').strip()
def box(pk):
    f=json.load(urllib.request.urlopen(f"https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live",timeout=30))
    role={};team={}
    for side in ('away','home'):
        t=f['liveData']['boxscore']['teams'][side]; ab=f['gameData']['teams'][side]['abbreviation']
        for p in t['players'].values():
            n=norm(p['person']['fullName']); team[n]=ab
            if p['person']['id'] in t['pitchers']: role[n]='P'
            elif p['stats'].get('batting',{}).get('plateAppearances',0)>0 or p.get('battingOrder'): role.setdefault(n,'H')
    return role,team
def load(path):
    ents=[]
    for r in csv.reader(open(path,encoding='utf-8-sig')):
        if not r or r[0]=='Rank' or len(r)<6 or not r[5]: continue
        m=re.match(r'CPT (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+)$',r[5])
        if not m: continue
        ents.append(dict(rank=int(r[0]),user=r[2],pts=float(r[4]),cpt=norm(m.group(1)),utl=[norm(m.group(i)) for i in range(2,7)]))
    return ents
def study(name,path,pk,ours_user='devintyler.vogel'):
    role,team=box(pk); E=load(path); N=len(E)
    tot=collections.Counter(); cp=collections.Counter()
    for e in E:
        cp[e['cpt']]+=1; tot[e['cpt']]+=1
        for u in e['utl']: tot[u]+=1
    own={n:tot[n]/N for n in tot}
    pts=np.array([e['pts'] for e in E]); order=np.argsort(-pts)
    print(f"\n===== {name}: {N} entries, winner {pts.max()}, median {np.median(pts)}, p90 {np.percentile(pts,90)}")
    def feats(e):
        six=[e['cpt']]+e['utl']
        npit=sum(1 for p in six if role.get(p)=='P')
        tms=collections.Counter(team.get(p,'?') for p in six)
        return dict(cpt_pitcher=role.get(e['cpt'])=='P',npit=npit,split=tuple(sorted(tms.values(),reverse=True)),own=np.mean([own[p] for p in six]),cptown=cp[e['cpt']]/N,
                    chalkP=max((own[p] for p in six if role.get(p)=='P'),default=0))
    F=[feats(e) for e in E]
    def summarize(idx,label):
        sub=[F[i] for i in idx]
        print(f"  {label:14} n={len(idx):5}  CPT=pitcher {100*np.mean([f['cpt_pitcher'] for f in sub]):4.0f}%  avg#pitchers {np.mean([f['npit'] for f in sub]):.2f}  avg own/player {100*np.mean([f['own'] for f in sub]):4.1f}%  median CPT own {100*np.median([f['cptown'] for f in sub]):4.1f}%  with chalk-SP(>40%) {100*np.mean([f['chalkP']>.4 for f in sub]):3.0f}%")
        sp=collections.Counter(f['split'] for f in sub); print('      team splits:',', '.join(f"{k}:{100*v/len(sub):.0f}%" for k,v in sp.most_common(4)))
    summarize(range(N),'ALL ENTRIES')
    k1=max(int(N*.01),5); k01=max(int(N*.001),3)
    summarize(list(order[:k1]),f'TOP 1% ({k1})'); summarize(list(order[:k01]),f'TOP 0.1% ({k01})')
    print('  top-10 lineups CPT:',[(E[i]['cpt'],E[i]['pts'],round(100*cp[E[i]['cpt']]/N,1)) for i in order[:10]])
    # lift by captain
    top=set(order[:k1].tolist()); c_top=collections.Counter(E[i]['cpt'] for i in top)
    rows=[(n,cp[n]/N,c_top[n]/len(top),role.get(n,'?')) for n in cp if cp[n]/N>=0.015]
    print('  captain: field% -> top1% share (lift) for CPT choices with >=1.5% field share')
    for n,f_,t_,r_ in sorted(rows,key=lambda x:-x[1])[:12]: print(f"     {n:22}{r_} field {100*f_:5.1f}% top1% {100*t_:5.1f}%  lift {t_/f_:4.1f}x")
    # where ours finished
    for i,e in enumerate(E):
        if ours_user in e['user']:
            f=F[i]; print(f"  OURS rank {e['rank']}/{N} ({100*e['rank']/N:.0f}th pct from top) pts {e['pts']}  CPT {e['cpt']} (own {100*f['cptown']:.1f}%)  split {f['split']} avg own {100*f['own']:.1f}% #pitchers {f['npit']}")
    return E,F,role,team,own
if __name__=='__main__':
    # usage: python field_study.py <standings.csv> <game_pk> [label]  -> structure of top finishers vs the field
    study(sys.argv[3] if len(sys.argv)>3 else sys.argv[2],sys.argv[1],int(sys.argv[2]))
    sys.exit(0)
    U='/root/.claude/uploads/273e52b1-5352-504c-95e8-e9e13ee03d14/'
    study('2026-10-06 MIL@SD $10K',U+'f54ec510-DKLineups10-7-26.csv',849826)
    study('2026-09-30 CHC@SD $8K Mini-Max',U+'e4a7b4f9-Showdown8KMini-Max-9-30_contest-standings-196220266.csv',849842)
