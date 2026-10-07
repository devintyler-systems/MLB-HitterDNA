"""Historical calibration backtest: simulate every FINAL game in a date range from pre-game-style inputs and compare each
player's actual DK points with the simulated distribution (hitters + starting pitchers).
usage: python backtest_games.py <start YYYY-MM-DD> <end YYYY-MM-DD> <out_dir> [--sims 3000] [--workers 4]
Caveats (read them): season/platoon stats are fetched as-of-today, so a past game's own result is inside its inputs
(mild look-ahead); BetLogic arsenal fit is unavailable historically (neutral); park factors are neutral (1.0);
relievers are generic. These make the backtest slightly optimistic about accuracy, not pessimistic.
Output: <out_dir>/games.jsonl (one row per game) and <out_dir>/players.jsonl (one row per player-game).
"""
import sys, os, json, subprocess, argparse, urllib.request, concurrent.futures as cf
from datetime import date, timedelta
import numpy as np
ap = argparse.ArgumentParser(); ap.add_argument('start'); ap.add_argument('end'); ap.add_argument('out')
ap.add_argument('--sims', type=int, default=3000); ap.add_argument('--workers', type=int, default=4)
ap.add_argument('--offense-scale', default='1.0'); ap.add_argument('--sig-scale', default='1.0'); ap.add_argument('--reuse', action='store_true', help='reuse existing per-game events.json (only re-simulate)')
a = ap.parse_args()
here = os.path.dirname(os.path.abspath(__file__)); OUT = a.out; os.makedirs(OUT, exist_ok=True)
env = {**os.environ, 'DFS_CACHE': f"{OUT}/cache", 'DFS_OFFENSE_SCALE': a.offense_scale, 'DFS_SIG_SCALE': a.sig_scale}

def jget(u):
    for _ in range(3):
        try: return json.load(urllib.request.urlopen(u, timeout=40))
        except Exception: pass
    return None

def games():
    d = date.fromisoformat(a.start); e = date.fromisoformat(a.end); out = []
    while d <= e:
        s = jget(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d}")
        for dt in (s or {}).get('dates', []):
            for g in dt['games']:
                if g['status']['abstractGameState'] == 'Final' and g['gameType'] in 'RFDLWC' and g['status']['detailedState'] in ('Final', 'Game Over'):
                    out.append((g['gamePk'], str(d), g['gameType']))
        d += timedelta(days=1)
    return out

def actual_points(feed):
    pts = {}
    for side in ('away', 'home'):
        for p in feed['liveData']['boxscore']['teams'][side]['players'].values():
            nm = p['person']['fullName']; b = p['stats'].get('batting') or {}; pi = p['stats'].get('pitching') or {}
            if b.get('plateAppearances', 0) > 0:
                h, d_, t, hr = b['hits'], b['doubles'], b['triples'], b['homeRuns']
                pts[nm] = 3 * (h - d_ - t - hr) + 5 * d_ + 8 * t + 10 * hr + 2 * b['rbi'] + 2 * b['runs'] + 2 * b['baseOnBalls'] + 2 * b.get('hitByPitch', 0) + 5 * b['stolenBases']
            ip = pi.get('inningsPitched')
            if ip and ip != '0.0':
                w, fr = ip.split('.'); outs = 3 * int(w) + int(fr)
                pts[nm + ' (P)'] = .75 * outs + 2 * pi['strikeOuts'] + (4 if pi.get('wins') else 0) - 2 * pi['earnedRuns'] - .6 * (pi['hits'] + pi['baseOnBalls'] + pi.get('hitBatsmen', 0))
    return pts

