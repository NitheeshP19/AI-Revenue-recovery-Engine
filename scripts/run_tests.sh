#!/usr/bin/env bash
# ==============================================================================
#  AI Revenue Recovery Engine — Cross-Platform Test Runner
#  Runs all three test suites: Go API + Python Services + Dashboard
#
#  Usage:
#    bash scripts/run_tests.sh
#    # or with make:
#    make test
#
#  Requirements: go, python3/pip, node/npm must be on PATH.
# ==============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PASS=0
FAIL=0

print_section() {
    echo ""
    echo "======================================================"
    echo "  $1"
    echo "======================================================"
}

run_step() {
    local label="$1"
    shift
    echo ""
    echo "→ $label"
    if "$@"; then
        echo "✅ PASSED: $label"
        PASS=$((PASS + 1))
    else
        echo "❌ FAILED: $label"
        FAIL=$((FAIL + 1))
        # Don't exit immediately — run all suites and report aggregate.
    fi
}

print_section "AI Revenue Recovery — Full Validation Suite"

# ── 1. Go unit tests ──────────────────────────────────────────────────────────
print_section "1/3 — Go Unit Tests (go-api/)"
run_step "go test ./... -v -count=1" \
    bash -c "cd '$ROOT/go-api' && go test ./... -v -count=1"

# ── 2. Python service tests ───────────────────────────────────────────────────
print_section "2/3 — Python Service Tests (ml/)"
run_step "pytest test_services.py -v" \
    bash -c "cd '$ROOT/ml' && python -m pytest test_services.py -v"

# ── 3. Dashboard: lint + vitest ───────────────────────────────────────────────
print_section "3/3 — Dashboard Tests (dashboard/)"
run_step "npm run lint" \
    bash -c "cd '$ROOT/dashboard' && npm run lint"
run_step "npm test (vitest)" \
    bash -c "cd '$ROOT/dashboard' && npm test"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "======================================================"
if [ "$FAIL" -eq 0 ]; then
    echo "  ✅ ALL $PASS CHECKS PASSED"
else
    echo "  ⚠️  $PASS passed, $FAIL FAILED"
fi
echo "======================================================"
echo ""

[ "$FAIL" -eq 0 ]
