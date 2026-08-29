## Read-only schedule smoke test

From PowerShell, run the Stats API schedule check for a date (with optional
away and home team-abbreviation filters):

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m hitterdna.smoke_test --date 2026-08-29 --away-team HOU --home-team NYM
```

The command only reads the MLB Stats API and prints compact JSON to stdout. It
returns a non-zero exit code for an empty, malformed, or unnormalizable result.
