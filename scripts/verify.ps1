$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "backend")
uv run ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run ruff format --check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run mypy app
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Set-Location (Join-Path $root "frontend")
npm test
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm run typecheck
exit $LASTEXITCODE
