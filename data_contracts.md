# AI Revenue Recovery System — JSON Data Contracts

**Version:** `1.0.0`  
**Status:** 🟢 Production-Ready  
**Alignment:** Go API ↔ Python Agent ↔ React Dashboard

> These contracts are the **single source of truth** for all inter-service communication.
> Every field is annotated with its type, validation rule, and whether it is required (`✅`) or optional (`❌`).

---

## Contract A — Event Ingestion Payload

**Direction:** Upstream payment system → Go API (`POST /v1/payments/failed`)  
**Description:** Sent by the payment gateway webhook or the merchant backend immediately after a payment failure is detected.

### Request Body

```json
{
  "event_type": "payment.failed",
  "event_id": "evt_01J5XK3M2P9R7VQNB4T8WFHGE",
  "event_timestamp": "2024-08-15T10:45:00.123Z",
  "api_version": "2024-08-01",

  "payment": {
    "transaction_id": "550e8400-e29b-41d4-a716-446655440000",
    "customer_id":    "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "merchant_id":    "3fa85f64-5717-4562-b3fc-2c963f66afa6",
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
    "time_since_last_attempt_mins": 8,
    "preferred_payment_methods": ["UPI", "Credit Card"],
    "account_age_days": 420,
    "is_vip": false
  },

  "metadata": {
    "source_system": "razorpay-webhook",
    "environment": "production",
    "correlation_id": "corr_abc123def456"
  }
}
```

### Field Reference — Contract A

| Field Path | Type | Required | Validation / Notes |
|---|---|---|---|
| `event_type` | `string` | ✅ | Must be `"payment.failed"` |
| `event_id` | `string` | ✅ | Unique event ID (idempotency key for the event) |
| `event_timestamp` | `string` | ✅ | ISO 8601 with UTC offset (TIMESTAMPTZ) |
| `api_version` | `string` | ✅ | Semver-like, for contract versioning |
| `payment.transaction_id` | `string (UUID)` | ✅ | Must be globally unique |
| `payment.customer_id` | `string (UUID)` | ✅ | |
| `payment.merchant_id` | `string (UUID)` | ✅ | For multi-tenant routing |
| `payment.amount` | `number (float)` | ✅ | Must be > 0, 2 decimal places max |
| `payment.currency` | `string` | ✅ | ISO 4217 (INR, USD, etc.) |
| `payment.payment_method` | `string (enum)` | ✅ | One of: UPI, Credit Card, Debit Card, Net Banking, Wallet, BNPL |
| `payment.failure_reason_raw` | `string (enum)` | ✅ | One of: insufficient_funds, gateway_timeout, incorrect_pin, expired_card, risk_flag, do_not_honor, unknown |
| `payment.gateway_name` | `string` | ✅ | e.g. razorpay, stripe, payu |
| `payment.gateway_error_code` | `string` | ❌ | Raw code from gateway |
| `payment.gateway_order_id` | `string` | ❌ | Gateway-side order reference |
| `payment.gateway_payment_id` | `string` | ❌ | Gateway-side payment reference |
| `payment.idempotency_key` | `string` | ✅ | Prevents duplicate processing |
| `customer_context.customer_ltv` | `number (float)` | ✅ | Must be >= 0 |
| `customer_context.recent_retries` | `integer` | ✅ | Range: 0–10 |
| `customer_context.time_since_last_attempt_mins` | `integer` | ✅ | Must be >= 0 |
| `customer_context.preferred_payment_methods` | `string[]` | ❌ | For switch_method recommendations |
| `customer_context.account_age_days` | `integer` | ❌ | |
| `customer_context.is_vip` | `boolean` | ❌ | |
| `metadata.source_system` | `string` | ✅ | |
| `metadata.environment` | `string` | ✅ | production, staging, sandbox |
| `metadata.correlation_id` | `string` | ❌ | For distributed tracing |

### Success Response — `202 Accepted`

```json
{
  "status": "accepted",
  "failed_payment_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "transaction_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Event ingested successfully. Recovery agent queued.",
  "queued_job_id": "job_9f8e7d6c-5b4a-3210-fedc-ba9876543210",
  "timestamp": "2024-08-15T10:45:00.456Z"
}
```

---

## Contract B — Agent Request Payload

**Direction:** Go backend / task queue → Python Agent Service (`POST /agent/v1/decide`)  
**Description:** Sent by the Go backend (or a message queue consumer like BullMQ/Celery) to trigger the Python AI agent for a recovery decision. Contains full enriched context.

### Request Body

