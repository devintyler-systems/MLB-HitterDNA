"""Backtest lineup-construction RULES on real contest fields (no simulation involved).
usage: python rule_backtest.py <standings.csv> <game_pk> [label] ...   (repeat triples)
For every real entry, evaluates rules like 'chalk SP in UTIL', 'CPT is a hitter under X% field CPT share', 'stack >= 4'
and reports the top-1% / top-0.1% rate and mean points of entries that satisfy each rule vs everyone else."""
import sys, collections, io, contextlib, math
import numpy as np
import field_study as fs

def wilson(k, n, z=1.64):
    if n == 0: return (0, 0)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)

def evaluate(label, path, pk):
    with contextlib.redirect_stdout(io.StringIO()): E, F, role, team, own = fs.study(label, path, pk)
    N = len(E); pts = np.array([e['pts'] for e in E]); top1 = pts >= np.percentile(pts, 99); top01 = pts >= np.percentile(pts, 99.9)
    cp = collections.Counter(e['cpt'] for e in E)
    pitchers = [n for n, r in role.items() if r == 'P' and n in own]
    chalk = max(pitchers, key=lambda n: own[n]) if pitchers else None
    def six(e): return [e['cpt']] + e['utl']
    rules = {
      'chalk SP in lineup (any slot)':          lambda e, f: chalk in six(e),
      'chalk SP as UTIL (not CPT)':             lambda e, f: chalk in e['utl'],
      'chalk SP as CPT':                        lambda e, f: e['cpt'] == chalk,
      'CPT is a hitter':                        lambda e, f: role.get(e['cpt']) != 'P',
      'CPT hitter, field CPT share < 10%':      lambda e, f: role.get(e['cpt']) != 'P' and cp[e['cpt']] / N < .10,
      'CPT hitter, field CPT share < 5%':       lambda e, f: role.get(e['cpt']) != 'P' and cp[e['cpt']] / N < .05,
      'stack >= 4 from one team':               lambda e, f: f['split'][0] >= 4,
      'stack >= 5 from one team':               lambda e, f: f['split'][0] >= 5,
      'avg ownership/player < 30%':             lambda e, f: f['own'] < .30,
      'LEVERAGE: chalk SP UTIL + hitter CPT<10% + stack>=4': lambda e, f: chalk in e['utl'] and role.get(e['cpt']) != 'P' and cp[e['cpt']] / N < .10 and f['split'][0] >= 4,
      'LEVERAGE-lite: chalk SP UTIL + hitter CPT<10%':       lambda e, f: chalk in e['utl'] and role.get(e['cpt']) != 'P' and cp[e['cpt']] / N < .10,
    }
    res = {}
    for name, fn in rules.items():
        m = np.array([bool(fn(e, f)) for e, f in zip(E, F)])
        res[name] = (m.sum(), top1[m].sum(), top01[m].sum(), pts[m].mean() if m.any() else float('nan'), top1[~m].mean() if (~m).any() else float('nan'))
    return N, chalk, own.get(chalk, 0), res, top1.mean()

if __name__ == '__main__':
    a = sys.argv[1:]; contests = [(a[i], int(a[i + 1]), a[i + 2]) for i in range(0, len(a), 3)]
    pooled = collections.defaultdict(lambda: [0, 0, 0, 0.0]); allres = {}
    for path, pk, label in contests:
        N, chalk, co, res, base = evaluate(label, path, pk); allres[label] = res
        print(f"\n=== {label}: {N} entries, chalk SP = {chalk} ({100 * co:.0f}% owned); base top-1% rate {100 * base:.2f}%")
        print(f"{'rule':58}{'n':>6}{'top1%':>7}{'rate':>7} {'90% CI':>14}{'lift':>6}{'top.1%':>7}{'mean pts':>9}")
        for name, (n, k, k2, mp, rest) in res.items():
            lo, hi = wilson(k, n); print(f"{name:58}{n:6}{k:7}{100 * k / max(n, 1):6.2f}% [{100 * lo:4.1f}-{100 * hi:4.1f}%] {k / max(n, 1) / base if base else 0:5.1f}x{k2:7}{mp:9.1f}")
            p = pooled[name]; p[0] += n; p[1] += k; p[2] += k2; p[3] += base * n
    if len(contests) > 1:
        print("\n=== POOLED (lift = rate / base rate, base-weighted)")
        for name, (n, k, k2, bn) in pooled.items():
            base = bn / n if n else 0; lo, hi = wilson(k, n); print(f"{name:58}{n:6}{k:5} rate {100 * k / max(n, 1):5.2f}% [{100 * lo:4.1f}-{100 * hi:4.1f}%] lift {k / max(n, 1) / base if base else 0:4.1f}x")
