"""Full per-game report: environment, starting-pitcher cards, bullpen availability, and every hitter's projection + ceiling odds.
usage: python slate_report.py <slate.json> <data_dir> [label]   (needs events.json, sims.npz, raw/ from the pipeline)
Writes <data_dir>/report.md, hitters.csv, pitchers.csv and prints the markdown."""
import sys, json, csv, os
import numpy as np
cfg = json.load(open(sys.argv[1])); D = sys.argv[2]
ev = json.load(open(f"{D}/events.json")); Z = np.load(f"{D}/sims.npz", allow_pickle=True)
M = Z['M']; names = [str(x) for x in Z['names']]; kinds = [str(x) for x in Z['kinds']]; tord = [str(x) for x in Z['team_order']]; HC = Z['hc'].astype(np.float32); SC = Z['sc'].astype(np.float32)
col = {n: i for i, n in enumerate(names)}
raw = json.load(open(f"{D}/raw/stats.json"))
PN = {'FF': '4-seam', 'SI': 'sinker', 'FC': 'cutter', 'SL': 'slider', 'ST': 'sweeper', 'CU': 'curve', 'KC': 'knuckle-curve', 'CH': 'changeup', 'FS': 'splitter', 'SV': 'slurve', 'KN': 'knuckler', 'EP': 'eephus'}
sens = {}
for ps in ('1.0', '0.80'):
    f = f"{D}/sims_ps{ps}.npz"
    if os.path.exists(f): sens[ps] = np.load(f, allow_pickle=True)['M'].mean(0)
out = []; rows_h = []; rows_p = []
def P(s): out.append(s)
away, home = cfg['away'], cfg['home']
P(f"# {away['abbr']} @ {home['abbr']} - {cfg['date']} - {cfg['park']}")
tr = Z['team_runs'].mean(0); tot = Z['total_runs']
P(f"Simulated {len(M):,} games (calibrated sim, postseason scale applied). Mean runs: " + ', '.join(f"{t} {x:.2f}" for t, x in zip(tord, tr)) + f"; total {tot.mean():.2f} (10th-90th pct {np.percentile(tot, 10):.0f}-{np.percentile(tot, 90):.0f}). "
  f"Sensitivity of total runs to the postseason scale: " + ', '.join(f"{k}: {np.load(f'{D}/sims_ps{k}.npz', allow_pickle=True)['total_runs'].mean():.2f}" for k in sens))
