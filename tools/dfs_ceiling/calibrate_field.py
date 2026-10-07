"""Score a REAL contest field (DK standings csv) inside the simulated games and compare to reality.
usage: python calibrate_field.py <data_dir> <standings.csv>
Reports: simulated vs real field score distribution, the sim's P(win) for real entries (do real top finishers rate
higher?), rank correlation, and where an optional --me user finished."""
import sys, csv, re, unicodedata, numpy as np
def norm(s): return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').lower().replace('.', '').strip()
D, path = sys.argv[1], sys.argv[2]
Z = np.load(f"{D}/sims.npz", allow_pickle=True); M = Z['M'].astype(np.float32)
names = [norm(str(x)) for x in Z['names']]; col = {n: i for i, n in enumerate(names)}
M = np.concatenate([M, np.zeros((M.shape[0], 1), np.float32)], 1); Zc = M.shape[1] - 1
ents, miss = [], set()
for r in csv.reader(open(path, encoding='utf-8-sig')):
    if not r or r[0] == 'Rank' or len(r) < 6 or not r[5]: continue
    m = re.match(r'CPT (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+)$', r[5])
    if not m: continue
    g = [norm(x) for x in m.groups()]; miss |= {x for x in g if x not in col}
    ids = [col.get(x, Zc) for x in g]; ents.append((ids[0], ids[1:], float(r[4]), r[2], int(r[0])))
N = len(ents); act = np.array([e[2] for e in ents])
print(f"{N} real entries; {len(miss)} drafted names without a sim column (score 0 in sims): {sorted(miss)[:8]}")
S = np.empty((M.shape[0], N), np.float32)
for j, (c, u, *_ ) in enumerate(ents): S[:, j] = 1.5 * M[:, c] + M[:, u].sum(1)
fmax = S.max(1)
print(f"field median   real {np.median(act):5.1f} | sim {np.median(S, axis=1).mean():5.1f}")
print(f"field p90      real {np.percentile(act, 90):5.1f} | sim {np.percentile(S, 90, axis=1).mean():5.1f}")
print(f"winning score  real {act.max():5.1f} | sim median {np.median(fmax):5.1f}  (P(sim winner <= real) = {(fmax <= act.max()).mean():.2f})")
pw = (S >= fmax[:, None]).mean(0)
order = np.argsort(-act)
k = max(int(N * .01), 5)
print(f"sim P(win): average entry {100 * pw.mean():.3f}% | real top-1% finishers {100 * pw[order[:k]].mean():.3f}% | real top-10 {100 * pw[order[:10]].mean():.3f}% | bottom half {100 * pw[order[N // 2:]].mean():.3f}%")
rk = lambda a: np.argsort(np.argsort(a))
print(f"rank correlation (real points vs sim P(win)): {np.corrcoef(rk(act), rk(pw))[0, 1]:.2f}  | vs sim mean score: {np.corrcoef(rk(act), rk(S.mean(0)))[0, 1]:.2f}")
for e in ents:
    if 'devintyler' in e[3]: print(f"ours: rank {e[4]}/{N}  actual {e[2]}")
