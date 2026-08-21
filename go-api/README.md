# ==============================================================================
#  AI Revenue Recovery — Go Ingestion API
#  Phase 2: Event Ingestion Microservice
# ==============================================================================

## Architecture

```
go-api/
├── main.go        — Fiber setup, middleware, graceful shutdown, slog
├── database.go    — GORM connection with exponential backoff (Neon cold-start)
├── models.go      — GORM model + Contract A request/response DTOs
├── handlers.go    — GET /health  |  POST /api/v1/events/failure
├── go.mod         — Module definition and dependencies
├── .env.example   — Environment variable template
└── README.md      — This file
```

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Go | ≥ 1.22 | https://go.dev/dl/ |
| Neon DB | — | https://neon.tech (free tier) |

> **Note**: Run `schema.sql` from Phase 1 against your Neon DB **before** starting this service.
> The ENUM types (`payment_method_enum`, `failure_reason_enum`, etc.) and the `failed_payments` table must exist.

## Setup

```bash
# 1. Navigate to the go-api directory
cd go-api

# 2. Copy the environment template
cp .env.example .env

# 3. Edit .env — paste your Neon connection string
#    DATABASE_URL=postgres://user:pass@ep-xyz.us-east-2.aws.neon.tech/neondb?sslmode=require

# 4. Download all dependencies
go mod tidy

# 5. Run the server
go run .
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | ✅ | — | Full Neon PostgreSQL connection string |
| `PORT` | ❌ | `8080` | HTTP port the server binds to |
| `APP_ENV` | ❌ | `development` | `development` / `staging` / `production` |
| `ALLOWED_ORIGINS` | ❌ | `*` | CORS allowed origins (comma-separated). Restrict in production. |

## API Reference

### `GET /health`

Returns real-time API and database status.

```bash
curl http://localhost:8080/health
```

**200 OK (healthy):**
```json
{ "status": "ok", "db_connected": true, "version": "1.0.0", "environment": "development" }
```

**503 Service Unavailable (DB unreachable):**
```json
{ "status": "degraded", "db_connected": false, "version": "1.0.0", "environment": "development" }
```

---

### `POST /api/v1/events/failure`

Ingests a failed payment event (Contract A). Writes to `failed_payments` table.

```bash
curl -X POST http://localhost:8080/api/v1/events/failure \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: test-corr-001" \
  -d '{
    "event_type": "payment.failed",
    "event_id": "evt_test_001",
    "event_timestamp": "2024-08-15T10:45:00.123Z",
    "api_version": "2024-08-01",
    "payment": {
      "transaction_id": "550e8400-e29b-41d4-a716-446655440001",
      "customer_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      "merchant_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "amount": 4999.00,
      "currency": "INR",
      "payment_method": "UPI",
      "failure_reason_raw": "gateway_timeout",
      "gateway_name": "razorpay",
      "gateway_error_code": "GATEWAY_ERROR",
      "gateway_order_id": "order_NQUBLtNKZMIk2X",
      "gateway_payment_id": "pay_NQUBLtNKZMIk2X",
      "idempotency_key": "idem_550e8400-e29b-41d4-a716"
    },
    "customer_context": {
      "customer_ltv": 12450.75,
      "recent_retries": 1,
      "time_since_last_attempt_mins": 8
    },
    "metadata": {
      "source_system": "razorpay-webhook",
      "environment": "production",
      "correlation_id": "corr_abc123"
    }
  }'
```

**201 Created:**
```json
{
  "status": "accepted",
  "message": "Event ingested successfully. Recovery agent queued.",
  "failed_payment_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "transaction_id": "550e8400-e29b-41d4-a716-446655440001",
  "timestamp": "2024-08-15T10:45:00.456Z"
}
```

**409 Conflict (duplicate `transaction_id`):**
```json
{ "status": "error", "error": "DUPLICATE_TRANSACTION", "details": "..." }
```

**422 Unprocessable Entity (validation failure):**
```json
{ "status": "error", "error": "VALIDATION_FAILED", "details": "payment.amount must be greater than 0" }
```

## Exponential Backoff — Neon Cold-Start

Neon's serverless compute pauses after a period of inactivity. The first
connection attempt after a cold-start may time out. `database.go` handles this
automatically:

| Attempt | Wait before retry |
|---|---|
| 1 | Immediate |
| 2 | 500 ms |
| 3 | 1,000 ms |
| 4 | 2,000 ms |
| 5 | 4,000 ms → fatal exit |

All retry attempts are logged at `WARN` level with `slog` in JSON format.

## Graceful Shutdown

The server listens for `SIGINT` (Ctrl-C) and `SIGTERM` (container stop).
On either signal:

1. **`app.ShutdownWithTimeout(10s)`** — in-flight requests complete (up to 10 seconds)
2. **`CloseDatabase()`** — connection pool is drained and closed

This ensures zero connection leaks on Neon and no dropped requests during deploys.

## Production Build

```bash
# Optimised binary (removes debug symbols, reduces size)
go build -ldflags="-s -w" -o bin/go-ingestion-api .

# Run the binary
./bin/go-ingestion-api
```

## Key Design Decisions

| Decision | Rationale |
|---|---|
| `gofiber/fiber/v2` | Fasthttp-based, ~10× faster than net/http for high-throughput webhooks |
| `gorm` + postgres driver | Expressive ORM with full ENUM and JSONB support |
| `datatypes.JSON` for JSONB | Type-safe marshal/unmarshal; GORM's built-in JSONB adapter |
| Idempotent 409 on duplicate TX | Gateway webhooks retry on failure; 409 prevents double-writes |
| UUID pointer for `merchant_id` | Nullable column — missing merchant_id writes SQL NULL, not UUID zero-value |
| `ptrStr()` helper | Converts empty strings to nil for nullable VARCHAR columns |
| `slog` (stdlib) | No extra dependency; structured JSON logs from day one |