P(f"Park: {cfg['park']} HR factors by batter side {cfg['park_factors']['hr']}, hits {cfg['park_factors']['h']}; weather HR multiplier {cfg.get('weather_hr', 1.0)}.\n")
# ---------------- pitchers ----------------
P("## Starting pitchers")
for ti, t in enumerate(tord):                       # SC rows = starter of FIELDING team t
    o = ev['teams'][[x for x in tord if x != t][0]]  # the opposing lineup record holds this starter's name/id
    pid = o['opp_starter_id']; nm = o['opp_starter']; r = raw['pit'][str(pid)]
    card = json.load(open(f"{D}/raw/p_{pid}.json")) if os.path.exists(f"{D}/raw/p_{pid}.json") else {}
    pc = card.get('pitcher', {}); outs = SC[:, ti, 0]; k = SC[:, ti, 1]; er = SC[:, ti, 2]; h = SC[:, ti, 3]; bb = SC[:, ti, 4]
    pts = M[:, col[nm]]; bf = o['opp_bf_mean_sd']
    kp = pc.get('kProfile', {}); l5 = pc.get('last5', []); sp_ = pc.get('splits', {}); ars = pc.get('arsenal', [])
    P(f"### {nm} ({t}, {r['bio']['pitchHand']['code']}HP) vs {o['hitters'] and [x for x in tord if x != t][0]}")
    P(f"- Season: {kp.get('ip', '?')} IP, {kp.get('era', '?')} ERA, {kp.get('whip', '?')} WHIP, K% {kp.get('kPct', '?')}, K/9 {kp.get('k9', '?')}")
    if l5: P("- Last 5 starts: " + '; '.join(f"{g['date']} {g['opp']} {g['ip']} IP {g['k']} K" for g in l5))
    if sp_:
        P("- Splits: " + ', '.join(f"vs {s}HB: {v['pa']} PA, K% {v['kPct']}, SLG {v['slg']}, {v['hr']} HR" for s, v in sp_.items() if v))
    if ars: P("- Arsenal (usage / SLG against / whiff): " + ', '.join(f"{PN.get(a['pitch'], a['pitch'])} {a['usage']:.0f}% / .{int(round(a['slgAgainst'] * 1000)):03d} / {a['whiff']:.0f}%" for a in ars))
    P(f"- Leash input: mean {bf[0]:.1f} batters faced (avg of last 5 starts), SD {bf[1]}.")
    P(f"- **Projection**: {np.median(outs) / 3:.1f} IP median ({outs.mean() / 3:.1f} mean; P(6+ IP) {100 * np.mean(outs >= 18):.0f}%, P(<4 IP) {100 * np.mean(outs < 12):.0f}%), K {k.mean():.1f} (P(7+) {100 * np.mean(k >= 7):.0f}%), ER {er.mean():.1f}, H {h.mean():.1f}, BB+HBP {bb.mean():.1f}; bullpen arms used after him {SC[:, ti, 5].mean():.1f}")
    P(f"- **DK pitcher pts**: mean {pts.mean():.1f}, p10 {np.percentile(pts, 10):.1f}, median {np.median(pts):.1f}, p90 {np.percentile(pts, 90):.1f}; P(25+) {100 * np.mean(pts >= 25):.0f}%, P(35+) {100 * np.mean(pts >= 35):.0f}%" + ('' if '1.0' not in sens else f"  (postseason-scale range {sens['0.80'][col[nm]]:.1f}-{sens['1.0'][col[nm]]:.1f})"))
    rows_p.append(dict(game=f"{away['abbr']}@{home['abbr']}", pitcher=nm, team=t, proj_IP=round(outs.mean() / 3, 2), proj_K=round(k.mean(), 2), proj_ER=round(er.mean(), 2), proj_H=round(h.mean(), 2), proj_BB=round(bb.mean(), 2),
                       dk_mean=round(pts.mean(), 2), dk_p10=round(np.percentile(pts, 10), 1), dk_p90=round(np.percentile(pts, 90), 1), p_6ip=round(np.mean(outs >= 18), 3), p_short_lt4ip=round(np.mean(outs < 12), 3)))
    P('')
