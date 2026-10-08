"""Fit the pre-lock ownership proxy used by optimize.py when no real/projected ownership is supplied.
usage: python fit_ownership.py <out.json> <standings.csv> <DKSalaries.csv> <sim_dir> [<standings.csv> <DKSalaries.csv> <sim_dir> ...]
Model: log(total ownership) ~ log(our simulated DK mean) + log(salary) + pitcher + top-3 slot + AvgPointsPerGame + cheap-regular flag,
ridge-regularized. Captain propensity ~ salary**k. Reports out-of-sample R2 when given >=2 slates (fit on one, test on another)."""
import sys, csv, re, unicodedata, collections, json
import numpy as np
def norm(s): return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').lower().replace('.', '').strip()
def load(field, sal, simdir):
    ents = []
    for r in csv.reader(open(field, encoding='utf-8-sig')):
        if not r or r[0] == 'Rank' or len(r) < 6 or not r[5]: continue
        m = re.match(r'CPT (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+)$', r[5]); ents.append([norm(x) for x in m.groups()])
    N = len(ents); tot = collections.Counter(x for e in ents for x in e); cp = collections.Counter(e[0] for e in ents)
    Z = np.load(simdir + '/sims.npz', allow_pickle=True); M = Z['M']; names = [norm(str(x)) for x in Z['names']]; kinds = [str(x) for x in Z['kinds']]
    sm = {n: float(M[:, i].mean()) for i, n in enumerate(names)}; kd = {n: kinds[i] for i, n in enumerate(names)}; out = []
    for r in csv.DictReader(open(sal, encoding='utf-8-sig')):
        n = norm(r['Name'])
        if r['Roster Position'] != 'UTIL' or n not in sm: continue
        s = int(r['Salary']); slot = int(r['Starting']) if r['Starting'].isdigit() else 0
        out.append(dict(n=n, f=feat(sm[n], s, kd[n] == 'P', slot, float(r['AvgPointsPerGame'] or 0)), own=max(tot[n] / N, .005), cpt=max(cp[n] / N, .002), sal=s))
    return out
def feat(sim, sal, isP, slot, avg):
    return [1, np.log(max(sim, .5)), np.log(sal / 1000), float(isP), float(0 < slot <= 3), avg / 10, float((not isP) and sal <= 4600 and slot > 0)]
def fit(X, y, lam=.3):
    A = X.T @ X + lam * np.eye(X.shape[1]); A[0, 0] -= lam; return np.linalg.solve(A, X.T @ y)
def r2(y, p): return 1 - ((y - p) ** 2).sum() / ((y - y.mean()) ** 2).sum()
if __name__ == '__main__':
    out = sys.argv[1]; a = sys.argv[2:]; D = [load(a[i], a[i + 1], a[i + 2]) for i in range(0, len(a), 3)]
    mats = [(np.array([r['f'] for r in d]), np.log([r['own'] for r in d])) for d in D]
    for i in range(len(D)):
        for j in range(len(D)):
            if i != j:
                b = fit(*mats[i]); p = mats[j][0] @ b; print(f"fit slate {i} -> test slate {j}: out-of-sample R2 {r2(mats[j][1], p):.2f}, corr {np.corrcoef(mats[j][1], p)[0, 1]:.2f}")
    X = np.vstack([m[0] for m in mats]); y = np.concatenate([m[1] for m in mats]); b = fit(X, y)
    cs = [(np.log(r['sal'] / 1000), np.log(r['cpt'] / r['own'])) for d in D for r in d if r['own'] > .1]; k = float(np.polyfit(*zip(*cs), 1)[0])
    print('pooled in-sample R2', round(r2(y, X @ b), 2), '| coef', b.round(2), '| captain-propensity salary exponent', round(k, 2))
    json.dump(dict(coef=b.tolist(), features=['1', 'log(sim DK mean)', 'log(util salary/1000)', 'is_pitcher', 'batting slot<=3', 'AvgPointsPerGame/10', 'cheap starting hitter (<=$4.6K)'],
                   cpt_salary_exp=k, n_slates=len(D)), open(out, 'w'), indent=1)
