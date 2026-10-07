"""Is the simulator's GAME-LEVEL (teammate-correlation) variance right? For each backtested game, locate the actual mean hitter DK points
(and each team's mean) inside the simulated distribution of the same quantity. Uniform positions => correct correlation structure.
usage: python game_level_check.py <bt_dir>"""
import sys, json, os
import numpy as np
D = sys.argv[1]
P = [json.loads(l) for l in open(f"{D}/players.jsonl")]
by = {}
for r in P:
    if r['kind'] == 'H': by.setdefault(r['game'], {})[r['name']] = r['actual']
pit_g, pit_t, zs, sd_ratio = [], [], [], []
for pk, act in by.items():
    f = f"{D}/g{pk}/sims.npz"
    if not os.path.exists(f): continue
    Z = np.load(f, allow_pickle=True); M = Z['M']; names = [str(x) for x in Z['names']]; kinds = [str(x) for x in Z['kinds']]; tms = [str(x) for x in Z['teams']]
    idx = [i for i, n in enumerate(names) if kinds[i] == 'H' and n in act]
    if len(idx) < 18: continue
    sim_mean = M[:, idx].mean(1); a = np.mean([act[names[i]] for i in idx])
    pit_g.append(float((sim_mean < a).mean() + .5 * (sim_mean == a).mean())); zs.append((a - sim_mean.mean()) / sim_mean.std()); sd_ratio.append(sim_mean.std())
    for tm in set(tms[i] for i in idx):
        ti = [i for i in idx if tms[i] == tm]; sm = M[:, ti].mean(1); at = np.mean([act[names[i]] for i in ti])
        pit_t.append(float((sm < at).mean() + .5 * (sm == at).mean()))
zs = np.array(zs)
print(f"{len(zs)} games. Actual game-mean hitter points vs the sim's own distribution:")
print(f"  z-score SD {zs.std():.2f} (1.00 = sim's game-level spread is right; >1 = sim too tight/overconfident, <1 = too loose); mean z {zs.mean():+.2f}")
for nm, p in (('whole game (18 hitters)', pit_g), ('single team (9 hitters)', pit_t)):
    h = np.histogram(p, bins=10, range=(0, 1))[0] / len(p)
    print(f"  {nm:26} PIT deciles {' '.join(f'{100*x:4.1f}' for x in h)}   share outside 10-90%: {100*np.mean((np.array(p)<.1)|(np.array(p)>.9)):.0f}% (expect 20%)  outside 2.5-97.5%: {100*np.mean((np.array(p)<.025)|(np.array(p)>.975)):.1f}% (expect 5%)")
print(f"  typical sim SD of game-mean hitter points: {np.mean(sd_ratio):.2f}")
