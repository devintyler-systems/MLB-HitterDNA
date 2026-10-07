"""Build per-PA event probabilities + starter/pen parameters for one game -> <data_dir>/events.json
usage: python build_events.py slates/<slate>.json <data_dir>
"""
import json, sys, os, importlib.util
cfg_path, D = sys.argv[1], sys.argv[2]
os.environ['DFS_DATA_DIR'] = D; os.environ['DFS_SLATE_CONFIG'] = cfg_path
cfg = json.load(open(cfg_path))
spec = importlib.util.spec_from_file_location('pm', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'projection_model.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
lin = json.load(open(f"{D}/lineups.json"))['lineups']
BVP = {int(k): v for k, v in cfg.get('bvp', {}).items()}

orig = m.hitter_profile
def wrapped(pid, pit, gc, pen, park, sb):  # tiny BvP nudge (150-AB-equivalent prior, clipped)
    p = orig(pid, pit, gc, pen, park, sb)
    if int(pid) in BVP:
        ab, h = BVP[int(pid)]; v = p['vs_sp']
        ph = (v['B1'] + v['B2'] + v['B3'] + v['HR']) / (1 - v['BB'] - v['HBP'])
        mult = min(max(((h + 150 * ph) / (ab + 150)) / ph, .95), 1.08)
        for e in ('B1', 'B2', 'B3', 'HR'): v[e] *= mult
    return p
m.hitter_profile = wrapped

out = {'date': cfg['date'], 'game_pk': cfg['game_pk'], 'teams': {}, 'relievers': cfg['relievers'], 'bulk': cfg['bulk_relievers']}
# away hitters face the HOME starter and vice versa
for side, opp in (('away', 'home'), ('home', 'away')):
    t, o = cfg[side], cfg[opp]
    if len(lin[side]) != 9: continue
    hitters = [dict(id=h['id'], slot=h['slot'], pos=h['pos']) for h in lin[side]]
    profs, tr = m.project_team(hitters, o['starter_id'], o['team_id'], cfg['park'], o['starter_bf_mean_sd'][0], t['team_pa'], 'G', 'confirmed')
    out['teams'][t['abbr']] = dict(
        side=side, opp=o['abbr'], opp_starter=m.PIT[o['starter_id']]['name'], opp_starter_id=o['starter_id'],
        opp_bf_mean_sd=o['starter_bf_mean_sd'], proj_runs=tr,
        hitters=[dict(id=p['id'], name=p['name'], slot=p['slot'], pos=p['pos'], vs_sp=p['vs_sp'], vs_pen=p['vs_pen'],
                      sb_per_ob=p['sb_per_ob'], sprint=p['sprint'],
                      proj=dict(H=p['H'], R=p['R'], RBI=p['RBI'], K=p['K'], BB=p['BB'], SB=p['SB'], HR=p['HR'], HRR=p['HRR'], PA=p['pa'])) for p in profs])
json.dump(out, open(f"{D}/events.json", 'w'), indent=1)
print('events.json:', {k: len(v['hitters']) for k, v in out['teams'].items()})
