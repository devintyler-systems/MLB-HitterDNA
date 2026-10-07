"""Derive reliever-usage weights from the series' box scores (days of rest, pitch counts) and season roles. usage: python build_pen.py <slate.json>
Writes cfg['relievers'][ABBR] = {name: weight} and cfg['bulk_relievers']. Weight ~ P(appears) = role base x availability multiplier.
Role: closer (>=10 SV) .60, setup (>=8 HLD) .55, middle .40, long man (IP/G>=1.8) .30 (bulk), starters who already started this series .05.
Availability: threw yesterday: x.55 (>=25 pitches: x.35; >=35: x.20); threw two straight days: x.30 more; threw 3 of last 3 days: x.10.
These are judgment weights, not fitted: reliever usage is the least validated part of the pipeline."""
import json, sys, urllib.request
from datetime import date, timedelta
p = sys.argv[1]; cfg = json.load(open(p)); today = date.fromisoformat(cfg['date'])
def jget(u):
    for _ in range(3):
        try: return json.load(urllib.request.urlopen(u, timeout=40))
        except Exception: pass
    return {}
usage = {}   # pid -> {days_ago: pitches}
started = set()
for side in ('away', 'home'):
    tid = cfg[side]['team_id']
    sch = jget(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={today - timedelta(days=6)}&endDate={today - timedelta(days=1)}&teamId={tid}&gameType=D,F,L,W")
    for dt in sch.get('dates', []):
        for g in dt['games']:
            if g['status']['abstractGameState'] != 'Final': continue
            f = jget(f"https://statsapi.mlb.com/api/v1.1/game/{g['gamePk']}/feed/live"); gd = f['gameData']
            for s in ('away', 'home'):
                if gd['teams'][s]['id'] != tid: continue
                t = f['liveData']['boxscore']['teams'][s]
                for n, pid in enumerate(t['pitchers']):
                    ds = (today - date.fromisoformat(dt['date'])).days
                    np_ = t['players'][f'ID{pid}']['stats']['pitching'].get('numberOfPitches', 0)
                    usage.setdefault(pid, {})[ds] = usage.get(pid, {}).get(ds, 0) + np_
                    if n == 0: started.add(pid)
cfg['relievers'] = {}; bulk = []
for side in ('away', 'home'):
    ab, tid = cfg[side]['abbr'], cfg[side]['team_id']
    ros = jget(f"https://statsapi.mlb.com/api/v1/teams/{tid}/roster?rosterType=active&season={today.year}").get('roster', [])
    ids = [r['person']['id'] for r in ros if r['position']['abbreviation'] == 'P' and r['person']['id'] != cfg[side]['starter_id']]
    st = jget("https://statsapi.mlb.com/api/v1/people?personIds=" + ','.join(map(str, ids)) + f"&hydrate=stats(group=[pitching],type=[season],season={today.year},gameType=R)").get('people', [])
    W = {}
    for pp in st:
        sp = ((pp.get('stats') or [{}])[0].get('splits') or [{}])[0].get('stat', {})
        g_, gs, sv, hld = (int(sp.get(k, 0)) for k in ('gamesPlayed', 'gamesStarted', 'saves', 'holds')); ip = float(str(sp.get('inningsPitched', '0') or '0'))
        if g_ < 5: continue
        pid = pp['id']; relief = g_ - gs
        if gs >= 8 and relief < 10: base, role = (.05 if pid in started else .03), 'starter'
        elif sv >= 10: base, role = .60, 'closer'
        elif hld >= 8: base, role = .55, 'setup'
        elif relief and (ip / max(g_, 1)) >= 1.8: base, role = .30, 'long'
        else: base, role = .40, 'middle'
        u = usage.get(pid, {}); m = 1.0
        if 1 in u:
            m *= .20 if u[1] >= 35 else .35 if u[1] >= 25 else .55
            if 2 in u: m *= .30
            if 2 in u and 3 in u: m *= .35
        elif 2 in u and u[2] >= 30: m *= .85
        w = round(min(base * m, .9), 3)
        if w >= .02: W[pp['fullName']] = w
        if role in ('long', 'starter'): bulk.append(pp['fullName'])
    cfg['relievers'][ab] = dict(sorted(W.items(), key=lambda kv: -kv[1])[:16])
cfg['bulk_relievers'] = bulk
json.dump(cfg, open(p, 'w'), indent=1)
for ab, w in cfg['relievers'].items(): print(ab, w)
