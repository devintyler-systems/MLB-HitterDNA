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
import argparse, csv, json, os, unicodedata
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
ap.add_argument('--mode', default='ceiling', choices=['ceiling', 'leverage', 'leverage5', 'chalkcpt', 'custom'],
                help="ceiling: unconstrained P(beat field). leverage: chalk SP forced into UTIL + hitter captain under 10%% field CPT share + 4-stack. "
                     "leverage5: chalk SP in lineup + hitter captain under 5%% (the two ingredients that held up on both real fields). custom: use the flags below.")
ap.add_argument('--chalk-sp', default=None, choices=['util', 'any'], help='force the most-owned pitcher into the lineup (as UTIL, or any slot)')
ap.add_argument('--cpt-max-own', type=float, default=None, help='captain must be a HITTER whose field captain share is below this fraction (e.g. 0.05)')
ap.add_argument('--cpt-chalk-sp', action='store_true', help='captain must be the most-owned starting pitcher (the other five slots still optimized for ceiling)')
ap.add_argument('--min-stack', type=int, default=0, help='at least this many players from one team')
ap.add_argument('--ownership-proxy', default=None, help='json from fit_ownership.py (default: ownership_proxy.json next to this script, if present)')
ap.add_argument('--legacy-own', action='store_true', help='use the old AvgPointsPerGame**p field instead of the fitted ownership proxy')
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
    d = pool.setdefault(n, dict(name=r['Name'], col=col[n], team=r['TeamAbbrev'], kind=kinds[col[n]], avg=float(r['AvgPointsPerGame'] or 0), slot=int(r['Starting']) if (r.get('Starting') or '').isdigit() else 0))
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
    _pp = a.ownership_proxy or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ownership_proxy.json')
    if os.path.exists(_pp) and not a.legacy_own:
        px = json.load(open(_pp)); cf = np.array(px['coef'])
        feats = np.array([[1, np.log(max(float(X[:, i].mean()), .5)), np.log(util[i] / 1000), float(P[i]['kind'] == 'P'), float(0 < P[i]['slot'] <= 3), P[i]['avg'] / 10,
                           float(P[i]['kind'] != 'P' and util[i] <= 4600 and P[i]['slot'] > 0)] for i in range(NP)])
        pred = np.clip(np.exp(feats @ cf), .005, .95) * np.where(is_rp, 0.04, 1.0); pred = pred * (5.9 / pred.sum())
        wu = pred.copy(); wc = pred * (util / util.mean()) ** px.get('cpt_salary_exp', 2.5)
        print(f"ownership proxy ({os.path.basename(_pp)}, fit on {px.get('n_slates', '?')} slate(s)): " + ', '.join(f"{P[i]['name']} {100 * min(pred[i], .99):.0f}%" for i in np.argsort(-pred)[:7]))
    else:
        wu = np.array([max(p['avg'], 0.5) ** a.own_power for p in P]) * np.where(is_rp, 0.04, 1.0)   # legacy: relievers rarely drafted
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
_cc = np.array([P[i]['col'] for i in range(NP)]) if a.field_lineups else np.arange(NP)
own_cpt = _cpt[_cc]; own_all = _all[_cc] + _cpt[_cc]            # field captain share / total ownership per pool player
if a.mode == 'leverage': a.chalk_sp, a.cpt_max_own, a.min_stack = 'util', 0.10, 4
if a.mode == 'leverage5': a.chalk_sp, a.cpt_max_own, a.min_stack = 'any', 0.05, 0
if a.mode == 'chalkcpt': a.cpt_chalk_sp = True
_pit = [i for i in range(NP) if P[i]['kind'] == 'P']
chalk_i = max(_pit, key=lambda i: own_all[i]) if _pit else None
if a.mode != 'ceiling' or a.cpt_chalk_sp or a.chalk_sp or a.cpt_max_own is not None or a.min_stack:
    print(f"CONSTRAINTS mode={a.mode}: chalk SP={P[chalk_i]['name'] if chalk_i is not None else None} ({100 * own_all[chalk_i]:.0f}% owned) slot={a.chalk_sp}  hitter-CPT share<{a.cpt_max_own}  min stack {a.min_stack}")
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
    if s > CAP or len({tm[c], *tm[list(rest)]}) != 2: return False
    if a.cpt_chalk_sp and chalk_i is not None and c != chalk_i: return False
    if a.chalk_sp == 'util' and chalk_i is not None and chalk_i not in rest: return False
    if a.chalk_sp == 'any' and chalk_i is not None and chalk_i != c and chalk_i not in rest: return False
    if a.cpt_max_own is not None and (P[c]['kind'] != 'H' or own_cpt[c] >= a.cpt_max_own): return False
    if a.min_stack:
        n1 = int(tm[c] + tm[list(rest)].sum())
        if max(n1, 6 - n1) < a.min_stack: return False
    return True

def random_start():
    for _ in range(400000):
        c = rng.choice(NP); rest = list(rng.choice(NP, 5, replace=False))
        if chalk_i is not None and a.chalk_sp and rng.random() < .8 and chalk_i not in rest and chalk_i != c: rest[0] = chalk_i
        if valid(c, rest): return c, rest
    raise SystemExit("constraints are infeasible for this pool/salary cap")

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

result = dict(mode=a.mode, objective=a.objective, field=len(field), n_sims=NS, best=[d for _, d in top[:8]], ev_max=e, portfolio=[])
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
