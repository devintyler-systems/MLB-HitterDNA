"""Summarize a backtest_games.py run: is the simulator biased, and are its tails right?  usage: python calibration_report.py <bt_dir> [label]"""
import sys, json
import numpy as np
D = sys.argv[1]; label = sys.argv[2] if len(sys.argv) > 2 else D
P = [json.loads(l) for l in open(f"{D}/players.jsonl")]; G = [json.loads(l) for l in open(f"{D}/games.jsonl")]
if len(sys.argv) > 3:  # holdout: only games after the first N (sorted by game id) -- the slice fit_calibration.py did NOT see
    keep = set(sorted({g['game'] for g in G})[int(sys.argv[3]):]); P = [r for r in P if r['game'] in keep]; G = [g for g in G if g['game'] in keep]
def arr(rows, k): return np.array([r[k] for r in rows], float)
H = [r for r in P if r['kind'] == 'H']; PI = [r for r in P if r['kind'] == 'P']
print(f"=== {label}: {len(G)} games, {len(H)} hitter-games, {len(PI)} starter-games")
def block(rows, name):
    if not rows: return
    a, s, pit = arr(rows, 'actual'), arr(rows, 'sim_mean'), arr(rows, 'pit')
    se = a.std() / np.sqrt(len(a)); dec = np.histogram(pit, bins=10, range=(0, 1))[0] / len(pit)
    print(f"{name:26} n={len(rows):5}  actual mean {a.mean():5.2f} vs sim {s.mean():5.2f}  ratio {a.mean() / s.mean():.3f} (+/-{se / s.mean():.3f})  80% interval coverage {100 * np.mean((pit > .1) & (pit < .9)):4.0f}%  PIT<.1/>.9: {100 * np.mean(pit < .1):.0f}%/{100 * np.mean(pit > .9):.0f}%")
block(H, 'hitters (all)'); block(PI, 'starting pitchers (all)')
for gt, nm in (('R', 'regular season'), ('F', 'wild card'), ('D', 'division series')):
    block([r for r in H if r['gt'] == gt], f'  hitters, {nm}'); block([r for r in PI if r['gt'] == gt], f'  pitchers, {nm}')
for lo, hi in ((1, 2), (3, 5), (6, 9)): block([r for r in H if lo <= r['slot'] <= hi], f'  hitters slots {lo}-{hi}')
print("\nPIT deciles, hitters (flat = 10% each):", ' '.join(f"{100 * x:4.1f}" for x in np.histogram(arr(H, 'pit'), bins=10, range=(0, 1))[0] / len(H)))
print("PIT deciles, pitchers               :", ' '.join(f"{100 * x:4.1f}" for x in np.histogram(arr(PI, 'pit'), bins=10, range=(0, 1))[0] / max(len(PI), 1)))
a = arr(H, 'actual')
print("\nTAIL FREQUENCY, hitters (share of hitter-games)   observed | simulated")
for k, f in ((0, 'p_zero'), (10, 'p_ge10'), (17, 'p_ge17'), (24, 'p_ge24')):
    obs = np.mean(a <= 0) if k == 0 else np.mean(a >= k); print(f"   {'<= 0 pts' if k == 0 else f'>= {k} pts':10} {100 * obs:6.2f}% | {100 * arr(H, f).mean():6.2f}%   ratio {obs / arr(H, f).mean():.2f}")
tp = arr(G, 'total_pit'); print(f"\nGAME TOTAL RUNS: real mean {arr(G, 'real_total').mean():.2f} vs sim {arr(G, 'sim_total_mean').mean():.2f}; PIT deciles", ' '.join(f"{100 * x:4.1f}" for x in np.histogram(tp, bins=10, range=(0, 1))[0] / len(tp)), f"| real SD {arr(G, 'real_total').std():.2f} vs sim SD {arr(G, 'sim_total_sd').mean():.2f}")

if 'sim_comp' in G[0]:
    S_ = np.array([g['sim_comp'] for g in G]); R_ = np.array([g['real_comp'] for g in G])
    print("\nCOMPONENTS per game (both teams, whole box score)   real | sim | ratio")
    for i, n in enumerate(['hits', 'home runs', 'BB+HBP', 'stolen bases', 'runs', 'RBI']): print(f"   {n:13} {R_[:, i].mean():6.2f} | {S_[:, i].mean():6.2f} | {R_[:, i].mean() / S_[:, i].mean():.3f}")