def work(g):
    pk, d, gt = g; D = f"{OUT}/g{pk}"; os.makedirs(D, exist_ok=True)
    try:
        feed = jget(f"https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"); gd = feed['gameData']; bx = feed['liveData']['boxscore']['teams']
        cfgp = f"{D}/config.json"
        if not os.path.exists(cfgp):
            cfg = {"date": d, "game_pk": pk, "park": gd['venue']['name'], "park_factors": {"h": 1.0, "hr": 1.0, "r": 1.0}, "extra_hitters": [], "bvp": {}, "bulk_relievers": [],
                   "relievers": {}}
            for side in ('away', 'home'):
                ab = gd['teams'][side]['abbreviation']; pids = bx[side]['pitchers']
                cfg[side] = {"team_id": gd['teams'][side]['id'], "abbr": ab, "starter_id": pids[0], "starter_bf_mean_sd": "auto", "team_pa": 37.9 if side == 'away' else 36.9}
                cfg['relievers'][ab] = {f"{ab} RP{i}": 0.5 for i in range(1, 9)}
            json.dump(cfg, open(cfgp, 'w'))
        if not (a.reuse and os.path.exists(f"{D}/events.json")):
            for cmd in (['fetch_inputs.py', cfgp, D], ['build_events.py', cfgp, D]):
                r = subprocess.run([sys.executable, f"{here}/{cmd[0]}", *cmd[1:]], env=env, capture_output=True, text=True)
                if r.returncode: return ('skip', pk, f"{cmd[0]} failed: {r.stderr[-200:]}")
        ev = json.load(open(f"{D}/events.json"))
        if len(ev['teams']) != 2: return ('skip', pk, 'lineup missing')
        r = subprocess.run([sys.executable, f"{here}/simulate.py", D, str(a.sims), '7'], env=env, capture_output=True, text=True)
        if r.returncode: return ('skip', pk, 'simulate failed ' + r.stderr[-200:])
        Z = np.load(f"{D}/sims.npz", allow_pickle=True); M = Z['M']; names = [str(x) for x in Z['names']]; kinds = [str(x) for x in Z['kinds']]
        act = actual_points(feed); rows = []
        tmap = {h['name']: (t, h['slot']) for t, v in ev['teams'].items() for h in v['hitters']}
        for j, nm in enumerate(names):
            if kinds[j] == 'H': key = nm
            elif kinds[j] == 'P': key = nm + ' (P)'
            else: continue
            if key not in act: continue
            col = M[:, j]; x = act[key]
            rows.append(dict(game=pk, date=d, gt=gt, name=nm, kind=kinds[j], slot=tmap.get(nm, (None, 0))[1], actual=x, sim_mean=float(col.mean()), sim_sd=float(col.std()),
                             pit=float((col < x).mean() + .5 * (col == x).mean()), p10=float(np.percentile(col, 10)), p90=float(np.percentile(col, 90)),
                             p_ge10=float((col >= 10).mean()), p_ge17=float((col >= 17).mean()), p_ge24=float((col >= 24).mean()), p_zero=float((col <= 0).mean())))
        tr = Z['total_runs']; real_tot = feed['liveData']['linescore']['teams']['away']['runs'] + feed['liveData']['linescore']['teams']['home']['runs']
        grow = dict(game=pk, date=d, gt=gt, sim_total_mean=float(tr.mean()), sim_total_sd=float(tr.std()), real_total=real_tot, total_pit=float((tr < real_tot).mean() + .5 * (tr == real_tot).mean()))
        return ('ok', pk, rows, grow)
    except Exception as e:
        return ('skip', pk, f"exception {e!r}")

if __name__ == '__main__':
    G = games(); print(f"{len(G)} final games from {a.start} to {a.end}", flush=True)
    ok = skip = 0
    with open(f"{OUT}/players.jsonl", 'w') as fp, open(f"{OUT}/games.jsonl", 'w') as fg, cf.ThreadPoolExecutor(a.workers) as ex:
        for res in ex.map(work, G):
            if res[0] == 'ok':
                ok += 1
                for r in res[2]: fp.write(json.dumps(r) + '\n')
                fg.write(json.dumps(res[3]) + '\n'); fp.flush(); fg.flush()
            else:
                skip += 1; print('skip', res[1], res[2], flush=True)
            if (ok + skip) % 10 == 0: print(f"  {ok + skip}/{len(G)} done ({ok} ok, {skip} skipped)", flush=True)
    print(f"finished: {ok} games ok, {skip} skipped -> {OUT}")
