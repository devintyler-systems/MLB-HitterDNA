"""Ceiling optimizer for DraftKings MLB Showdown.

Maximizes the probability a lineup finishes FIRST (or top-N%) in the simulated field, not its average score.

usage: python optimize.py <data_dir> <DKSalaries.csv> [--field 2378] [--objective win|top1|top5] [--portfolio 1]
                          [--starts 150] [--out lineups.json]
reads  <data_dir>/sims.npz (simulate.py)
How it works
  1. Every simulated game gives every player a DK point total (same game => teammates are correlated).
  2. A synthetic field of --field public-style lineups (weighted by DK AvgPointsPerGame, near-cap, both teams) is
     scored inside the SAME simulated games, giving the best/99th/95th-percentile field score in each game.
  3. A lineup's objective = share of simulated games in which it beats that field benchmark. Search = multi-start
     greedy swaps on a TRAIN half of the sims; results are re-measured on a held-out TEST half (no overfit to noise).
  4. --portfolio k picks k lineups maximizing the chance that AT LEAST ONE of them wins (multi-entry diversification).
Captain scores 1.5x and costs the CPT price; 6 players, both teams, <= $50,000.
"""
import argparse, csv, json, unicodedata
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument('data_dir'); ap.add_argument('salaries')
ap.add_argument('--field', type=int, default=2378)
ap.add_argument('--objective', default='win', choices=['win', 'top1', 'top5'])
ap.add_argument('--portfolio', type=int, default=1)
ap.add_argument('--starts', type=int, default=150)
ap.add_argument('--seed', type=int, default=11)
ap.add_argument('--out', default=None)
ap.add_argument('--own-power', type=float, default=3.0, help='public pick weight ~ AvgPointsPerGame**p')
ap.add_argument('--cpt-salary-power', type=float, default=2.5, help='captain propensity ~ UTIL salary**p (public captains the upside)')
ap.add_argument('--ownership', default=None, help='csv name,cpt_pct,util_pct (projected/actual ownership) overrides the proxy')
ap.add_argument('--field-lineups', default=None, help='DK contest-standings lineups csv (Rank,..,Points,Lineup,...): score the REAL field in the sims')
ap.add_argument('--allow-relievers', action='store_true', help='let relievers into lineups (default off: their usage in the sim is a guess the optimizer would exploit)')
ap.add_argument('--exclude', default='', help='comma-separated names to exclude (e.g. late scratches)')
a = ap.parse_args()
rng = np.random.default_rng(a.seed)
CAP = 50000

def norm(s): return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').lower().replace('.', '').strip()

Z = np.load(f"{a.data_dir}/sims.npz", allow_pickle=True)
M = Z['M'].astype(np.float32); names = [str(x) for x in Z['names']]; teams = [str(x) for x in Z['teams']]; kinds = [str(x) for x in Z['kinds']]
col = {norm(n): i for i, n in enumerate(names)}
NS = M.shape[0]

# ---- DK pool ------------------------------------------------------------------------------------
rows = list(csv.DictReader(open(a.salaries, encoding='utf-8-sig')))
excl = {norm(x) for x in a.exclude.split(',') if x}
pool = {}
for r in rows:
    n = norm(r['Name'])
    if n in excl or r['Status'] == 'IL' or n not in col: continue
    if kinds[col[n]] == 'RP' and not a.allow_relievers: continue
    d = pool.setdefault(n, dict(name=r['Name'], col=col[n], team=r['TeamAbbrev'], kind=kinds[col[n]], avg=float(r['AvgPointsPerGame'] or 0)))
    d['cpt_id' if r['Roster Position'] == 'CPT' else 'util_id'] = r['ID']
    d['cpt' if r['Roster Position'] == 'CPT' else 'util'] = int(r['Salary'])
P = [d for d in pool.values() if 'cpt' in d and 'util' in d]
NP = len(P)
cols_ = np.array([p['col'] for p in P]); X = M[:, cols_]            # NS x NP points (UTIL weight)
util = np.array([p['util'] for p in P]); cpt = np.array([p['cpt'] for p in P])
tm = np.array([0 if p['team'] == P[0]['team'] else 1 for p in P])
print(f"pool {NP} players, {NS} sims")

# ---- synthetic public field ----------------------------------------------------------------------
is_rp = np.array([p['kind'] == 'RP' for p in P])
if a.ownership:
    own = {norm(r['name']): r for r in csv.DictReader(open(a.ownership))}
    wc = np.array([float(own.get(norm(p['name']), {}).get('cpt_pct', 0.05)) for p in P]); wu = np.array([float(own.get(norm(p['name']), {}).get('util_pct', 0.2)) for p in P])