# ---------------- bullpens ----------------
P("## Bullpen availability (weights = chance of appearing, from rest and workload in this series)")
for ab, w in cfg['relievers'].items(): P(f"- {ab}: " + ', '.join(f"{n} {x:.2f}" for n, x in w.items()))
P('')
# ---------------- hitters ----------------
for ti, t in enumerate(tord):
    rec = ev['teams'][t]; o = [x for x in tord if x != t][0]
    P(f"## {t} lineup vs {rec['opp_starter']} (projected team runs {tr[ti]:.2f})")
    P("| # | Hitter | H | R | RBI | BB | K | SB | P(HR) | HRR | DK pts (p10-p90) | P(10+/17+/24+) | Why |"); P("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for j, h in enumerate(rec['hitters']):
        c = HC[:, ti, j, :]; dk = M[:, col[h['name']]]; hrr = c[:, 0] + c[:, 2] + c[:, 3]
        # why
        bits = []; g = sorted([x for x in (h.get('grid') or []) if x[1] >= 12 and x[2] >= 10 and x[4] is not None], key=lambda x: -x[1])[:2]
        if g: bits.append("vs mix: " + ', '.join(f"{PN.get(x[0], x[0])} {x[1]:.0f}% (.{int(round(x[4] * 1000)):03d} SLG, {x[5]:.0f}% whiff)" for x in g))
        if h.get('hand_ops') and h.get('hand_pa', 0) >= 50: bits.append(f"{h['hand_ops']} OPS vs {raw['pit'][str(rec['opp_starter_id'])]['bio']['pitchHand']['code']}HP ({int(h['hand_pa'])} PA)")
        f = h.get('form')
        if f and f['pa'] >= 8: bits.append(f"L14 incl. postseason {f['avg']}/{f['ops']} OPS, {f['hr']} HR, {f['k']} K in {int(f['pa'])} PA")
        b = h.get('bvp')
        if b and b[0] >= 3: bits.append(f"BvP {b[1]}-for-{b[0]}")
        if h.get('pull_air') is not None: bits.append(f"pull-air {h['pull_air']:.0f}% -> park HR x{h['park_hr_eff']:.2f}")
        fit = h.get('fit') or 1
        if fit >= 1.04: bits.append("arsenal fit +")
        elif fit <= .96: bits.append("arsenal fit -")
        if c[:, 6].mean() >= .12: bits.append(f"SB threat ({h['sb']:.0f} SB/{h['cs']:.0f} CS, {h['sprint']:.1f} ft/s)")
        P(f"| {h['slot']} | {h['name']} ({h['pos']}, {h.get('bats', '')}) | {c[:, 0].mean():.2f} | {c[:, 2].mean():.2f} | {c[:, 3].mean():.2f} | {c[:, 4].mean():.2f} | {c[:, 5].mean():.2f} | {c[:, 6].mean():.2f} | {100 * np.mean(c[:, 1] >= 1):.0f}% | {hrr.mean():.2f} | {dk.mean():.1f} ({np.percentile(dk, 10):.0f}-{np.percentile(dk, 90):.0f}) | {100 * np.mean(dk >= 10):.0f}/{100 * np.mean(dk >= 17):.0f}/{100 * np.mean(dk >= 24):.0f}% | {'; '.join(bits)} |")
        rows_h.append(dict(game=f"{away['abbr']}@{home['abbr']}", team=t, slot=h['slot'], pos=h['pos'], name=h['name'], bats=h.get('bats'), opp_pitcher=rec['opp_starter'], proj_PA=round(c[:, 7].mean(), 2),
                           proj_H=round(c[:, 0].mean(), 3), proj_R=round(c[:, 2].mean(), 3), proj_RBI=round(c[:, 3].mean(), 3), proj_BB=round(c[:, 4].mean(), 3), proj_K=round(c[:, 5].mean(), 3), proj_SB=round(c[:, 6].mean(), 3),
                           p_HR=round(float(np.mean(c[:, 1] >= 1)), 3), proj_HRR=round(float(hrr.mean()), 3), p_HRR_ge2=round(float(np.mean(hrr >= 2)), 3), p_HRR_ge3=round(float(np.mean(hrr >= 3)), 3), p_H_ge2=round(float(np.mean(c[:, 0] >= 2)), 3),
                           p_1plus_hit=round(float(np.mean(c[:, 0] >= 1)), 3), p_1plus_run=round(float(np.mean(c[:, 2] >= 1)), 3), p_1plus_rbi=round(float(np.mean(c[:, 3] >= 1)), 3), p_1plus_bb=round(float(np.mean(c[:, 4] >= 1)), 3), p_1plus_k=round(float(np.mean(c[:, 5] >= 1)), 3), p_sb=round(float(np.mean(c[:, 6] >= 1)), 3),
                           dk_mean=round(float(dk.mean()), 2), dk_p10=round(float(np.percentile(dk, 10)), 1), dk_p90=round(float(np.percentile(dk, 90)), 1), p_dk10=round(float(np.mean(dk >= 10)), 3), p_dk17=round(float(np.mean(dk >= 17)), 3), p_dk24=round(float(np.mean(dk >= 24)), 3),
                           dk_mean_ps1=round(float(sens['1.0'][col[h['name']]]), 2) if '1.0' in sens else '', dk_mean_ps080=round(float(sens['0.80'][col[h['name']]]), 2) if '0.80' in sens else '',
                           pull_air=None if h.get('pull_air') is None else round(h['pull_air'], 1), park_hr_eff=None if h.get('park_hr_eff') is None else round(h['park_hr_eff'], 3), bvp=f"{b[1]}-{b[0]}" if b else '',
                           form=f"{f['avg']}/{f['ops']} {int(f['pa'])}PA {f['hr']}HR {f['k']}K" if f else '', why='; '.join(bits)))
    P('')
open(f"{D}/report.md", 'w').write('\n'.join(out))
for nm_, rows in (('hitters', rows_h), ('pitchers', rows_p)):
    with open(f"{D}/{nm_}.csv", 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print('\n'.join(out))
