# Changelog

All notable changes to the AI Revenue Recovery Engine are documented here.

---

## [Unreleased] — Buildathon Fixes (2026-08-25)

### Added
- **Real Razorpay Integration** (`go-api/razorpay_webhook.go`)
  - `POST /api/v1/webhooks/razorpay` endpoint with HMAC-SHA256 signature verification
  - Parses Razorpay's `payment.failed` webhook payload shape
  - Calls Razorpay Payment Links API (sandbox/test mode) and logs `payment_link_id`, `status`, `short_url` to `razorpay_recovery_actions` table
- **Fallback Transparency** (`ml/agent_service.py`, `simulation_engine.py`)
  - `fallback_status: "agent_unavailable_fallback"` field on `DecisionResponse` when Groq is unavailable
  - Simulation engine now returns `is_fallback` flag per decision and excludes fallback transactions from headline AI recovery rate
  - Separate `fallback_bucket` in `metrics_summary.json` output
  - Dashboard amber warning banner when `meta.agent_degraded == true`
- **Honest Benchmark Labels** (`README.md`, dashboard)
  - Revenue Lift and Recovery Rate labelled as "simulated projection based on a modeled outcome-probability matrix"
  - New "📐 Methodology & Limitations" section in README
  - Methodology disclaimer chip in dashboard below KPI cards
- **Cross-platform Test Runner** (`scripts/run_tests.sh`, `Makefile`)
  - Bash equivalent of `run_tests.ps1` (works on Linux/macOS/WSL)
  - `Makefile` with `make train`, `make test`, `make dev-go`, `make dev-ml`, `make dev-dashboard`
- **Integration Tests** (`go-api/handlers_test.go`, `ml/test_services.py`)
  - Razorpay webhook signature tests: valid, invalid, missing
  - Fallback status field assertion tests
  - Fallback banner logic / simulation engine unit test
- **CORS Hardening** (all services)
  - Default `ALLOWED_ORIGINS` changed from `*` to `http://localhost:5173`
  - Wildcard only allowed when `ALLOW_INSECURE_CORS=true` is explicitly set

### Changed
- `go-api/main.go`: CORS defaults to `localhost:5173`; registers `/api/v1/webhooks/razorpay` route
- `ml/agent_service.py`: CORS defaults to `localhost:5173`; `DecisionResponse` gains `fallback_status` field
- `docker-compose.yml`: `ml-service` `ALLOWED_ORIGINS` no longer hardcoded to `*`
- `k8s-manifest.yaml`: CORS origins restricted to `localhost:5173` placeholder
- `.env.example` / `go-api/.env.example`: Razorpay key placeholders added; `ALLOWED_ORIGINS` restricted

### Removed (from git index)
- `go-api/api.exe` — compiled binary (18 MB); now in `.gitignore`
- `ml/classifier.json` — large ML artifact (7.5 MB); regenerate with `make train`

---

## [1.0.0] — Initial Submission (2026-08-20)

### Phase 1 — Data Schema & Go Ingestion API
- PostgreSQL schema with ENUM types, GORM models
- `POST /api/v1/events/failure` with Contract A validation
- Exponential backoff DB connection, structured JSON logging (slog)

### Phase 2 — Synthetic Data Generator
- `synthetic_data_generator.py` — realistic failed transaction CSV (10k rows)
- Stratified distributions across 6 failure reasons and 6 payment methods

### Phase 3 — ML Inference Service
- XGBoost multi-class classifier for failure root cause prediction
- `train_model.py` with feature engineering and label encoding
- FastAPI inference service at `POST /predict` (Contract B → Contract C)

### Phase 4 — LLM Agent Service (Groq)
- FastAPI Groq agent at `POST /agent/decide`
- System prompt engineering for structured JSON output
- Rate limit handling with rule-based fallback engine

### Phase 5 — Simulation Engine & Dashboard
- A/B simulation: Rule-Based (Strategy A) vs AI Agent (Strategy B)
- Stochastic `RECOVERY_MATRIX` outcome model
- Vite/React dashboard with Recharts, Framer Motion, Tailwind CSS

### Phase 6 — Infrastructure
- Docker Compose (4 services + PostgreSQL)
- Kubernetes manifest (`k8s-manifest.yaml`)
- PowerShell test runner (`run_tests.ps1`)
