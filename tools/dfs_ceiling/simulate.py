"""Monte Carlo game simulator -> per-player DraftKings points for every simulated game.

usage: python simulate.py <data_dir> [n_sims] [seed]
reads  <data_dir>/events.json (from build_events.py)
writes <data_dir>/sims.npz     (M: n_sims x n_players DK points at UTIL weight; names; teams; kinds)

Why a full game sim instead of per-player distributions: runs, RBI and steals depend on who is on base ahead of a
hitter, so teammate outcomes are correlated (stacks). Simulating plate appearances with base/out states produces
that correlation naturally. Extra game-level shocks (offense form, starter form, per-hitter form, per-hitter power)
fatten the tails so the optimizer sees realistic ceilings, not just the mean.

DK Showdown scoring used:
  hitters  1B 3, 2B 5, 3B 8, HR 10, RBI 2, R 2, BB 2, HBP 2, SB 5
  pitchers 0.75/out, K 2, W 4, ER -2, H -0.6, BB -0.6, HBP -0.6   (CG bonuses ignored)
"""
import json, sys, math, random
import numpy as np

D = sys.argv[1]
NS = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 7
ev = json.load(open(f"{D}/events.json"))
rnd = random.Random(SEED)

EVK = ('K', 'BB', 'HBP', 'B1', 'B2', 'B3', 'HR')
import os
SIG = dict(offense=.07, starter=.10, pen=.06, hitter=.08, power=.20)   # log-sd of game-level shocks
_SS = float(os.environ.get('DFS_SIG_SCALE', 1.0)); SIG = {k: v * _SS for k, v in SIG.items()}
# Component defaults below were fit by fit_components.py on ALL 377 games of the 9/1-10/5/2026 backtest (fitting one half and testing the
# other overfit run totals: halves differ by ~5% from sampling noise alone). Raw model: hits +6%, HR +9%, BB +6%, RBI +8%, steals -60%, runs +3%, and the errors cancelled in hitter points.
OFF = float(os.environ.get('DFS_OFFENSE_SCALE', 0.966))
# opt-in extra multiplier for division-series-and-later games: hitters there scored ~22-30% below the sim on only 144 hitter-games
# (8 games), so it is NOT applied by default. Try 0.88-0.92 for playoff slates and judge with calibrate_field.py.
OFF *= float(os.environ.get('DFS_POSTSEASON_SCALE', 1.0))
# component knobs (fit by fit_components.py so each box-score component matches reality, not just the point total)
HRS = float(os.environ.get('DFS_HR_SCALE', 0.971)); BBS = float(os.environ.get('DFS_BB_SCALE', 0.957))
SBS = float(os.environ.get('DFS_SB_SCALE', 2.629)); ADV = float(os.environ.get('DFS_ADV_SCALE', 1.217))
WILD = float(os.environ.get('DFS_WILD_RATE', 0.074))   # per-PA chance a runner on 3rd scores on a wild pitch/passed ball/error/balk (a run with NO RBI)
# late replacements: a starter may be pinch-hit for / defensively replaced before his 3rd-or-later PA; his team's runs still count but the
# DFS starter stops scoring. Hazard per late PA by lineup slot x DFS_SUB_SCALE (fit so starters' share of team production matches reality).
SUBS = float(os.environ.get('DFS_SUB_SCALE', 1.5)); SUBQ = [.015, .015, .03, .03, .03, .07, .07, .07, .07]
BENCH = None
def adv(p): return min(p * ADV, 0.97)   # baserunning advancement / runner-scoring probabilities
def shock(sd, z): return math.exp(sd * z - sd * sd / 2)               # mean-preserving lognormal

_bp = dict(K=.24, BB=.08, HBP=.01, B1=.135, B2=.038, B3=.003, HR=.024); _acc = 0.0; BENCH = []
for _e in ('K', 'BB', 'HBP', 'B1', 'B2', 'B3', 'HR'): _acc += _bp[_e]; BENCH.append(_acc)
teams = list(ev['teams'].keys())
assert len(teams) == 2, "need both lineups posted"
side = {t: ev['teams'][t]['side'] for t in teams}
opp = {t: ev['teams'][t]['opp'] for t in teams}

# ---- column layout -----------------------------------------------------------------------------
cols, kinds, ctm = [], [], []
col_of = {}
for t in teams:
    for h in ev['teams'][t]['hitters']:
        col_of[('H', t, h['slot'])] = len(cols); cols.append(h['name']); kinds.append('H'); ctm.append(t)
for t in teams:  # starter of team t is the opp_starter of the OTHER team
    o = opp[t]
    col_of[('SP', t)] = len(cols); cols.append(ev['teams'][o]['opp_starter']); kinds.append('P'); ctm.append(t)
