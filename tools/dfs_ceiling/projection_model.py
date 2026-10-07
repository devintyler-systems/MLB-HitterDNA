"""Per-PA event-probability model for one game (own synthesis from real data).
No sportsbook odds/lines/probabilities are read anywhere. BetLogic cards are used ONLY for per-pitch-type
batter grids and pitcher arsenal (raw numbers); its fit scores/grades/odds are ignored.
Data dir comes from $DFS_DATA_DIR (set by build_events.py); slate settings from $DFS_SLATE_CONFIG.
"""
import json, csv, math, os
S = os.environ["DFS_DATA_DIR"]
CFG = json.load(open(os.environ["DFS_SLATE_CONFIG"]))
raw = json.load(open(f"{S}/raw/stats.json"))
card = {}
for _p in (CFG['away']['starter_id'], CFG['home']['starter_id']):
    try: card[_p] = json.load(open(f"{S}/raw/p_{_p}.json"))
    except Exception: card[_p] = {'batters': [], 'pitcher': {'grid': []}}

def rd(f):
    return list(csv.DictReader(open(f"{S}/raw/{f}.csv", encoding="utf-8-sig")))
bx = {int(r['player_id']): r for r in rd('sv_bat_xstats')}
be = {int(r['player_id']): r for r in rd('sv_bat_ev')}
px = {int(r['player_id']): r for r in rd('sv_pit_xstats')}
sp = {int(r['player_id']): r for r in rd('sv_sprint')}
# players below Savant's minimums (rookies, bench) get neutral stand-ins instead of crashing
_NEUTRAL_BAT = {'ba': '0', 'est_ba': '0', 'slg': '0', 'est_slg': '0', 'est_woba': '0.315'}
class _Dflt(dict):
    def __init__(self, d, dflt): super().__init__(d); self.dflt = dflt
    def __missing__(self, k): return self.dflt
bx = _Dflt(bx, _NEUTRAL_BAT); sp = _Dflt(sp, {'sprint_speed': '27.0'})

# ---------------- league constants (2026 regular season, all 30 teams) ----------------
LG = dict(K=.2214, BB=.0889, HBP=.0115, B1=.1420, B2=.0408, B3=.0036, HR=.0303)
LG_SB_PER_OB = .0624
LG_WOBA_PIT = sum(float(r['est_woba']) * float(r['pa']) for r in px.values()) / sum(float(r['pa']) for r in px.values())
LG_OPS_RP = .700
LW = dict(B1=.47, B2=.77, B3=1.04, HR=1.40, BB=.31, HBP=.33)
OUT_C = 0.0577  # run value of a PA-out baseline (calibrates to 4.48 R/G)

PARK = {  # park multipliers: hits(in play), HR, runs
    'Truist Park': dict(h=1.00, hr=1.03, r=1.01),
    'Petco Park': dict(h=.98, hr=.94, r=.96),  # hot (83F) but 9 mph in from LF
}
PARK[CFG['park']] = CFG['park_factors']  # slate config overrides; 'hr' may be a number or {'L': .., 'R': ..} (batter side)
WEATHER_HR = float(CFG.get('weather_hr', 1.0))  # temperature/wind multiplier on HR odds for this game

# ---------------- helpers ----------------
def g(stat, k, d=0):
    try: return float(stat.get(k, d))
    except: return d
def spl_pick(r, key):
    out = {}
    st = r[key]['stats']
    if not st: return out
    for s in st[0].get('splits', []):
        k = s['split']['description']
        if k not in out or g(s['stat'], 'plateAppearances', g(s['stat'], 'battersFaced')) > g(out[k], 'plateAppearances', g(out[k], 'battersFaced')):
            out[k] = s['stat']
    return out
def first(r, key):
    st = r[key]['stats']
    if not st or not st[0].get('splits'): return {}
    return st[0]['splits'][0]['stat']

def rates(st):
    pa = g(st, 'plateAppearances')
    if pa <= 0: return None, 0
    h, d, t, hr = g(st, 'hits'), g(st, 'doubles'), g(st, 'triples'), g(st, 'homeRuns')
    return dict(K=g(st, 'strikeOuts') / pa, BB=g(st, 'baseOnBalls') / pa, HBP=g(st, 'hitByPitch') / pa,
                B1=(h - d - t - hr) / pa, B2=d / pa, B3=t / pa, HR=hr / pa), pa

