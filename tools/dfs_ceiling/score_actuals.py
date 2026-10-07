"""Actual DK Showdown points for every player in a finished game.  usage: python score_actuals.py <game_pk> [out.json]"""
import json, sys, urllib.request
pk = sys.argv[1]
f = json.load(urllib.request.urlopen(f"https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live", timeout=30))
out = {}
for side in ('away', 'home'):
    for p in f['liveData']['boxscore']['teams'][side]['players'].values():
        nm = p['person']['fullName']; b = p['stats'].get('batting') or {}; pi = p['stats'].get('pitching') or {}
        if b.get('plateAppearances', 0) > 0 or b.get('atBats', 0) > 0:
            h, d, t, hr = b['hits'], b['doubles'], b['triples'], b['homeRuns']
            out[nm] = 3 * (h - d - t - hr) + 5 * d + 8 * t + 10 * hr + 2 * b['rbi'] + 2 * b['runs'] + 2 * b['baseOnBalls'] + 2 * b.get('hitByPitch', 0) + 5 * b['stolenBases']
        ip = pi.get('inningsPitched')
        if ip and ip != '0.0':
            w, fr = ip.split('.'); outs = 3 * int(w) + int(fr)
            win = 4 if pi.get('wins') else 0
            out[nm] = out.get(nm, 0) + .75 * outs + 2 * pi['strikeOuts'] + win - 2 * pi['earnedRuns'] - .6 * (pi['hits'] + pi['baseOnBalls'] + pi.get('hitBatsmen', 0))
for k, v in sorted(out.items(), key=lambda kv: -kv[1]): print(f"{k:24}{v:6.2f}")
if len(sys.argv) > 2: json.dump(out, open(sys.argv[2], 'w'), indent=1)