for t in teams:
    for nm in ev['relievers'].get(t, {}):
        col_of[('RP', t, nm)] = len(cols); cols.append(nm); kinds.append('RP'); ctm.append(t)
NC = len(cols)

def draw_pen_queue(t):
    names = list(ev['relievers'].get(t, {}).keys()); w = [ev['relievers'][t][n] for n in names]
    order = []
    while names:  # weighted order without replacement
        i = rnd.choices(range(len(names)), weights=w)[0]
        order.append(names.pop(i)); w.pop(i)
    return order

def planned_outs(name):
    if name in ev['bulk']: return 3 * rnd.choice((2, 3, 3, 4))
    return 3 * rnd.choice((1, 1, 1, 2))

def sim_game():
    z_off = {t: rnd.gauss(0, 1) for t in teams}
    z_sp = {t: rnd.gauss(0, 1) for t in teams}      # shock of team t's STARTER
    z_pen = {t: rnd.gauss(0, 1) for t in teams}
    # per-hitter probabilities for this game
    P = {}
    for t in teams:
        o = opp[t]; lst = []
        for h in ev['teams'][t]['hitters']:
            zh = rnd.gauss(0, 1); zp = rnd.gauss(0, 1)
            fo = shock(SIG['offense'], z_off[t]) * shock(SIG['hitter'], zh)
            fsp = fo * shock(SIG['starter'], z_sp[o]); fpen = fo * shock(SIG['pen'], z_pen[o])
            fhr = shock(SIG['power'], zp)
            ksp = shock(SIG['starter'], -z_sp[o]); kpen = shock(SIG['pen'], -z_pen[o])
            def mk(v, f, k, tto=1.0):
                d = {'K': v['K'] * k * (0.97 if tto > 1 else 1.0),
                     'BB': v['BB'] * BBS, 'HBP': v['HBP'],
                     'B1': v['B1'] * f * tto * OFF, 'B2': v['B2'] * f * tto * OFF, 'B3': v['B3'] * f * tto * OFF, 'HR': v['HR'] * f * fhr * tto * OFF * HRS}
                s = sum(d.values())
                if s > .97:
                    for e in ('B1', 'B2', 'B3', 'HR'): d[e] *= (.97 - d['K'] - d['BB'] - d['HBP']) / max(s - d['K'] - d['BB'] - d['HBP'], 1e-9)
                c, acc = [], 0.0
                for e in EVK: acc += d[e]; c.append(acc)
                return c
            lst.append(dict(name=h['name'], slot=h['slot'], sp=mk(h['vs_sp'], fsp, ksp), sp3=mk(h['vs_sp'], fsp, ksp, 1.04),
                            pen=mk(h['vs_pen'], fpen, kpen), sb=min(h['sb_per_ob'] * 1.115 * SBS, .6), col=col_of[('H', t, h['slot'])],
                            n_sp=0, n_pa=0, out=False))
        P[t] = lst
    pts = [0.0] * NC; saved = {}
    stat = {t: [dict(b1=0, b2=0, b3=0, hr=0, rbi=0, r=0, bb=0, hbp=0, sb=0) for _ in range(9)] for t in teams}
    # pitching state per fielding team
    pit = {}
    for t in teams:  # t = fielding team
        m_, s_ = ev['teams'][opp[t]]['opp_bf_mean_sd']  # parameters of t's starter live on the other team's record
        cap = int(min(max(round(rnd.gauss(m_, s_)), 3), 27))
        pit[t] = dict(cur=dict(kind='SP', col=col_of[('SP', t)], outs=0, K=0, H=0, BB=0, HBP=0, ER=0, bf=0),
                      cap=cap, queue=draw_pen_queue(t), all=[], planned=None, exit_lead=None, blown=False, sp_exit=False)
        pit[t]['all'].append(pit[t]['cur'])
    score = {t: 0 for t in teams}; batter = {t: 0 for t in teams}
    away = [t for t in teams if side[t] == 'away'][0]; home = [t for t in teams if side[t] == 'home'][0]

    def pull_if_needed(fld):
        pc = pit[fld]; c = pc['cur']
        if c['kind'] == 'SP' and c['bf'] >= pc['cap']:
            pc['sp_exit'] = True; pc['exit_lead'] = score[fld] - score[opp[fld]]
            nm = pc['queue'].pop(0) if pc['queue'] else None
            if nm is None: return
            pc['cur'] = dict(kind='RP', col=col_of[('RP', fld, nm)], outs=0, K=0, H=0, BB=0, HBP=0, ER=0, bf=0, plan=planned_outs(nm))
            pc['all'].append(pc['cur'])
        elif c['kind'] == 'RP' and c['outs'] >= c['plan'] and pc['queue']:
            nm = pc['queue'].pop(0)
            pc['cur'] = dict(kind='RP', col=col_of[('RP', fld, nm)], outs=0, K=0, H=0, BB=0, HBP=0, ER=0, bf=0, plan=planned_outs(nm))
            pc['all'].append(pc['cur'])

    def run_scores(bat, fld, runner):  # runner = (batter_idx, pitcher_dict or None)
        score[bat] += 1
        stat[bat][runner[0]]['r'] += 1
        if runner[1] is not None: runner[1]['ER'] += 1
        pc = pit[fld]
        if pc['sp_exit'] and not pc['blown'] and score[fld] - score[bat] <= 0: pc['blown'] = True

    for inning in range(1, 13):
        for half in (0, 1):
            bat, fld = (away, home) if half == 0 else (home, away)
            if half == 1 and inning >= 9 and score[home] > score[away]: break
            if inning > 9 and half == 1 and False: pass
            outs = 0; bases = [None, None, None]
            if inning > 9:  # ghost runner on 2nd (unearned)
                bases[1] = ((batter[bat] - 1) % 9, None)
            while outs < 3:
                pull_if_needed(fld)
                pc = pit[fld]; cur = pc['cur']
                i = batter[bat]; hp = P[bat][i]
                if SUBS and not hp['out'] and hp['n_pa'] >= 2 and rnd.random() < SUBQ[hp['slot'] - 1] * SUBS:
                    hp['out'] = True; saved[(bat, i)] = stat[bat][i]
                    stat[bat][i] = dict(b1=0, b2=0, b3=0, hr=0, rbi=0, r=0, bb=0, hbp=0, sb=0)   # bench hitter's production: counts for the team, not for the DFS starter
                hp['n_pa'] += 1; st = stat[bat][i]
                # stolen base attempt before the PA
                if bases[0] and not bases[1] and outs < 2:
                    rp = P[bat][bases[0][0]]
                    if rnd.random() < rp['sb']:
                        if rnd.random() < .78:
                            bases[1] = bases[0]; bases[0] = None; stat[bat][rp['slot'] - 1]['sb'] += 1
                        else:
                            bases[0] = None; outs += 1; cur['outs'] += 1
                            if outs >= 3: break
                if bases[2] and WILD and rnd.random() < WILD:
                    run_scores(bat, fld, bases[2]); bases[2] = None
                    if bases[1]: bases[2] = bases[1]; bases[1] = None
                u = rnd.random()
                if hp['out']: c = BENCH
                elif cur['kind'] == 'SP':
                    c = hp['sp3'] if hp['n_sp'] >= 2 else hp['sp']; hp['n_sp'] += 1
                else: c = hp['pen']
                cur['bf'] += 1
                k = 0
                while k < 7 and u >= c[k]: k += 1
                rbi = 0
                if k == 0:                                   # K
                    outs += 1; cur['outs'] += 1; cur['K'] += 1
                elif k in (1, 2):                            # BB / HBP: forced advances
                    if k == 1: st['bb'] += 1; cur['BB'] += 1
                    else: st['hbp'] += 1; cur['HBP'] += 1
                    if bases[0]:
                        if bases[1]:
                            if bases[2]: run_scores(bat, fld, bases[2]); rbi += 1
                            bases[2] = bases[1]
                        bases[1] = bases[0]
                    bases[0] = (i, cur)
                elif k in (3, 4, 5, 6):                      # hits
                    cur['H'] += 1
                    r1, r2, r3 = bases
                    if k == 3:
                        st['b1'] += 1
                        if r3: run_scores(bat, fld, r3); rbi += 1
                        nb = [(i, cur), None, None]
                        if r2:
                            if rnd.random() < adv(.60): run_scores(bat, fld, r2); rbi += 1
                            else: nb[2] = r2
                        if r1:
                            if rnd.random() < adv(.28) and nb[2] is None: nb[2] = r1
                            elif nb[1] is None: nb[1] = r1
                            else: nb[2] = nb[2] or r1
                        bases = nb
                    elif k == 4:
                        st['b2'] += 1
                        for r in (r3, r2):
                            if r: run_scores(bat, fld, r); rbi += 1
                        nb = [None, (i, cur), None]
                        if r1:
                            if rnd.random() < adv(.45): run_scores(bat, fld, r1); rbi += 1
                            else: nb[2] = r1
                        bases = nb
                    elif k == 5:
                        st['b3'] += 1
                        for r in (r3, r2, r1):
                            if r: run_scores(bat, fld, r); rbi += 1
                        bases = [None, None, (i, cur)]
                    else:
                        st['hr'] += 1
                        for r in (r3, r2, r1):
                            if r: run_scores(bat, fld, r); rbi += 1
                        run_scores(bat, fld, (i, cur)); rbi += 1
                        bases = [None, None, None]
                else:                                        # ball in play, out
                    go = rnd.random() < .58
                    if go and bases[0] and outs < 2 and rnd.random() < .14:   # double play
                        outs += 2; cur['outs'] += 2; bases[0] = None
                        if outs < 3 and bases[2] and False: pass
                    else:
                        outs += 1; cur['outs'] += 1
                        if outs < 3:
                            if go:
                                if bases[2] and rnd.random() < adv(.45): run_scores(bat, fld, bases[2]); rbi += 1; bases[2] = None
                                if bases[1] and not bases[2] and rnd.random() < adv(.30): bases[2] = bases[1]; bases[1] = None
                                if bases[0] and not bases[1]: bases[1] = bases[0]; bases[0] = None
                            else:
                                if bases[2] and rnd.random() < adv(.55): run_scores(bat, fld, bases[2]); rbi += 1; bases[2] = None
                                if bases[1] and not bases[2] and rnd.random() < adv(.18): bases[2] = bases[1]; bases[1] = None
                st['rbi'] += rbi
                batter[bat] = (i + 1) % 9
                if half == 1 and inning >= 9 and score[home] > score[away]: outs = 3  # walk-off
            if inning >= 9 and half == 1: break
        if inning >= 9 and score[away] != score[home]: break
        if inning >= 9 and half == 1 and score[away] != score[home]: break
    # ---- DK points ----
    for t in teams:
        for j, s in enumerate(stat[t]):
            s = saved.get((t, j), s)
            pts[P[t][j]['col']] = 3 * s['b1'] + 5 * s['b2'] + 8 * s['b3'] + 10 * s['hr'] + 2 * s['rbi'] + 2 * s['r'] + 2 * s['bb'] + 2 * s['hbp'] + 5 * s['sb']
    winner = away if score[away] > score[home] else home if score[home] > score[away] else None
    for t in teams:  # t fielding
        pc = pit[t]
        for p in pc['all']:
            win = 0
            if p['kind'] == 'SP' and winner == t and p['outs'] >= 15 and pc['exit_lead'] is not None and pc['exit_lead'] > 0 and not pc['blown']: win = 1
            if p['kind'] == 'SP' and winner == t and pc['exit_lead'] is None and p['outs'] >= 15: win = 1  # went the distance
            pts[p['col']] += .75 * p['outs'] + 2 * p['K'] + 4 * win - 2 * p['ER'] - .6 * (p['H'] + p['BB'] + p['HBP'])
    allst = [x for t in teams for x in stat[t]] + list(saved.values())      # starters + replaced starters' saved lines + bench
    comp = [sum(x['b1'] + x['b2'] + x['b3'] + x['hr'] for x in allst), sum(x['hr'] for x in allst), sum(x['bb'] + x['hbp'] for x in allst),
            sum(x['sb'] for x in allst), sum(x['r'] for x in allst), sum(x['rbi'] for x in allst)]
    return pts, score[away] + score[home], score, comp

