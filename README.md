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
