# Daily Discovery Queue

## Purpose

The Daily Discovery Queue is the mandatory research gate between external alerts and the HitterDNA candidate filter table.

No alert, screenshot, manual note, batter-versus-pitcher split, composite score, market movement, or third-party dashboard signal may enter a recommendation directly.

## Flow

external source -> queue validation -> candidate filter table -> fair-probability model -> market devig -> edge-ranked shortlist -> ledger

## Permitted sources

- Baseball Savant
- MLB Stats API
- FanGraphs daily projections
- Statcast park factors
- Verified weather source
- Market feed
- Approved third-party discovery tools, including Barrellab, only after raw-feature decomposition

## Prohibited evidence

- Hit streaks
- Last-seven or last-night results
- Drought logic
- Batter-versus-pitcher evidence below 50 PA
- Opaque composite scores without raw components
- Unsourced screenshots
- Market-price recollection or estimates

## BvP policy

BvP below 50 PA is stored only as: DESCRIPTIVE ONLY, NOT PREDICTIVE.

It receives zero model weight. The replacement is hitter performance against the opposing pitcher's projected arsenal, velocity bands, handedness, and locations.

## Advancement rules

A record advances from INVESTIGATE to ADVANCE only when:

1. The player MLBAM ID and gamePk resolve.
2. The source URL and retrieval timestamp exist.
3. The sample type and sample size exist.
4. The feature maps to a defined model feature.
5. Stabilization status is PASS or the model explicitly permits a shrinkage treatment.
6. The hitter has a CONFIRMED lineup status.
7. The candidate does not duplicate an existing feature without an explicit correlation treatment.

A record advances from ADVANCE to PRICED only when:

1. Book, line, price, and timestamp exist.
2. The fair probability is computed through the analysis tool.
3. Devig method and devigged market probability are stored.
4. Edge is stored in percentage points.

## Required dispositions

- INVESTIGATE: valid lead awaiting source or feature validation
- ADVANCE: validated feature eligible for filter table
- HOLD: valid but awaiting lineup, weather, or price
- DROP: invalid, prohibited, duplicate, or inadequate sample
- ARCHIVED: completed slate record
