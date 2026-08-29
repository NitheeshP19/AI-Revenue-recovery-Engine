# ==============================================================================
#  AI Revenue Recovery Engine — Makefile
#  Cross-platform task runner. Requires GNU make (pre-installed on Linux/macOS;
#  install via `choco install make` or `winget install GnuWin32.Make` on Windows).
# ==============================================================================

.PHONY: help train test test-go test-python test-dashboard dev-go dev-ml dev-dashboard docker-up docker-down lint ci clean

# ── Default target: show help ─────────────────────────────────────────────────
help:
	@echo ""
	@echo "  AI Revenue Recovery Engine — Available Targets"
	@echo "  ────────────────────────────────────────────────"
	@echo "  make train          Train the XGBoost ML classifier (generates ml/classifier.json)"
	@echo "  make test           Run all three test suites (Go + Python + Dashboard)"
	@echo "  make dev-go         Start the Go Ingestion API (port 8080)"
	@echo "  make dev-ml         Start both Python ML services (ports 8001 + 8002)"
	@echo "  make dev-dashboard  Start the Vite React dashboard (port 5173)"
	@echo "  make docker-up      Start all services via Docker Compose"
	@echo "  make docker-down    Stop and remove Docker Compose services"
	@echo "  make lint           Run linters (go vet + oxlint)"
	@echo "  make clean          Remove build artifacts"
	@echo ""

# ── ML Model Training ─────────────────────────────────────────────────────────
# Generates ml/classifier.json, ml/feature_cols.json, ml/label_map.json
# Required before starting the ML Inference Service.
train:
	@echo "Training XGBoost classifier..."
	cd ml && python train_model.py
	@echo "✅ Training complete. ml/classifier.json generated."

# ── Test Suites ───────────────────────────────────────────────────────────────
test: test-go test-python test-dashboard

test-go:
	@echo "[1/3] Running Go unit tests..."
	cd go-api && go test ./... -v -count=1
	@echo "✅ Go tests passed."

test-python:
	@echo "[2/3] Running Python service tests..."
	cd ml && python -m pytest test_services.py -v
	@echo "✅ Python tests passed."

test-dashboard:
	@echo "[3/3] Running dashboard lint + tests..."
	cd dashboard && npm run lint && npm test
	@echo "✅ Dashboard tests passed."

# ── Development Servers ───────────────────────────────────────────────────────
dev-go:
	@echo "Starting Go Ingestion API on :8080..."
	cd go-api && go run .

dev-ml:
	@echo "Starting ML services (inference :8001, agent :8002)..."
	cd ml && python inference_service.py &
	cd ml && python agent_service.py

dev-dashboard:
	@echo "Starting Vite dashboard on :5173..."
	cd dashboard && npm run dev

# ── Docker Compose ────────────────────────────────────────────────────────────
docker-up:
	@echo "Starting all services via Docker Compose..."
	docker compose up --build

docker-down:
	docker compose down --remove-orphans

# ── Linting & CI ─────────────────────────────────────────────────────────────
lint:
	cd go-api && go vet ./...
	cd dashboard && npm run lint

ci: lint test
	@echo "======================================================"
	@echo "  ✅ ALL CI CHECKS (LINT + TESTS) PASSED"
	@echo "======================================================"

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	rm -f go-api/api go-api/api.exe go-api/main go-api/main.exe
	@echo "✅ Build artifacts cleaned."