def odds(p): p = min(max(p, 1e-4), .9); return p / (1 - p)
def unodds(o): return o / (1 + o)
def oratio(pb, pp, pl): return unodds(odds(pb) * odds(pp) / odds(pl))

PLAT = {  # (bat, throws): (hit-type mult, BB mult, K mult)
    ('L', 'L'): (.91, .96, 1.10), ('L', 'R'): (1.03, 1.02, .97),
    ('R', 'L'): (1.05, 1.03, .95), ('R', 'R'): (.99, 1.0, 1.0),
}

# ---------------- pitcher profiles ----------------
PIT = {}
for pid, r in raw['pit'].items():
    pid = int(pid)
    s = first(r, 'season'); bf = g(s, 'battersFaced')
    k = g(s, 'strikeOuts') / bf; bb = g(s, 'baseOnBalls') / bf; hr = g(s, 'homeRuns') / bf
    kreg = (g(s, 'strikeOuts') + 60 * LG['K']) / (bf + 60)
    bbreg = (g(s, 'baseOnBalls') + 170 * LG['BB']) / (bf + 170)
    hrreg = (g(s, 'homeRuns') + 500 * LG['HR']) / (bf + 500)
    spl = spl_pick(r, 'spl')
    xw = float(px[pid]['est_woba']) if pid in px else LG_WOBA_PIT; xpa = float(px[pid]['pa']) if pid in px else 0.0
    xw_reg = (xw * xpa + LG_WOBA_PIT * 400) / (xpa + 400)
    hand = {}
    for side, desc in (('L', 'vs Left'), ('R', 'vs Right')):
        st = spl.get(desc, {}); n = g(st, 'battersFaced')
        ops_all = g(s, 'ops'); ops_h = g(st, 'ops', ops_all) or ops_all
        # float() on '.595' style strings handled by g
        hand[side] = dict(
            K=(g(st, 'strikeOuts') + 100 * kreg) / (n + 100),
            BB=(g(st, 'baseOnBalls') + 100 * bbreg) / (n + 100),
            HR=(g(st, 'homeRuns') + 300 * hrreg) / (n + 300),
            contact=(xw_reg / LG_WOBA_PIT) * (1 + (ops_h / ops_all - 1) * n / (n + 300)) if ops_all else xw_reg / LG_WOBA_PIT,
            n=n)
    PIT[pid] = dict(name=r['bio']['fullName'], throws=r['bio']['pitchHand']['code'], hand=hand, xw=xw, xw_reg=xw_reg,
                    kpct=g(s, 'strikeOuts') / bf, bbpct=g(s, 'baseOnBalls') / bf, era=s.get('era'), bf=bf)
# bullpens
PEN = {}
for t, r in raw['team'].items():
    t = int(t)
    for sp_ in r['pit']['stats'][0]['splits']:
        if sp_['split']['description'] == 'Reliever':
            x = sp_['stat']; bf = g(x, 'battersFaced')
            PEN[t] = dict(K=g(x, 'strikeOuts') / bf, BB=g(x, 'baseOnBalls') / bf, ops=g(x, 'ops'),
                          HR9=g(x, 'homeRunsPer9'))

