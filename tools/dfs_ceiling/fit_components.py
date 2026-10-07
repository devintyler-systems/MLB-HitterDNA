"""Fit the box-score COMPONENT knobs so simulated game totals of hits, HR, walks, steals, runs and RBI match reality.
usage: python fit_components.py <bt_dir> [--games 150] [--offset 0] [--iters 5] [--sims 1500]
Multiplicative, damped updates on DFS_OFFENSE_SCALE (non-HR hits), DFS_HR_SCALE, DFS_BB_SCALE, DFS_SB_SCALE, DFS_ADV_SCALE
(runner advancement/scoring) using per-game component totals saved in sims.npz['comp'] and the real box-score totals
stored by backtest_games.py in games.jsonl. Always validate the result on games outside the fitted slice."""
import sys, os, json, shutil, subprocess, argparse, tempfile, concurrent.futures as cf
import numpy as np
ap = argparse.ArgumentParser(); ap.add_argument('bt'); ap.add_argument('--games', type=int, default=150); ap.add_argument('--offset', type=int, default=0)
ap.add_argument('--iters', type=int, default=5); ap.add_argument('--sims', type=int, default=1500); ap.add_argument('--workers', type=int, default=8)
a = ap.parse_args(); here = os.path.dirname(os.path.abspath(__file__))
G = {json.loads(l)['game']: json.loads(l) for l in open(f"{a.bt}/games.jsonl")}
gids = sorted(G)[a.offset:a.offset + a.games]
real = np.array([G[g]['real_comp'] for g in gids]).mean(0)   # H, HR, BB+HBP, SB, R, RBI
K = dict(off=0.966, hr=0.971, bb=0.957, sb=2.629, adv=1.217, wild=0.074)   # warm start from a previous fit; re-run from defaults if the data changes a lot
real_gap = float(np.mean([G[g]['real_comp'][4] - G[g]['real_comp'][5] for g in gids]))
def sim_means(K):
    def one(pk):
        tmp = tempfile.mkdtemp(); shutil.copy(f"{a.bt}/g{pk}/events.json", tmp)
        env = {**os.environ, 'DFS_OFFENSE_SCALE': str(K['off']), 'DFS_HR_SCALE': str(K['hr']), 'DFS_BB_SCALE': str(K['bb']), 'DFS_SB_SCALE': str(K['sb']), 'DFS_ADV_SCALE': str(K['adv']), 'DFS_WILD_RATE': str(K['wild'])}
        subprocess.run([sys.executable, f"{here}/simulate.py", tmp, str(a.sims), '5'], env=env, capture_output=True, check=True)
        c = np.load(f"{tmp}/sims.npz", allow_pickle=True)['comp'].mean(0); shutil.rmtree(tmp); return c
    with cf.ThreadPoolExecutor(a.workers) as ex: return np.mean(list(ex.map(one, gids)), axis=0)
names = ['hits', 'HR', 'BB+HBP', 'SB', 'runs', 'RBI']
for it in range(a.iters):
    sim = sim_means(K); r = real / sim
    print(f"iter {it}: knobs {K}\n        real/sim: " + '  '.join(f"{n} {x:.3f}" for n, x in zip(names, r)), flush=True)
    # hits ~ off (non-HR hits), HR ~ hr, BB ~ bb, SB ~ sb, runs ~ adv (after hits are right)
    nonhr = (real[0] - real[1]) / (sim[0] - sim[1])
    K['off'] *= nonhr ** .8; K['hr'] *= r[1] ** .8; K['bb'] *= r[2] ** .8; K['sb'] *= r[3] ** .8
    K['adv'] *= r[5] ** 1.0                                   # RBI total pins the advancement/scoring probabilities
    sim_gap = max(sim[4] - sim[5], 0.04); K['wild'] *= (real_gap / sim_gap) ** .9    # runs minus RBI pins the no-RBI-run rate
K = {k: round(float(v), 3) for k, v in K.items()}
sim = sim_means(K); print(f"FINAL knobs {K}\n   real/sim: " + '  '.join(f"{n} {x:.3f}" for n, x in zip(names, real / sim)))
json.dump(K, open(f"{a.bt}/component_fit_{a.offset}.json", 'w'))
print("export: " + ' '.join(f"{k}={v}" for k, v in {'DFS_OFFENSE_SCALE': K['off'], 'DFS_HR_SCALE': K['hr'], 'DFS_BB_SCALE': K['bb'], 'DFS_SB_SCALE': K['sb'], 'DFS_ADV_SCALE': K['adv'], 'DFS_WILD_RATE': K['wild']}.items()))