else:
    wu = np.array([max(p['avg'], 0.5) ** a.own_power for p in P]) * np.where(is_rp, 0.04, 1.0)   # relievers rarely drafted
    wc = wu * (util / util.mean()) ** a.cpt_salary_power
wu = wu / wu.sum(); wc = wc / wc.sum()
def draw_field(n):
    out = []; tries = 0
    while len(out) < n and tries < n * 600:
        tries += 1
        c = rng.choice(NP, p=wc)
        w2 = wu.copy(); w2[c] = 0; w2 /= w2.sum()
        rest = rng.choice(NP, size=5, replace=False, p=w2)
        s = cpt[c] + util[rest].sum()
        if s > CAP or s < 46500 or len({tm[c], *tm[rest]}) < 2: continue
        out.append((c, rest))
    return out
def parse_real_field(path):
    import re
    out, bad, actual = [], 0, []
    M_ext_col = {**col, '__zero__': M.shape[1]}
    for r in csv.reader(open(path, encoding='utf-8-sig')):
        if not r or r[0] == 'Rank' or len(r) < 6 or not r[5]: continue
        m = re.match(r'CPT (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+?) UTIL (.+)$', r[5])
        if not m: bad += 1; continue
        g = [norm(x) for x in m.groups()]
        ids = [M_ext_col.get(x, M.shape[1]) for x in g]
        out.append((ids[0], np.array(ids[1:]))); actual.append(float(r[4]))
    print(f"real field: {len(out)} entries parsed ({bad} unparsable); unmapped names score 0 in sims")
    return out, np.array(actual)
if a.field_lineups:
    field, field_actual = parse_real_field(a.field_lineups)
    M = np.concatenate([M, np.zeros((NS, 1), np.float32)], axis=1)
    FX = M
else:
    field = draw_field(a.field); FX = X
print(f"field: {len(field)} lineups")
_lab = (lambda i: names[i] if i < len(names) else 'n/a') if a.field_lineups else (lambda i: P[i]['name'])
_n = (M.shape[1] if a.field_lineups else NP)
_cpt = np.bincount([c for c, _ in field], minlength=_n) / len(field)
_all = (np.bincount([x for _, r in field for x in r], minlength=_n)) / len(field)
print("field ownership (CPT% / UTIL%): " + ', '.join(f"{_lab(i)} {100*_cpt[i]:.1f}/{100*_all[i]:.0f}" for i in np.argsort(-(_all + _cpt))[:8]))
Fmax = np.full(NS, -1e9, np.float32); F = np.zeros((NS, len(field)), np.int16)
for j, (c, rest) in enumerate(field):
    F[:, j] = np.rint(4 * (1.5 * FX[:, c] + FX[:, rest].sum(1)))
Fmax = F.max(1) / 4.0
if a.field_lineups:
    print(f"CALIBRATION real field vs simulated field (mean over sims): median {np.median(field_actual):.1f} vs {np.median(F, axis=1).mean()/4:.1f} | p90 {np.percentile(field_actual, 90):.1f} vs {np.percentile(F, 90, axis=1).mean()/4:.1f} | winner {field_actual.max():.1f} vs median-sim-winner {np.median(Fmax):.1f} (P(sim winner <= real winner) = {(Fmax <= field_actual.max()).mean():.2f})")
P99 = np.percentile(F, 99, axis=1) / 4.0; P95 = np.percentile(F, 95, axis=1) / 4.0
del F
BENCH = dict(win=Fmax, top1=P99, top5=P95)[a.objective]
print(f"field benchmark ({a.objective}) per sim: mean {BENCH.mean():.1f}  median {np.median(BENCH):.1f}  p90 {np.percentile(BENCH, 90):.1f}")