# ---------------- per-hitter matchup ----------------
def hitter_profile(pid, pit, game_card, pen, park, slot_bat):
    """Return per-PA event probs vs starter and vs pen for this hitter."""
    pid = int(pid)
    r = raw['hit'][str(pid)]
    bio = r['bio']; bs = bio['batSide']['code']
    th = PIT[pit]['throws']
    side = bs if bs != 'S' else ('L' if th == 'R' else 'R')
    seas, spa = rates(first(r, 'season'))
    NLG = dict(K=60, BB=100, HBP=300, B1=150, B2=200, B3=300, HR=150)
    seas = {e: (seas[e] * spa + NLG[e] * LG[e]) / (spa + NLG[e]) for e in seas}
    spl = spl_pick(r, 'spl')
    hs, hpa = rates(spl.get('vs Left' if th == 'L' else 'vs Right', {}))
    pm_h, pm_bb, pm_k = PLAT[(side, th)]
    base = {}
    for ev in ('B1', 'B2', 'B3', 'HR', 'BB', 'K', 'HBP'):
        mult = pm_k if ev == 'K' else pm_bb if ev == 'BB' else 1.0 if ev == 'HBP' else pm_h
        prior = seas[ev] * mult
        if ev == 'B3': prior = .6 * prior + .4 * LG['B3'] * mult
        n0 = {'K': 100, 'BB': 150, 'HBP': 1e9, 'HR': 200}.get(ev, 250)
        base[ev] = (hs[ev] * hpa + n0 * prior) / (hpa + n0) if hs and ev != 'HBP' else prior
    # recent form: last 14 days before the slate date (regular season games)
    r14, pa14 = rates(first(r, 'd14'))
    w14 = min(.15, .6 * pa14 / (pa14 + 300)) if r14 else 0
    form = None
    if r14:
        for ev in ('B1', 'B2', 'HR', 'BB', 'K'):
            base[ev] = base[ev] * (1 - w14) + r14[ev] * w14
        st14 = first(r, 'd14')
        form = dict(pa=pa14, avg=st14.get('avg'), ops=st14.get('ops'), hr=int(g(st14, 'homeRuns')), h=int(g(st14, 'hits')),
                    k=int(g(st14, 'strikeOuts')), bb=int(g(st14, 'baseOnBalls')))
    # contact-quality (xBA/xSLG vs actual, half credit)
    xr = bx[pid]; ba, xba, slg, xslg = float(xr['ba']), float(xr['est_ba']), float(xr['slg']), float(xr['est_slg'])
    q1 = min(max((xba / ba) ** .4, .93), 1.07) if ba > 0 else 1
    qx = min(max((xslg / slg) ** .4, .90), 1.10) if slg > 0 else 1
    base['B1'] *= q1; base['B2'] *= qx; base['B3'] *= qx; base['HR'] *= qx
    # arsenal fit (per-pitch-type batter grid x pitcher usage), BetLogic raw numbers only
    fitmult, kfit, grid_note = 1.0, 1.0, None
    bl = next((b for b in game_card.get('batters', []) if b['id'] == pid), None)
    xwb = float(xr['est_woba'])
    if bl and bl['grid']:
        pg = {x['pitch']: x for x in game_card['pitcher']['grid']}
        tot = sum(x['usage'] for x in bl['grid']) or 1
        fx = fw = pw = 0
        parts = []
        for x in bl['grid']:
            u = x['usage'] / tot
            xw_p = ((x['xwoba'] or xwb) * x['pa'] + 40 * xwb) / (x['pa'] + 40)
            bw = ((x['whiff'] or 0) * x['pa'] + 30 * (pg.get(x['pitch'], {}).get('whiff', 25))) / (x['pa'] + 30)
            fx += u * xw_p; fw += u * bw; pw += u * pg.get(x['pitch'], {}).get('whiff', 25)
            parts.append((x['pitch'], x['usage'], x['pa'], x['xwoba'], x['slg'], x['whiff']))
        fitmult = min(max(fx / xwb, .85), 1.15) ** .6
        kfit = min(max(fw / pw, .88), 1.12) ** .5 if pw else 1
        grid_note = parts
    # pitcher adjustments vs starter
    P = PIT[pit]['hand'][side if side in 'LR' else 'R']
    pk, pbb, phr, pc = P['K'], P['BB'], P['HR'], P['contact']
    def build(pk_, pbb_, phr_, contact, hitfit, kf, park_h, park_hr):
        K = oratio(base['K'], pk_, LG['K']) * kf
        BB = oratio(base['BB'], pbb_, LG['BB'])
        HR = oratio(base['HR'], phr_, LG['HR']) * (contact ** .5) * (hitfit ** 1.0) * park_hr
        HBP = base['HBP']
        bip0 = 1 - base['K'] - base['BB'] - base['HBP'] - base['HR']
        bip1 = max(1 - K - BB - HBP - HR, .3)
        sc_ = bip1 / bip0 * contact * hitfit * park_h
        B1, B2, B3 = base['B1'] * sc_, base['B2'] * sc_, base['B3'] * sc_
        return dict(K=K, BB=BB, HBP=HBP, B1=B1, B2=B2, B3=B3, HR=HR)
    pk_h = PARK[park]
    # park HR factor by batter side, scaled by how much he pulls the ball in the air (league pulled-air share ~22%): a pull-side power bat
    # gets the full park effect, a spray hitter much less. BetLogic card gives pull-air % by pitch type; no card => neutral weight 1.0.
    hf = pk_h['hr'][side] if isinstance(pk_h['hr'], dict) else pk_h['hr']
    pull_air = None
    if bl and bl['grid']:
        num = sum((x.get('pullair') or 0) * x['usage'] * max(x['pa'], 1) for x in bl['grid']); den = sum(x['usage'] * max(x['pa'], 1) for x in bl['grid'])
        pull_air = num / den if den else None
    pull_w = min(max(pull_air / 22.0, .5), 1.6) if pull_air else 1.0
    park_hr_eff = (1 + pull_w * (hf - 1)) * WEATHER_HR
    vs_sp = build(pk, pbb, phr, pc, fitmult, kfit, pk_h['h'], park_hr_eff)
    pen_c = (pen['ops'] / LG_OPS_RP) ** .9
    vs_pen = build(pen['K'], pen['BB'], LG['HR'] * pen['HR9'] / 1.06 / 1.0, pen_c, 1.0, 1.0, pk_h['h'], park_hr_eff)
    # third-time-through penalty is applied in PA allocation (see below)
    # SB
    seasst = first(r, 'season')
    sb, cs = g(seasst, 'stolenBases'), g(seasst, 'caughtStealing')
    ob = g(seasst, 'hits') - g(seasst, 'homeRuns') + g(seasst, 'baseOnBalls') + g(seasst, 'hitByPitch')
    ss = float(sp[pid]['sprint_speed'])
    prior_sb = LG_SB_PER_OB * min(max((ss / 27.0) ** 5, .25), 3.0)
    sb_per_ob = (sb + 40 * prior_sb) / (ob + 40)
    sb_mult = (0.85 if th == 'L' else 1.0) * .88  # LHP holds runners; postseason attempt suppression
    return dict(id=pid, name=bio['fullName'], bats=bs, eff=side, vs_sp=vs_sp, vs_pen=vs_pen, base=base,
                sb_per_ob=sb_per_ob * sb_mult, sprint=ss, sb=sb, cs=cs, form=form, fit=fitmult, kfit=kfit, grid=grid_note,
                season_ops=seasst.get('ops'), season_pa=g(seasst, 'plateAppearances'),
                hand_ops=spl.get('vs Left' if th == 'L' else 'vs Right', {}).get('ops'), hand_pa=hpa,
                pull_air=pull_air, park_hr_eff=park_hr_eff, xwoba=xwb, brl=float(be[pid]['brl_percent']) if pid in be else None, hh=float(be[pid]['ev95percent']) if pid in be else None)