```json
{
  "job_id": "job_9f8e7d6c-5b4a-3210-fedc-ba9876543210",
  "failed_payment_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "request_timestamp": "2024-08-15T10:45:01.000Z",
  "agent_config": {
    "model_id": "gpt-4o-2024-08-06",
    "decision_version": 3,
    "confidence_threshold": 0.72,
    "max_reasoning_tokens": 1024,
    "enable_chain_of_thought": true
  },

  "payment_context": {
    "transaction_id": "550e8400-e29b-41d4-a716-446655440000",
    "customer_id":    "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "merchant_id":    "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "amount": 4999.00,
    "currency": "INR",
    "payment_method": "UPI",
    "failure_reason_raw": "gateway_timeout",
    "gateway_name": "razorpay",
    "gateway_error_code": "GATEWAY_ERROR"
  },

  "customer_profile": {
    "customer_ltv": 12450.75,
    "recent_retries": 1,
    "time_since_last_attempt_mins": 8,
    "preferred_payment_methods": ["UPI", "Credit Card"],
    "account_age_days": 420,
    "is_vip": false
  },

  "historical_context": {
    "total_failed_payments_30d": 3,
    "last_successful_payment_method": "UPI",
    "last_successful_payment_at": "2024-07-30T09:15:00Z",
    "average_transaction_value": 3250.50
  },

  "system_context": {
    "gateway_health_status": "degraded",
    "current_gateway_error_rate_pct": 12.4,
    "is_peak_hour": true,
    "estimated_gateway_recovery_mins": 15
  }
}
```

### Field Reference — Contract B

| Field Path | Type | Required | Validation / Notes |
|---|---|---|---|
| `job_id` | `string (UUID)` | ✅ | Traces back to the task queue job |
| `failed_payment_id` | `string (UUID)` | ✅ | FK to failed_payments.id |
| `request_timestamp` | `string` | ✅ | ISO 8601 UTC |
| `agent_config.model_id` | `string` | ✅ | LLM model identifier |
| `agent_config.decision_version` | `integer` | ✅ | Model/prompt version, stored in recovery_decisions |
| `agent_config.confidence_threshold` | `number` | ✅ | Agent escalates to human if below this |
| `agent_config.enable_chain_of_thought` | `boolean` | ❌ | Default: true |
| `payment_context.*` | `object` | ✅ | Mirrors the core payment fields from Contract A |
| `customer_profile.*` | `object` | ✅ | Customer LTV and behavioral signals |
| `historical_context.*` | `object` | ❌ | Enriched by Go backend from DB lookups |
| `system_context.*` | `object` | ❌ | Real-time gateway health signals |
| `system_context.gateway_health_status` | `string (enum)` | ❌ | healthy, degraded, down |

---

## Contract C — Agent Response Payload

**Direction:** Python Agent Service → Go backend  
**Description:** The structured decision returned by the Python AI agent. Must include `decision`, `confidence_score`, and full `reasoning_trace`. The Go backend persists this directly to `recovery_decisions.agent_reasoning_trace` (JSONB).

### Response Body — `200 OK`

```json
{
  "job_id": "job_9f8e7d6c-5b4a-3210-fedc-ba9876543210",
  "failed_payment_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "transaction_id": "550e8400-e29b-41d4-a716-446655440000",
  "decision_version": 3,
  "decided_at": "2024-08-15T10:45:02.891Z",

  "decision": "retry_later",
  "confidence_score": 0.8740,

  "action_parameters": {
    "recommended_retry_delay_mins": 20,
    "recommended_channel": null,
    "customer_communication_hint": "Your payment via UPI encountered a temporary gateway issue. We will auto-retry in 20 minutes — no action needed from you.",
    "send_notification": true,
    "notification_channel": "push"
  },

  "reasoning_trace": {
    "summary": "Gateway timeout on UPI is a transient, non-user-fault error. Current gateway health is degraded. Retry after estimated recovery window.",
    "decision_path": "gateway_timeout → system_context.degraded → retry_later",

    "feature_vector": {
      "payment_method": "UPI",
      "failure_reason_raw": "gateway_timeout",
      "recent_retries": 1,
      "customer_ltv": 12450.75,
      "is_vip": false,
      "gateway_health": "degraded",
      "is_peak_hour": true,
      "time_since_last_attempt_mins": 8
    },

    "triggered_rules": [
      "RULE_001: gateway_timeout is transient → prefer retry over give_up",
      "RULE_007: gateway_health=degraded → prefer retry_later over retry_now",
      "RULE_012: recent_retries <= 2 → retry strategies still viable",
      "RULE_019: high_customer_ltv (12450.75) → preserve relationship, avoid give_up"
    ],

    "considered_actions": [
      { "action": "retry_later",   "score": 0.8740, "selected": true  },
      { "action": "retry_now",     "score": 0.0820, "selected": false },
      { "action": "switch_method", "score": 0.0310, "selected": false },
      { "action": "give_up",       "score": 0.0130, "selected": false }
    ],

    "chain_of_thought": "1. The failure reason is gateway_timeout, a transient network issue, not a customer-side fault.\n2. System context confirms gateway health is degraded with a 12.4% error rate, corroborating the timeout.\n3. The customer has only retried once (recent_retries=1), meaning retry budget is not exhausted.\n4. Customer LTV is high (12450.75 INR), making relationship preservation a high-priority signal.\n5. Retry delay is set to 20 minutes, aligning with the estimated gateway recovery window of 15 minutes plus safety buffer.\n6. retry_now is excluded because the gateway is still in a degraded state.\n7. Final decision: retry_later with 87.4% confidence.",

    "llm_model_used": "gpt-4o-2024-08-06",
    "prompt_tokens": 687,
    "completion_tokens": 341,
    "agent_latency_ms": 1891,
    "schema_version": "1.0.0"
  }
}
```