idx = rng.permutation(NS); tr, te = idx[:NS // 2], idx[NS // 2:]
def score(c, rest, rows_=slice(None)): return 1.5 * X[rows_, c] + X[rows_][:, rest].sum(1)
def obj(c, rest, rows_): return float((score(c, rest, rows_) >= BENCH[rows_]).mean())
def valid(c, rest):
    if c in rest or len(set(rest)) < 5: return False
    s = cpt[c] + util[list(rest)].sum()
    return s <= CAP and len({tm[c], *tm[list(rest)]}) == 2

def random_start():
    while True:
        c = rng.choice(NP); rest = list(rng.choice(NP, 5, replace=False))
        if valid(c, rest): return c, rest

def climb(c, rest, rows_):
    best = obj(c, rest, rows_); improved = True
    Xr, Br = X[rows_], BENCH[rows_]
    while improved:
        improved = False
        base_rest = Xr[:, rest].sum(1)
        cands = []
        for c2 in range(NP):                                     # new captain
            if c2 != c and valid(c2, rest): cands.append((c2, rest))
        for k in range(5):                                       # replace one UTIL
            for n in range(NP):
                if n in rest or n == c: continue
                r2 = rest.copy(); r2[k] = n
                if valid(c, r2): cands.append((c, r2))
        for k in range(5):                                       # promote a UTIL to CPT, old CPT to UTIL
            r2 = rest.copy(); r2[k] = c
            if valid(rest[k], r2): cands.append((rest[k], r2))
        for c2, r2 in cands:
            v = float(((1.5 * Xr[:, c2] + Xr[:, r2].sum(1)) >= Br).mean())
            if v > best + 1e-9: best, c, rest, improved = v, c2, r2, True
    return best, c, rest

cand = {}
for s in range(a.starts):
    c, r = random_start(); v, c, r = climb(c, r, tr)
    cand[(c, tuple(sorted(r)))] = v
ranked = sorted(cand.items(), key=lambda kv: -kv[1])
# EV-optimal contrast (maximize mean, same search machinery)
def climb_mean(c, rest):
    best = float(score(c, rest).mean()); improved = True
    while improved:
        improved = False
        for c2 in range(NP):
            if c2 != c and valid(c2, rest):
                v = float(score(c2, rest).mean())
                if v > best + 1e-9: best, c, improved = v, c2, True
        for k in range(5):
            for n in range(NP):
                if n in rest or n == c: continue
                r2 = rest.copy(); r2[k] = n
                if valid(c, r2):
                    v = float(score(c, r2).mean())
                    if v > best + 1e-9: best, rest, improved = v, r2, True
    return best, c, rest
ev_best = max((climb_mean(*random_start()) for _ in range(20)), key=lambda t: t[0])

def describe(c, rest):
    r = sorted(rest, key=lambda i: -util[i])
    sc = score(c, list(rest))
    return dict(cpt=P[c]['name'], util=[P[i]['name'] for i in r], salary=int(cpt[c] + util[list(rest)].sum()),
                mean=float(sc.mean()), p90=float(np.percentile(sc, 90)), p99=float(np.percentile(sc, 99)),
                train=obj(c, list(rest), tr), test=obj(c, list(rest), te),
                dk_entry=[f"{P[c]['name']} ({P[c]['cpt_id']})"] + [f"{P[i]['name']} ({P[i]['util_id']})" for i in r])

print(f"\nTOP LINEUPS by P({a.objective}) — measured on held-out sims")
top = []
for (c, r), v in ranked[:max(a.portfolio * 4, 12)]:
    d = describe(c, list(r)); top.append(((c, list(r)), d))
top.sort(key=lambda t: -t[1]['test'])
for (c, r), d in top[:8]:
    print(f" P({a.objective}) test {100 * d['test']:.2f}% (train {100 * d['train']:.2f}%)  mean {d['mean']:.1f}  p90 {d['p90']:.1f}  p99 {d['p99']:.1f}  ${d['salary']}")
    print(f"   CPT {d['cpt']} | " + ', '.join(d['util']))
e = describe(ev_best[1], ev_best[2])
print(f"\nEV-MAX lineup for contrast: mean {e['mean']:.1f}  P({a.objective}) {100 * e['test']:.2f}%  p99 {e['p99']:.1f}  ${e['salary']}\n   CPT {e['cpt']} | " + ', '.join(e['util']))

result = dict(objective=a.objective, field=len(field), n_sims=NS, best=[d for _, d in top[:8]], ev_max=e, portfolio=[])
if a.portfolio > 1:
    chosen, covered = [], np.zeros(NS, bool)
    pool_l = [(k, np.asarray(score(k[0], k[1]) >= BENCH)) for k, _ in top]
    for _ in range(a.portfolio):
        best_gain, bi = -1, None
        for i, (k, win) in enumerate(pool_l):
            gain = float((win & ~covered).mean())
            if gain > best_gain: best_gain, bi = gain, i
        k, win = pool_l.pop(bi); covered |= win; chosen.append(k)
        d = describe(*k); d['marginal_test_gain'] = best_gain
        result['portfolio'].append(d)
        print(f"\nPORTFOLIO #{len(chosen)}: +{100 * best_gain:.2f}% coverage  (union so far {100 * covered.mean():.2f}%)\n   CPT {d['cpt']} | " + ', '.join(d['util']) + f"  ${d['salary']}")
if a.out: json.dump(result, open(a.out, 'w'), indent=1)
