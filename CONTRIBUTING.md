# Contributing to AI Revenue Recovery Engine

Thank you for your interest in contributing! This guide covers how to set up the project, run tests, and submit changes.

---

## Prerequisites

- [Go](https://go.dev/) v1.22+
- [Python](https://www.python.org/) v3.10+ with `pip`
- [Node.js](https://nodejs.org/) v18+ with `npm`
- [GNU Make](https://www.gnu.org/software/make/) (optional but recommended)

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/NitheeshP19/AI-Revenue-recovery-Engine.git
cd AI-Revenue-recovery-Engine

# 2. Set up environment
cp .env.example .env
# Edit .env — fill in DATABASE_URL, GROQ_API_KEY, and Razorpay test keys

# 3. Train the ML model (required — classifier.json is not in git)
make train

# 4. Run the full test suite
make test
```

---

## Project Structure

```
├── go-api/              # Go Fiber ingestion API + Razorpay webhook
│   ├── main.go          # Entry point, route registration
│   ├── handlers.go      # /api/v1/events/failure handler
│   ├── razorpay_webhook.go  # POST /api/v1/webhooks/razorpay
│   ├── models.go        # GORM models + DTOs
│   └── handlers_test.go # Unit tests
├── ml/
│   ├── inference_service.py  # FastAPI XGBoost classifier
│   ├── agent_service.py      # FastAPI Groq LLM agent
│   ├── train_model.py        # Trains classifier.json (run: make train)
│   └── test_services.py      # pytest integration tests
├── dashboard/           # Vite + React + Tailwind dashboard
├── simulation_engine.py # A/B simulation benchmark runner
├── Makefile             # Cross-platform task runner
├── scripts/run_tests.sh # Bash test runner (Linux/macOS/WSL)
├── run_tests.ps1        # PowerShell test runner (Windows)
├── docker-compose.yml   # All services
└── k8s-manifest.yaml    # Kubernetes deployment
```

---

## Running Tests

```bash
# All suites
make test

# Individual
make test-go         # Go unit tests
make test-python     # pytest
make test-dashboard  # oxlint + vitest

# Non-make alternatives
cd go-api && go test ./... -v
cd ml && python -m pytest test_services.py -v
bash scripts/run_tests.sh   # Linux/macOS/WSL
powershell -ExecutionPolicy Bypass -File run_tests.ps1  # Windows
```

---

## Razorpay Webhook Testing

To test the webhook endpoint locally, use the [Razorpay Webhook Simulator](https://dashboard.razorpay.com/app/webhooks):

```bash
# Generate a valid HMAC-SHA256 signature manually:
echo -n '<raw_payload_bytes>' | openssl dgst -sha256 -hmac '<your_webhook_secret>'
```

Or use the `TestRazorpayWebhook_*` tests in `go-api/handlers_test.go` which handle this automatically.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | — | Neon/PostgreSQL connection string |
| `GROQ_API_KEY` | Yes | — | Groq API key |
| `RAZORPAY_KEY_ID` | Yes* | — | Razorpay test-mode key ID (`rzp_test_...`) |
| `RAZORPAY_KEY_SECRET` | Yes* | — | Razorpay test-mode secret |
| `RAZORPAY_WEBHOOK_SECRET` | Yes* | — | Razorpay webhook verification secret |
| `ALLOWED_ORIGINS` | No | `http://localhost:5173` | CORS allowed origins |
| `ALLOW_INSECURE_CORS` | No | `false` | Set to `true` to allow `*` CORS (dev only) |
| `PORT` | No | `8080` | Go API port |
| `APP_ENV` | No | `development` | Environment (`development`/`production`) |

*Required for Razorpay recovery actions; webhook verification works without `KEY_ID`/`KEY_SECRET` but will log `recovery_action=disabled`.

---

## Code Style

- **Go**: `gofmt` + `go vet` (run `make lint`)
- **Python**: PEP 8, type hints encouraged
- **JavaScript**: oxlint (run `npm run lint`)

---

## Submitting Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make changes and run `make test` to verify
4. Commit with a descriptive message following [Conventional Commits](https://www.conventionalcommits.org/)
5. Open a Pull Request against `main`
