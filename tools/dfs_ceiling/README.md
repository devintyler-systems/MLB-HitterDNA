# dfs_ceiling — ceiling-first DraftKings Showdown tooling (experimental)

Separate from the HitterDNA audit engine in `src/` (which deliberately emits no projections). This directory holds a
standalone pipeline for **tournament** Showdown lineups: instead of maximizing a lineup's average score it maximizes the
probability the lineup **beats the best entry in the field** (or finishes top 1% / 5%).

```
python fetch_inputs.py slates/<slate>.json <data_dir>      # statsapi + Savant + BetLogic raw numbers (no odds/lines read)
python build_events.py slates/<slate>.json <data_dir>      # per-PA event odds per hitter vs starter and vs pen
python simulate.py <data_dir> 40000                        # full-game Monte Carlo -> DK points per player per game
python optimize.py <data_dir> DKSalaries.csv --portfolio 4 # ceiling optimizer (+ multi-entry portfolio)
python build_pen.py slates/<slate>.json                    # reliever-usage weights from the series' rest/workload (writes into the slate config)
python slate_report.py slates/<slate>.json <data_dir>      # per-hitter / per-starter projections, ceiling odds, why-lines -> report.md, hitters.csv, pitchers.csv
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

## 2026-10-07 additions
- **Form now spans regular season + postseason**: statsapi will not union game types in `byDateRange`, so `fetch_inputs.py` pulls R/F/D/L/W separately and merges (before this, a hitter's "last 14 days" ignored this week's playoff games).
- **Park HR factors by batter side and pull tendency**: `park_factors.hr` may be `{"L": x, "R": y}`; each hitter's effect is scaled by his pulled-air share (BetLogic card, league ~22%), so a pull-side lefty gets the full Yankee Stadium porch effect and a spray hitter little. `weather_hr` is a per-game temperature/wind multiplier (~+0.25%/F vs 72F; ~0.9%/mph along the axis).
- **Postseason scale**: 19 playoff games (342 hitter-games, through 10/6): raw sim ratio 0.885 pooled; wild card 0.993, division series 0.788. `DFS_POSTSEASON_SCALE` 0.90 fits the pooled sample, 0.80 the division series alone. Tonight's headline uses 0.88 with 1.0 and 0.80 as sensitivity (`sims_ps*.npz`). Not fitted to enough games to trust beyond +/-0.05.
- `simulate.py` now also saves per-hitter components (H, HR, R, RBI, BB, K, SB, PA) and per-starter lines (outs, K, ER, H, BB, relievers used).

## 2026-10-08 additions: ownership proxy, chalk-captain mode, TB@NYY result
- **`fit_ownership.py` + `ownership_proxy.json`**: log(total ownership) ~ log(our simulated DK mean) + log(salary) + pitcher + top-3 slot + AvgPointsPerGame + cheap-regular-hitter flag (ridge). Fit on the 10/6 MIL@SD and 10/7 TB@NYY Showdown fields: out-of-sample R2 0.88/0.89 (fit on one slate, predict the other), captain propensity ~ salary^2.6. This replaces the old AvgPointsPerGame**p proxy (R2 0.26) as the default field model in `optimize.py` (`--legacy-own` restores it). Two slates, so treat it as promising, not settled; the cheap-regular feature was added after seeing a miss on the second slate.
- **`--mode chalkcpt`**: captain forced to the most-owned starting pitcher. Pooled over three real fields, chalk-SP-as-captain showed a 1.8x top-1% lift (1.1x, 2.0x, 2.4x), owning the chalk SP in any slot 1.5x, hitter captains under 5% share 1.6x (but 0 of 68 on 10/7), 4+/5+ stacks 1.2x/1.7x (tracks which team won).
- **10/7 TB@NYY $1K Showdown (277 entries)**, one lineup entered (CPT Rice, rank 87, 52.05). Our four-lineup portfolio would have scored 52.1 / 38.1 / 25.5 / 50.0 (ranks 87, 202, 254, 113). The top seven finishers all captained Fried (42% of the field). Sim vs reality: hitters 5.33 actual vs 5.52 simulated, Fried 14.55 vs 14.77, Martinez 6.65 vs 11.26, total runs 7 vs 6.7; field median 45.5 vs 48.2 sim, p90 60.0 vs 68.7. The sim gave the real top-7 entries 0.78% win probability vs a 0.51% field average.
- **Pre-lock test with the proxy (fit on 10/6 only)**: lineups chosen against the proxy field finished at mean real rank 50 (ceiling), 96 (leverage5), 111 (chalkcpt) of 277, vs 164 for the portfolio built against the old proxy. One slate; do not over-read it.

## 2026-10-08 (night): MIL@SD result, postseason scale, reliever wins
- **10/7 MIL@SD $8K Mini-Max (9,461 entries)**: all four portfolio lineups entered. Results: CPT Tatis 50.35 (rank 1442, top 15%), CPT Gasser/Chourio stack 44.90 (2644), CPT Buehler SD stack 37.63 (4820), CPT Gasser/both starters 34.65 (5765). Winner 83.15 (CPT Tatis, Gasser, Bauers, Yelich, Frelick, plus $4,000 reliever Ashby: 1 W, 3 K, 15.55). MIL won 3-1.
- **Ownership proxy holds out of sample**: on a slate it never saw (MIL@SD), the 10/6 + 10/7-TB@NYY fit predicted real total ownership with R2 0.89. Refit on all three slates; every cross-slate fit scores R2 0.82-0.88 (`fit_ownership.py`).
- **Postseason scale for division-series games is ~0.80, not 0.88**: tonight's two games pooled actual/sim hitter points were 0.80 at scale 1.0 (0.93 at 0.88, 1.03 at 0.80); the 19-game backtest said 0.80 for the division series alone. Slate configs now carry `postseason_scale` (read by `build_events.py`/`simulate.py`; env `DFS_POSTSEASON_SCALE` overrides). With 0.80 the real-field median matches closely (TB@NYY 46.2 sim vs 45.5 real; MIL@SD 40.7 vs 38.0), but the sim's 90th-percentile lineup score still runs 10-19% above reality.
- **Not an over-projection of stars**: backtest hitters bucketed by simulated mean show actual/sim ratios 0.95-1.04 in every bucket, calibration slope 1.07.
- **Pitcher-of-record wins are now simulated** (the pitcher on the mound when the winner took its final lead; a starter needs 15 outs, else the next pitcher), so relievers can earn the +4. Starter calibration unchanged on the 377-game backtest (12.36 sim vs 12.20 real).
- **Relievers stay excluded by default**: across four real fields the top-1% rate of entries holding a non-starter was 0.24% vs 1.18%, 2.30% vs 0.83%, 0% vs 1.54%, 1.26% vs 0.99% (no consistent edge). Simulated reliever means are 1-3 DK points at $4,000.
- **Mode comparison on this slate (real field, one game)**: average real rank of each mode's top-3: chalkcpt ~2,175, ceiling ~5,137, leverage5 ~7,954 of 9,461. Pooled over four fields chalk-SP-as-captain still shows the strongest lift (~1.8x). Treat all of it as noisy.
- `compare_modes.py --extra=--allow-relievers` (use the `=` form) passes flags through to `optimize.py`.