M = np.zeros((NS, NC), dtype=np.float32); tot = np.zeros(NS, dtype=np.float32)
sc_mat = np.zeros((NS, 2), dtype=np.float32); comp_mat = np.zeros((NS, 6), dtype=np.float32)  # game totals: H, HR, BB+HBP, SB, R, RBI
for s in range(NS):
    p, t, sc, cp_ = sim_game(); M[s] = p; tot[s] = t; sc_mat[s] = [sc[teams[0]], sc[teams[1]]]; comp_mat[s] = cp_
    if (s + 1) % 5000 == 0: print(f"  {s + 1}/{NS} sims", flush=True)
np.savez_compressed(f"{D}/sims.npz", M=M, names=np.array(cols), teams=np.array(ctm), kinds=np.array(kinds), total_runs=tot, team_order=np.array(teams), team_runs=sc_mat, comp=comp_mat)
print(f"simulated {NS} games; mean total runs {tot.mean():.2f}  team runs {dict(zip(teams, sc_mat.mean(0).round(2)))}")
for j in np.argsort(-M.mean(0))[:14]:
    col = M[:, j]
    print(f"  {cols[j]:22}{ctm[j]:4}{kinds[j]:3} mean {col.mean():5.2f}  p90 {np.percentile(col, 90):5.1f}  p99 {np.percentile(col, 99):5.1f}  max {col.max():5.1f}")