# ---------------- lineup/game assembly ----------------
BASE_PA = [4.65, 4.55, 4.45, 4.35, 4.25, 4.15, 4.05, 3.95, 3.85]
BASE_R = [.36, .35, .33, .31, .30, .29, .28, .27, .27]   # P(score | on base non-HR)
BASE_RUN = [.21, .34, .42, .42, .40, .37, .34, .30, .27]  # runners on avg when slot bats (non-leadoff via prior hitters)

def alloc_sp_pa(bf, n=9):
    out = [0.0] * n
    for w, b in ((.25, bf - 5), (.5, bf), (.25, bf + 5)):
        b = max(b, 3); base_, rem = int(b // n), b - n * int(b // n)
        for k in range(n): out[k] += w * (base_ + min(max(rem - k, 0), 1))
    return out

def project_team(hitters, pit_id, pen_team, park, bf_sp, team_pa, label, status):
    gc = card[pit_id]
    pen = PEN[pen_team]
    profs = [hitter_profile(h['id'], pit_id, gc, pen, park, None) | dict(slot=h['slot'], pos=h['pos']) for h in hitters]
    spPA = alloc_sp_pa(bf_sp)
    scale = team_pa / sum(BASE_PA)
    for i, p in enumerate(profs):
        pa = BASE_PA[i] * scale
        n_sp = min(spPA[i], pa); n_pen = pa - n_sp
        # times-through-order: PAs 3+ vs starter get +4% hit-type/ -3% K
        n3 = max(n_sp - 2, 0); n12 = n_sp - n3
        ev = {}
        for e in ('B1', 'B2', 'B3', 'HR', 'BB', 'HBP', 'K'):
            f3 = 1.0 if e in ('BB', 'HBP') else (0.97 if e == 'K' else 1.04)
            ev[e] = n12 * p['vs_sp'][e] + n3 * p['vs_sp'][e] * f3 + n_pen * p['vs_pen'][e]
        p['pa'] = pa; p['n_sp'] = n_sp; p['ev'] = ev
    # team run expectancy
    tr = 0
    for p in profs:
        e = p['ev']
        tr += sum(e[k] * LW[k] for k in LW) - p['pa'] * OUT_C
    park_r = PARK[park]['r']
    tr = tr * park_r
    # R / RBI distribution
    obp = [(p['ev']['B1'] + p['ev']['B2'] + p['ev']['B3'] + p['ev']['BB'] + p['ev']['HBP']) / p['pa'] + p['ev']['HR'] / p['pa'] for p in profs]
    wo = [sum(p['ev'][k] * LW[k] for k in LW) / p['pa'] for p in profs]
    lg_obp = .318; lg_w = .176
    rawR, rawRBI = [], []
    for i, p in enumerate(profs):
        e = p['ev']
        prev = [obp[(i - j) % 9] for j in (1, 2, 3)]
        nxt = [wo[(i + j) % 9] for j in (1, 2, 3)]
        runners = BASE_RUN[i] * (sum(prev) / 3 / lg_obp)
        sc_ = BASE_R[i] * (sum(nxt) / 3 / lg_w) ** .8
        onb = e['B1'] + e['B2'] + e['B3'] + e['BB'] + e['HBP']
        speed = (p['sprint'] / 27.0) ** 2
        rawR.append(e['HR'] + onb * sc_ * speed ** .5)
        biploss = max(p['pa'] - e['K'] - e['BB'] - e['HBP'] - e['HR'] - e['B1'] - e['B2'] - e['B3'], 0)
        rawRBI.append(e['HR'] * (1 + runners) + (e['B1'] * .42 + e['B2'] * .62 + e['B3'] * .9) * runners + biploss * .05 * runners + e['BB'] * .02 * runners)
    kR = tr * 0.99 / sum(rawR); kB = tr * .94 / sum(rawRBI)
    for i, p in enumerate(profs):
        e = p['ev']
        p['H'] = e['B1'] + e['B2'] + e['B3'] + e['HR']
        p['R'] = rawR[i] * kR; p['RBI'] = rawRBI[i] * kB
        ob = e['B1'] + e['B2'] + e['B3'] + e['BB'] + e['HBP']
        p['SB'] = ob * p['sb_per_ob']
        p['K'] = e['K']; p['BB'] = e['BB']; p['HR'] = e['HR']
        p['HRR'] = p['H'] + p['R'] + p['RBI']
        p['label'] = label; p['status'] = status
    return profs, tr

def starter_line(pit_id, opp_profs, bf):
    """K and IP for starter from the opponent's per-PA vs-starter probabilities."""
    spPA = alloc_sp_pa(bf)
    K = sum(spPA[i] * p['vs_sp']['K'] * (1 if i < 6 else 1) for i, p in enumerate(opp_profs))
    reach = sum(spPA[i] * (p['vs_sp']['B1'] + p['vs_sp']['B2'] + p['vs_sp']['B3'] + p['vs_sp']['HR'] + p['vs_sp']['BB'] + p['vs_sp']['HBP']) for i, p in enumerate(opp_profs))
    H = sum(spPA[i] * (p['vs_sp']['B1'] + p['vs_sp']['B2'] + p['vs_sp']['B3'] + p['vs_sp']['HR']) for i, p in enumerate(opp_profs))
    BB = sum(spPA[i] * p['vs_sp']['BB'] for i, p in enumerate(opp_profs))
    HR = sum(spPA[i] * p['vs_sp']['HR'] for i, p in enumerate(opp_profs))
    outs = bf - reach
    return dict(bf=bf, K=K, H=H, BB=BB, HR=HR, IP=outs / 3)

if __name__ == '__main__':
    pass