### Field Reference — Contract C

| Field Path | Type | Required | Validation / Notes |
|---|---|---|---|
| `job_id` | `string (UUID)` | ✅ | Echo of request job_id |
| `failed_payment_id` | `string (UUID)` | ✅ | FK for DB write to recovery_decisions |
| `transaction_id` | `string (UUID)` | ✅ | |
| `decision_version` | `integer` | ✅ | Must match agent_config.decision_version from request |
| `decided_at` | `string` | ✅ | ISO 8601 UTC timestamp |
| `decision` | `string (enum)` | ✅ | One of: retry_now, retry_later, switch_method, give_up, escalate_to_agent |
| `confidence_score` | `number (float)` | ✅ | Range: [0.0, 1.0], 4 decimal places |
| `action_parameters.recommended_retry_delay_mins` | `integer or null` | ❌ | Present when decision = retry_later |
| `action_parameters.recommended_channel` | `string or null` | ❌ | Present when decision = switch_method |
| `action_parameters.customer_communication_hint` | `string` | ✅ | User-facing message template |
| `action_parameters.send_notification` | `boolean` | ✅ | |
| `action_parameters.notification_channel` | `string (enum)` | ❌ | push, sms, email, whatsapp |
| `reasoning_trace.summary` | `string` | ✅ | One-line human-readable summary |
| `reasoning_trace.decision_path` | `string` | ✅ | Breadcrumb trace of the decision |
| `reasoning_trace.feature_vector` | `object` | ✅ | Key-value map of input features used |
| `reasoning_trace.triggered_rules` | `string[]` | ✅ | List of business/ML rules that fired |
| `reasoning_trace.considered_actions` | `object[]` | ✅ | All actions with scores and selection flag |
| `reasoning_trace.chain_of_thought` | `string` | ✅ | Full LLM CoT for explainability and audit |
| `reasoning_trace.llm_model_used` | `string` | ✅ | |
| `reasoning_trace.prompt_tokens` | `integer` | ✅ | For cost tracking |
| `reasoning_trace.completion_tokens` | `integer` | ✅ | For cost tracking |
| `reasoning_trace.agent_latency_ms` | `integer` | ✅ | End-to-end latency |
| `reasoning_trace.schema_version` | `string` | ✅ | Enables forward-compat parsing |

### Error Response — `4xx / 5xx`

```json
{
  "job_id": "job_9f8e7d6c-5b4a-3210-fedc-ba9876543210",
  "error": {
    "code": "AGENT_INFERENCE_FAILED",
    "message": "LLM provider returned a 503. Decision could not be made.",
    "retryable": true,
    "retry_after_seconds": 30
  },
  "decided_at": "2024-08-15T10:45:03.001Z"
}
```

---

## Contract Alignment Matrix

| Concern | Contract A | Contract B | Contract C |
|---|---|---|---|
| `transaction_id` | Provided by upstream | Echoed from A | Echoed back |
| `customer_ltv` | In `customer_context` | In `customer_profile` | In `reasoning_trace.feature_vector` |
| `failure_reason_raw` | Enum, validated at ingestion | Forwarded verbatim | Used in rule triggering |
| `confidence_score` | — | `confidence_threshold` (config) | Returned by agent |
| `reasoning_trace` | — | — | JSONB → stored in `recovery_decisions` |
| `idempotency` | `idempotency_key` | `job_id` | `job_id` echoed |

---

## Enum Reference

```json
{
  "payment_method":        ["UPI", "Credit Card", "Debit Card", "Net Banking", "Wallet", "BNPL"],
  "failure_reason_raw":    ["insufficient_funds", "gateway_timeout", "incorrect_pin", "expired_card", "risk_flag", "do_not_honor", "unknown"],
  "decision":              ["retry_now", "retry_later", "switch_method", "give_up", "escalate_to_agent"],
  "gateway_health_status": ["healthy", "degraded", "down"],
  "notification_channel":  ["push", "sms", "email", "whatsapp"],
  "environment":           ["production", "staging", "sandbox"],
  "outcome":               ["pending", "success", "failed", "expired", "skipped"]
}
```

---

> **Versioning Policy:** All breaking changes to any contract MUST increment `api_version` in Contract A and `schema_version` in Contract C.
> Both Go and Python services MUST validate the version field before processing and return `HTTP 400` with code `UNSUPPORTED_CONTRACT_VERSION` if mismatched.
