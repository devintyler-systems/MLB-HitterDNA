# dfs_ceiling — ceiling-first DraftKings Showdown tooling (experimental)

Separate from the HitterDNA audit engine in `src/` (which deliberately emits no projections). This directory holds a
standalone pipeline for **tournament** Showdown lineups: instead of maximizing a lineup's average score it maximizes the
probability the lineup **beats the best entry in the field** (or finishes top 1% / 5%).

```
python fetch_inputs.py slates/<slate>.json <data_dir>      # statsapi + Savant + BetLogic raw numbers (no odds/lines read)
python build_events.py slates/<slate>.json <data_dir>      # per-PA event odds per hitter vs starter and vs pen
python simulate.py <data_dir> 40000                        # full-game Monte Carlo -> DK points per player per game
python optimize.py <data_dir> DKSalaries.csv --portfolio 4 # ceiling optimizer (+ multi-entry portfolio)
python score_actuals.py <game_pk>                          # actual DK points after the game (backtesting)
python calibrate_field.py <data_dir> <standings.csv>       # score the REAL field in the sims, compare to reality
python field_study.py <standings.csv> <game_pk>            # who finishes top 1%: captain type, stacks, ownership
```
Needs `numpy` (`pip install -r requirements.txt`). `optimize.py` options: `--objective win|top1|top5`, `--field-lineups
<DK standings csv>` (score the REAL field inside the sims), `--ownership name,cpt_pct,util_pct.csv`, `--exclude "A,B"`
(late scratches), `--allow-relievers`. Rebuild events + sims after any lineup change.

## How it works
1. `build_events.py` → per-PA K/BB/HBP/1B/2B/3B/HR odds (season + platoon split + last-14 form + Savant contact quality +
   per-pitch-type arsenal fit + park/weather + a capped BvP nudge). Starter vs bullpen odds are separate.
2. `simulate.py` plays whole games PA-by-PA with base/out states (steals, DPs, sac flies, extras with a ghost runner),
   starter leash drawn per game, relievers drawn from `slates/*.json` weights, plus game-level shocks (offense form, starter
   form, hitter form, hitter power) so tails are realistic and teammates are correlated.
3. `optimize.py` scores a field of lineups inside the same simulated games, then searches for the lineup (and portfolio)
   with the highest chance of beating the field. Search runs on half the sims; reported numbers are on the held-out half.

## Calibration (the part to trust, and its limits)
**Player-level backtest** (`backtest_games.py` + `calibration_report.py`): 377 final games, 9/1-10/5/2026, 6,785 hitter-games, 754 starter-games, simulated from
pre-game-style inputs and compared with each player's actual DK points. With the shipped defaults:

| check | actual | sim |
|---|---|---|
| hitter DK points / game | 6.84 | 6.82 (ratio 1.003 +/- 0.013; 1.00 in slots 1-2, 3-5 and 6-9; second half alone 0.993) |
| starting-pitcher DK points | 12.20 | 12.36 (ratio 0.987 +/- 0.032) |
| hitter games at 0 pts / >=10 / >=17 / >=24 | 23.8 / 26.8 / 10.6 / 3.1 % | 24.4 / 27.3 / 10.5 / 3.1 % |
| game total runs (mean / SD) | 9.02 / 4.35 | 8.96 / 4.32 |
| hits, HR, BB+HBP, SB, R, RBI per game | 16.43, 2.32, 7.50, 1.35, 9.02, 8.60 | 16.29, 2.29, 7.42, 1.34, 8.96, 8.55 |
| 80% interval coverage (hitters / pitchers) | | 85% / 80% |
| `game_level_check.py`: z-score SD of game-mean hitter points | | 0.95 (single-team positions uniform; 20% outside 10-90% as expected) |

How it got there (each step is a knob in `simulate.py`, all env-overridable): the raw model ran ~8% hot on hitter points, but that hid offsetting errors
(hits +6%, HR +9%, BB +6%, RBI +8%, **steals -60%**). `fit_components.py` fits one knob per box-score component on all 377 games
(`DFS_OFFENSE_SCALE`, `DFS_HR_SCALE`, `DFS_BB_SCALE`, `DFS_SB_SCALE`, `DFS_ADV_SCALE` runner advancement, `DFS_WILD_RATE` no-RBI runs); a late-replacement
hazard (`DFS_SUB_SCALE`) fixes starters' share of team production (starters were ~5% too productive, worst at the bottom of the order).
Fitting one half and testing the other overfit run totals (halves differ ~5% from sampling noise alone), so the shipped values use all games.

**Known remaining misses**
- **Division-series hitters scored ~24% below the sim** (4.76 vs 6.27 DK pts, n=144 hitter-games, 8 games, ~3 SE). Wild-card hitters matched (ratio 0.995, n=162). `DFS_POSTSEASON_SCALE` (default 1.0, try 0.9) exists but is NOT applied: too few playoff games to trust it.
- **Look-ahead and neutral inputs in the backtest**: season/platoon stats are as-of-today (a game's own result is inside its inputs), BetLogic arsenal fit is unavailable historically, park factors neutral, relievers generic. So live accuracy can be somewhat worse than the table.
- **Reliever usage is a guess** in live slates; relievers are excluded from lineups by default (`--allow-relievers`). Reliever wins are not simulated.
- **The field is a model** unless you pass real ownership (`--ownership`) or a real standings export (`--field-lineups`); the proxy fit real ownership poorly (R^2 0.26).
- Shock sizes (`SIG`) sit on a flat valley in the fit (0.8x-1.0x indistinguishable); 1.0 kept.

**Field-level checks on real contests** (`calibrate_field.py`, one game each, so noisy): 10/6 MIL@SD $10K (2,366 entries): sim field median 46 vs 38 real, winning score
92 vs 82 (real winner at the 28th percentile of the sim). 9/30 CHC@SD $8K (9,471 entries): sim median 52 vs 27, winning score 102 vs 65.5; that game's hitters were ~1.4 SD
below the sim's expectation (CHC scored 1 run), consistent with the game-level check rather than a structural miss.

## Lineup-construction rules, tested on real fields (`rule_backtest.py`, 11.8K entries, 2 contests, top-1% rate lift vs base)
Consistent in both contests: chalk SP anywhere in the lineup (1.2x, 1.6x); hitter captain with <5% field captain share (2.2x, 1.5x). Not consistent: 5-stacks (0.9x vs 1.9x; tracks which team won).
Chalk SP as captain pooled 1.8x (so "fade the pitcher captain" is NOT supported). Combined leverage rule (chalk SP in UTIL + hitter CPT <10% + 4-stack) pooled 1.9x, CIs overlap the single rules.
Optimizer modes: `--mode ceiling` (unconstrained P(beat field)), `leverage`, `leverage5`; `compare_modes.py` runs them side by side. On 10/6 the three modes' top-3 lineups would have
finished at average real ranks of ~1150 (ceiling), ~1280 (leverage), ~620 (leverage5) of 2,366: one slate, mostly luck, logged for data not as a conclusion.
