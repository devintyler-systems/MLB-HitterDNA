"""Run optimize.py in several modes on one slate and compare: simulated odds AND (when the game is final) the actual
points + finishing percentile each lineup would have taken in the real field.
usage: python compare_modes.py <data_dir> <DKSalaries.csv> [--field-lineups standings.csv] [--actuals actuals.json] [--portfolio 4]
"""
import sys, json, subprocess, os, re, csv, argparse, unicodedata
import numpy as np
ap = argparse.ArgumentParser(); ap.add_argument('data_dir'); ap.add_argument('salaries')
ap.add_argument('--field-lineups'); ap.add_argument('--actuals'); ap.add_argument('--portfolio', type=int, default=4)
ap.add_argument('--starts', type=int, default=100); ap.add_argument('--extra', default='', help='extra args passed to optimize.py, e.g. "--allow-relievers"'); ap.add_argument('--modes', default='ceiling,leverage,leverage5')
a = ap.parse_args()
def norm(s): return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').lower().replace('.', '').strip()
act = {norm(k): v for k, v in json.load(open(a.actuals)).items()} if a.actuals else None
real = None
if a.field_lineups:
    real = np.array([float(r[4]) for r in csv.reader(open(a.field_lineups, encoding='utf-8-sig')) if r and r[0] != 'Rank' and len(r) > 5 and r[5]])
here = os.path.dirname(os.path.abspath(__file__)); out = {}
for mode in a.modes.split(','):
    f = f"{a.data_dir}/cmp_{mode}.json"
    cmd = [sys.executable, f"{here}/optimize.py", a.data_dir, a.salaries, '--mode', mode, '--portfolio', str(a.portfolio), '--starts', str(a.starts), '--out', f]
    if a.field_lineups: cmd += ['--field-lineups', a.field_lineups]
    if a.extra: cmd += a.extra.split()
    print(f"running mode={mode} ...", flush=True); subprocess.run(cmd, check=True, capture_output=True)
    out[mode] = json.load(open(f))
def actual(l):
    if act is None: return None
    return 1.5 * act.get(norm(l['cpt']), 0) + sum(act.get(norm(u), 0) for u in l['util'])
print(f"\n{'mode':10}{'#':>2} {'sim P(win)':>10} {'sim mean':>9} {'sim p99':>8} {'actual':>7} {'real rank':>10}  lineup")
for mode, r in out.items():
    rows = [(l, 'port') for l in r['portfolio']] + [(r['ev_max'], 'EV-max')]
    for i, (l, tag) in enumerate(rows, 1):
        ac = actual(l); rk = ''
        if ac is not None and real is not None: rk = f"{int((real > ac).sum()) + 1}/{len(real)}"
        pw = l.get('test', 0) * 100
        print(f"{mode:10}{('EV' if tag == 'EV-max' else i):>2} {pw:9.2f}% {l['mean']:9.1f} {l['p99']:8.1f} {'' if ac is None else f'{ac:7.1f}'} {rk:>10}  CPT {l['cpt']} | " + ', '.join(l['util']))
    cov = None
