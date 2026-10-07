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

## Validation on 2026-10-06 MIL@SD Showdown ($10K, 2,366 entries) — one slate, anecdotal
- Simulated tails vs the real game: sim expects 4.5 hitters >=10 DK pts and 1.6 >=17; actual 5 and 2. Sim mean total runs 7.6 vs 7 actual.
- Real field scored inside the sims: sim field median 47.6 / p90 69.7 vs real 38.2 / 55.9 (chalk bats Salas, Tatis, Chourio, France all busted that night); sim's expected winning score 93 vs real 81.75 (real winner sits at the 26th percentile).
- The real top-10 finishers rate 1.3-2.2x the average entry's simulated win probability (rank corr 0.26): the sim sees what wins, but luck dominates one game.
- Tool picks scored 30-52 actual points. A single result neither validates nor invalidates the approach.

### Second, out-of-sample check: 2026-09-30 CHC@SD $8K Mini-Max (9,471 entries) — the sim ran HOT
`python calibrate_field.py` scores the real field inside the sims. Here the sim's field median was 53.6 vs 27.0 real, and its median winning score 104 vs 65.5 real (real winner is a ~1% outcome in the sim). The game was low-scoring (5 runs vs a sim mean of 8.3) and both starters were pulled at 15 batters faced (config assumed 21), so part of the miss is one low game and a config guess, not the model. Pooled over both games, actual hitter points averaged 5.0 vs 6.7 simulated (36 hitter-games, ~1.8 SE below), while total runs across the six games with data (7, 5, 8, 9, 11, 10) average 8.3, in line with the sim's 7.6-8.3. No recalibration has been done: two games cannot separate bias from luck. Treat absolute win probabilities as overstated for the highest-variance lineups until more slates are logged with `calibrate_field.py`.

## Known limitations (read before trusting a number)
- **Reliever usage is a guess** (`relievers`/`bulk_relievers` in the slate config). The optimizer exploits guessed-cheap-ceiling relievers, so they are excluded from lineups by default. Reliever wins are not simulated.
- **The field is a model.** Pass real/projected ownership (`--ownership`) when you have it; the built-in proxy (AvgPointsPerGame**p, captain propensity ~ salary**2.2) fit real ownership poorly (R^2 0.26 on this slate).
- Win probabilities are small by nature (~0.3-0.4% for the best single lineup in a ~2.4K field, ~25x an average entry); a 4-lineup portfolio covers ~1.0-1.3%.
- BvP and the arsenal-fit data are small-sample inputs; shock sizes (`SIG` in simulate.py) are judgment calls, not fitted.
- BetLogic pitcher cards exist only for current probables, so historical re-runs need previously saved `raw/p_<id>.json`.
