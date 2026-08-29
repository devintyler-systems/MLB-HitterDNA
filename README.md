## Clean-machine setup

From PowerShell, create and install the editable development environment:

```powershell
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -c "import hitterdna; print(hitterdna.__file__)"
python -m pytest -q
python -m hitterdna.smoke_test --date 2026-08-29
```

The smoke test only reads the MLB Stats API and prints compact JSON to stdout.
Each game is retained by its `game_pk` event key, including doubleheaders. Only
`eligible_refresh` and `urgent_refresh` may proceed to lineup refresh; all
other freshness states are blocked from pregame candidate generation.

The read-only pipeline is: schedule -> freshness gate -> confirmed-lineup
ingestion -> candidate filter table -> model -> market evaluation. Only players
with an explicit `confirmed` lineup status pass to candidate filtering.
`UNCONFIRMED` is an exclusion state, never a lineup forecast; `game_pk` is the
sole event key, with no team/date deduplication.
