"""Grid-search the two calibration knobs (DFS_OFFENSE_SCALE, DFS_SIG_SCALE) against a backtest_games.py run.
usage: python fit_calibration.py <bt_dir> [--games 80] [--sims 1500] [--workers 6] [--off 0.9,0.95,1.0,1.05] [--sig 0.8,1.0,1.2,1.4]
Re-simulates the saved games under each setting (events are reused) and scores hitter-level calibration:
  loss = (log mean-ratio)^2 + mean over thresholds {>=10,>=17,>=24} of (log tail-ratio)^2 + (log zero-rate ratio)^2 + 0.5*chi2(PIT deciles)
Pick the setting with the lowest loss, but read the table: a flat valley means the data cannot choose, and
fitting on a few hundred games risks fitting luck. Check the chosen setting on games it was not fitted on."""
import sys, os, json, shutil, subprocess, argparse, itertools, tempfile, concurrent.futures as cf
import numpy as np
ap = argparse.ArgumentParser(); ap.add_argument('bt'); ap.add_argument('--games', type=int, default=80); ap.add_argument('--sims', type=int, default=1500)
ap.add_argument('--workers', type=int, default=6); ap.add_argument('--off', default='0.9,0.95,1.0,1.05'); ap.add_argument('--sig', default='0.8,1.0,1.2,1.4')
ap.add_argument('--offset', type=int, default=0, help='skip this many games first (use disjoint slices for fit vs holdout)')
a = ap.parse_args(); here = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(f"{a.bt}/players.jsonl")]
act = {}; 
for r in rows: act.setdefault(r['game'], {})[r['name']] = (r['actual'], r['kind'])
gids = sorted(act)[a.offset:a.offset + a.games]
def run(setting):
    off, sig = setting; ys = []; pits = []
    def one(pk):
        D = f"{a.bt}/g{pk}"; tmp = tempfile.mkdtemp(); shutil.copy(f"{D}/events.json", tmp)
        env = {**os.environ, 'DFS_OFFENSE_SCALE': str(off), 'DFS_SIG_SCALE': str(sig)}
        subprocess.run([sys.executable, f"{here}/simulate.py", tmp, str(a.sims), '11'], env=env, capture_output=True, check=True)
        Z = np.load(f"{tmp}/sims.npz", allow_pickle=True); M = Z['M']; names = [str(x) for x in Z['names']]; out = []
        for j, nm in enumerate(names):
            if nm in act[pk] and act[pk][nm][1] == 'H':
                col = M[:, j]; x = act[pk][nm][0]; out.append((x, col.mean(), (col >= 10).mean(), (col >= 17).mean(), (col >= 24).mean(), (col <= 0).mean(), (col < x).mean() + .5 * (col == x).mean()))
        shutil.rmtree(tmp); return out
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for o in ex.map(one, gids): ys += o
    Y = np.array(ys); x = Y[:, 0]
    mr = x.mean() / Y[:, 1].mean(); tails = [np.mean(x >= k) / Y[:, c].mean() for k, c in ((10, 2), (17, 3), (24, 4))]; zr = np.mean(x <= 0) / Y[:, 5].mean()
    dec = np.histogram(Y[:, 6], bins=10, range=(0, 1))[0]; chi = float(((dec - len(Y) / 10) ** 2 / (len(Y) / 10)).sum() / 9)
    loss = np.log(mr) ** 2 + np.mean([np.log(max(t, 1e-3)) ** 2 for t in tails]) + np.log(zr) ** 2 + .5 * chi / 10
    return dict(off=off, sig=sig, n=len(Y), mean_ratio=mr, tail10=tails[0], tail17=tails[1], tail24=tails[2], zero=zr, chi=chi, loss=loss)
grid = list(itertools.product([float(x) for x in a.off.split(',')], [float(x) for x in a.sig.split(',')]))
res = []
for s in grid:
    r = run(s); res.append(r)
    print(f"off {r['off']:.2f} sig {r['sig']:.2f} | n={r['n']} mean-ratio {r['mean_ratio']:.3f} tails>=10/17/24 {r['tail10']:.2f}/{r['tail17']:.2f}/{r['tail24']:.2f} zero-rate {r['zero']:.2f} PITchi {r['chi']:.2f} | loss {r['loss']:.4f}", flush=True)
b = min(res, key=lambda r: r['loss']); print(f"\nBEST: DFS_OFFENSE_SCALE={b['off']} DFS_SIG_SCALE={b['sig']}  (loss {b['loss']:.4f})")
json.dump(res, open(f"{a.bt}/fit_{a.offset}.json", 'w'), indent=1)
