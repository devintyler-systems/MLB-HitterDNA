"""Download every raw input for one game into <out>/raw. Sources: MLB statsapi, Baseball Savant CSV exports,
BetLogic pitcher cards (per-pitch-type raw numbers only; its grades/odds fields are never read).
usage: python fetch_inputs.py slates/<slate>.json <out_dir>
"""
import json, sys, os, urllib.request, concurrent.futures as cf
from datetime import date, timedelta

cfg = json.load(open(sys.argv[1])); out = sys.argv[2]
os.makedirs(f"{out}/raw", exist_ok=True)
d0 = date.fromisoformat(cfg['date']); season = d0.year
UA = {"User-Agent": "Mozilla/5.0"}

import hashlib
CACHE = os.environ.get("DFS_CACHE")  # optional disk cache keyed by URL (used by backtest_games.py to share fetches)
if CACHE: os.makedirs(CACHE, exist_ok=True)
def get(u, hdr=None, raw=False):
    err = None
    cp = f"{CACHE}/{hashlib.md5(u.encode()).hexdigest()}" if CACHE and "betlogic" not in u else None
    if cp and os.path.exists(cp):
        body = open(cp, "rb").read()
        return body if raw else json.loads(body)
    for _ in range(3):
        try:
            req = urllib.request.Request(u, headers={**UA, **(hdr or {})})
            body = urllib.request.urlopen(req, timeout=40).read()
            out = body if raw else json.loads(body)
            if cp: open(cp, "wb").write(body)
            return out
        except Exception as e:
            err = e
    return {"error": str(err), "u": u}

# lineups from the official game feed (batting order is empty until posted)
feed = get(f"https://statsapi.mlb.com/api/v1.1/game/{cfg['game_pk']}/feed/live")
lineups = {}
for side in ('away', 'home'):
    t = feed['liveData']['boxscore']['teams'][side]
    # starters carry battingOrder codes 100,200,...,900 (substitutes 101, 201, ...), so this is the STARTING nine
    # even after the game has been played; before lineups post the list is empty.
    st = sorted((int(p['battingOrder']), p) for p in t['players'].values() if p.get('battingOrder') and int(p['battingOrder']) % 100 == 0)
    lineups[side] = [dict(id=p['person']['id'], slot=c // 100, pos=p['allPositions'][0]['abbreviation'] if p.get('allPositions') else p['position']['abbreviation'],
                          name=p['person']['fullName']) for c, p in st]
    if len(st) != 9:
        print(f"WARNING: {side} lineup not posted ({len(st)} hitters) - projections for that side will be missing")
json.dump(dict(lineups=lineups, status=feed['gameData']['status']['detailedState'], weather=feed['gameData'].get('weather')),
          open(f"{out}/lineups.json", "w"), indent=1)

hit_ids = {h['id'] for s in lineups.values() for h in s} | set(cfg.get('extra_hitters', []))
pit_ids = [cfg['away']['starter_id'], cfg['home']['starter_id']]
teams = [cfg['away']['team_id'], cfg['home']['team_id']]

SV = "https://baseballsavant.mlb.com/leaderboard"
sav = {
 "sv_bat_xstats": f"{SV}/expected_statistics?type=batter&year={season}&position=&team=&filterType=bip&min=25&csv=true",
 "sv_pit_xstats": f"{SV}/expected_statistics?type=pitcher&year={season}&position=&team=&filterType=bip&min=25&csv=true",
 "sv_bat_ev": f"{SV}/statcast?type=batter&year={season}&position=&team=&min=25&csv=true",
 "sv_pit_ev": f"{SV}/statcast?type=pitcher&year={season}&position=&team=&min=25&csv=true",
 "sv_sprint": f"{SV}/sprint_speed?min_season={season}&max_season={season}&position=&team=&min=10&csv=true",
}
for name, u in sav.items():
    open(f"{out}/raw/{name}.csv", "wb").write(get(u, raw=True))

for p in pit_ids:  # BetLogic card: raw per-pitch-type arsenal + opposing batter grids
    card = get(f"https://betlogic.ai/matchup-proxy.php?pitcher={p}&v=7", {"Referer": "https://betlogic.ai/mlb/matchup"})
    if 'pitcher' not in card:  # served only for current probables; keep a previously saved card, else model runs without arsenal fit
        print(f"WARNING: no BetLogic card for pitcher {p} ({card.get('error')}); arsenal-fit adjustment will be neutral unless raw/p_{p}.json already exists")
        if os.path.exists(f"{out}/raw/p_{p}.json"): continue
    json.dump(card, open(f"{out}/raw/p_{p}.json", "w"))

S = f"https://statsapi.mlb.com/api/v1/people/%d/stats?stats=%s&group=%s&season={season}"
w_start, w_end = (d0 - timedelta(days=14)).isoformat(), (d0 - timedelta(days=1)).isoformat()
def hf(i):
    return str(i), dict(
        bio=get(f"https://statsapi.mlb.com/api/v1/people/{i}")['people'][0],
        season=get(S % (i, 'season', 'hitting') + "&gameType=R"),
        d14=get(f"https://statsapi.mlb.com/api/v1/people/{i}/stats?stats=byDateRange&group=hitting&startDate={w_start}&endDate={w_end}&gameType=R"),
        spl=get(S % (i, 'statSplits', 'hitting') + "&sitCodes=vl,vr&gameType=R"),
        log=get(S % (i, 'gameLog', 'hitting') + "&gameType=R"))
def pf(i):
    return str(i), dict(
        bio=get(f"https://statsapi.mlb.com/api/v1/people/{i}")['people'][0],
        season=get(S % (i, 'season', 'pitching') + "&gameType=R"),
        log=get(S % (i, 'gameLog', 'pitching') + "&gameType=R"),
        spl=get(S % (i, 'statSplits', 'pitching') + "&sitCodes=vl,vr&gameType=R"))
def tf(t):
    return str(t), dict(pit=get(f"https://statsapi.mlb.com/api/v1/teams/{t}/stats?stats=statSplits&group=pitching&season={season}&sitCodes=rp,sp&gameType=R"))
with cf.ThreadPoolExecutor(12) as ex:
    H = dict(ex.map(hf, sorted(hit_ids))); P = dict(ex.map(pf, pit_ids)); T = dict(ex.map(tf, teams))
json.dump(dict(hit=H, pit=P, team=T), open(f"{out}/raw/stats.json", "w"))
print("fetched", len(H), "hitters,", len(P), "pitchers,", len(T), "teams ->", out)
