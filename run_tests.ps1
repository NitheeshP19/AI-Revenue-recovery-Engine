<#
.SYNOPSIS
  Runs the full local validation suite for the AI Revenue Recovery Engine.

.DESCRIPTION
  Executes tests for all three service layers:
    1. Go API        — go test ./...
    2. ML Services   — pytest ml/test_services.py
    3. Dashboard     — oxlint + vitest

.USAGE
  From the project root:
    powershell -ExecutionPolicy Bypass -File run_tests.ps1
#>

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  AI Revenue Recovery — Full Validation Suite" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan

# ── 1. Go unit tests ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[1/3] Running Go unit tests (go-api/)..." -ForegroundColor Yellow
Push-Location "$root\go-api"
go test ./... -v -count=1
if ($LASTEXITCODE -ne 0) { Write-Host "Go tests FAILED" -ForegroundColor Red; exit 1 }
Pop-Location
Write-Host "Go tests PASSED" -ForegroundColor Green

# ── 2. Python service tests ───────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/3] Running Python service tests (ml/)..." -ForegroundColor Yellow
Push-Location "$root\ml"
python -m pytest test_services.py -v
if ($LASTEXITCODE -ne 0) { Write-Host "Python tests FAILED" -ForegroundColor Red; exit 1 }
Pop-Location
Write-Host "Python tests PASSED" -ForegroundColor Green

# ── 3. Dashboard: lint + test + build ─────────────────────────────────────────
Write-Host ""
Write-Host "[3/3] Running Dashboard lint, tests, and build (dashboard/)..." -ForegroundColor Yellow
Push-Location "$root\dashboard"

Write-Host "  → oxlint..." -ForegroundColor Gray
npm run lint
if ($LASTEXITCODE -ne 0) { Write-Host "Dashboard lint FAILED" -ForegroundColor Red; exit 1 }

Write-Host "  → vitest..." -ForegroundColor Gray
npm test
if ($LASTEXITCODE -ne 0) { Write-Host "Dashboard tests FAILED" -ForegroundColor Red; exit 1 }

Write-Host "  → vite build (bundle size check)..." -ForegroundColor Gray
npm run build
if ($LASTEXITCODE -ne 0) { Write-Host "Dashboard build FAILED" -ForegroundColor Red; exit 1 }

Pop-Location
Write-Host "Dashboard checks PASSED" -ForegroundColor Green

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  ALL CHECKS PASSED" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Cyan
