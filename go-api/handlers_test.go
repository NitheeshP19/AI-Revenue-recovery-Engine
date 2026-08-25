package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/gofiber/fiber/v2"
)

// newTestApp builds a minimal Fiber app with the same routes as main.go
// but without starting a real HTTP server or requiring a database.
func newTestApp() *fiber.App {
	app := fiber.New(fiber.Config{DisableStartupMessage: true})
	app.Get("/health", HealthHandler)
	app.Post("/api/v1/events/failure", IngestFailureEventHandler)
	app.Post("/api/v1/webhooks/razorpay", HandleRazorpayWebhook)
	return app
}

// validPayloadBytes returns a valid Contract A JSON body as bytes.
func validPayloadBytes() []byte {
	p := map[string]interface{}{
		"event_type":      "payment.failed",
		"event_id":        "evt-unit-001",
		"event_timestamp": "2026-08-20T10:00:00Z",
		"api_version":     "1.0.0",
		"payment": map[string]interface{}{
			"transaction_id":     "550e8400-e29b-41d4-a716-446655440000",
			"customer_id":        "550e8400-e29b-41d4-a716-446655440001",
			"amount":             1500.00,
			"currency":           "INR",
			"payment_method":     "UPI",
			"failure_reason_raw": "gateway_timeout",
			"idempotency_key":    "idem-001",
		},
		"customer_context": map[string]interface{}{
			"customer_ltv":                 5000.0,
			"recent_retries":               1,
			"time_since_last_attempt_mins": 15,
		},
		"metadata": map[string]interface{}{"source_system": "razorpay"},
	}
	b, _ := json.Marshal(p)
	return b
}

// ── GET /health ───────────────────────────────────────────────────────────────

// TestHealthHandler_NoDB verifies 503 + degraded status when DB is nil.
func TestHealthHandler_NoDB(t *testing.T) {
	DB = nil
	app := newTestApp()

	req := httptest.NewRequest("GET", "/health", nil)
	resp, err := app.Test(req, -1)
	if err != nil {
		t.Fatalf("app.Test: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != fiber.StatusServiceUnavailable {
		t.Errorf("want 503, got %d", resp.StatusCode)
	}
	var body HealthResponse
	json.NewDecoder(resp.Body).Decode(&body)
	if body.Status != "degraded" {
		t.Errorf("want status='degraded', got %q", body.Status)
	}
	if body.DBConnected {
		t.Error("want db_connected=false when DB is nil")
	}
}

// ── POST /api/v1/events/failure — parse errors ────────────────────────────────

// TestIngestHandler_InvalidJSON expects HTTP 400 for malformed JSON.
func TestIngestHandler_InvalidJSON(t *testing.T) {
	DB = nil
	app := newTestApp()

	req := httptest.NewRequest("POST", "/api/v1/events/failure",
		bytes.NewBufferString(`not-valid-json`))
	req.Header.Set("Content-Type", "application/json")
	resp, err := app.Test(req, -1)
	if err != nil {
		t.Fatalf("app.Test: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != fiber.StatusBadRequest {
		t.Errorf("want 400, got %d", resp.StatusCode)
	}
	var body ErrorResponse
	json.NewDecoder(resp.Body).Decode(&body)
	if body.Error != "INVALID_JSON" {
		t.Errorf("want error='INVALID_JSON', got %q", body.Error)
	}
}

// ── POST /api/v1/events/failure — semantic validation (422) ───────────────────

// TestIngestHandler_ValidationErrors is table-driven, mutating one field per
// case to trigger the corresponding 422 validation failure.
func TestIngestHandler_ValidationErrors(t *testing.T) {
	type mutFn func(map[string]interface{})
	cases := []struct {
		name   string
		mutate mutFn
	}{
		{"wrong_event_type", func(p map[string]interface{}) { p["event_type"] = "payment.success" }},
		{"empty_event_id", func(p map[string]interface{}) { p["event_id"] = "" }},
		{"zero_amount", func(p map[string]interface{}) { p["payment"].(map[string]interface{})["amount"] = 0 }},
		{"bad_payment_method", func(p map[string]interface{}) { p["payment"].(map[string]interface{})["payment_method"] = "Crypto" }},
		{"bad_failure_reason", func(p map[string]interface{}) { p["payment"].(map[string]interface{})["failure_reason_raw"] = "hacked" }},
		{"retries_out_of_range", func(p map[string]interface{}) { p["customer_context"].(map[string]interface{})["recent_retries"] = 15 }},
		{"negative_ltv", func(p map[string]interface{}) { p["customer_context"].(map[string]interface{})["customer_ltv"] = -1.0 }},
	}

	DB = nil
	app := newTestApp()

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			var payload map[string]interface{}
			json.Unmarshal(validPayloadBytes(), &payload)
			tc.mutate(payload)
			body, _ := json.Marshal(payload)

			req := httptest.NewRequest("POST", "/api/v1/events/failure", bytes.NewBuffer(body))
			req.Header.Set("Content-Type", "application/json")
			resp, err := app.Test(req, -1)
			if err != nil {
				t.Fatalf("app.Test: %v", err)
			}
			defer resp.Body.Close()

			if resp.StatusCode != fiber.StatusUnprocessableEntity {
				t.Errorf("[%s] want 422, got %d", tc.name, resp.StatusCode)
			}
			var errBody ErrorResponse
			json.NewDecoder(resp.Body).Decode(&errBody)
			if errBody.Error != "VALIDATION_FAILED" {
				t.Errorf("[%s] want error='VALIDATION_FAILED', got %q", tc.name, errBody.Error)
			}
		})
	}
}

// ── validateIngestionRequest unit tests ───────────────────────────────────────

func TestValidateIngestionRequest(t *testing.T) {
	good := &EventIngestionRequest{
		EventType: "payment.failed",
		EventID:   "evt-001",
		Payment: PaymentPayload{
			TransactionID:    "550e8400-e29b-41d4-a716-446655440000",
			CustomerID:       "550e8400-e29b-41d4-a716-446655440001",
			Amount:           500.0,
			PaymentMethod:    "UPI",
			FailureReasonRaw: "insufficient_funds",
			IdempotencyKey:   "idem-001",
		},
		CustomerContext: CustomerContext{
			CustomerLTV:              1000.0,
			RecentRetries:            2,
			TimeSinceLastAttemptMins: 10,
		},
	}
	if msg := validateIngestionRequest(good); msg != "" {
		t.Errorf("valid request should pass, got: %s", msg)
	}

	bad := *good
	bad.Payment.Amount = -5
	if msg := validateIngestionRequest(&bad); msg == "" {
		t.Error("negative amount should fail validation")
	}
}

// ── ptrStr helper ─────────────────────────────────────────────────────────────

func TestPtrStr(t *testing.T) {
	if ptrStr("") != nil {
		t.Error(`ptrStr("") should be nil`)
	}
	if ptrStr("  ") != nil {
		t.Error(`ptrStr("  ") should be nil`)
	}
	got := ptrStr("hello")
	if got == nil || *got != "hello" {
		t.Error(`ptrStr("hello") should return pointer to "hello"`)
	}
}

// =============================================================================
//  POST /api/v1/webhooks/razorpay — Signature verification integration tests
// =============================================================================

// razorpayTestPayload returns a minimal but valid Razorpay payment.failed body.
func razorpayTestPayload() []byte {
	p := map[string]interface{}{
		"entity":     "event",
		"account_id": "acc_test123",
		"event":      "payment.failed",
		"contains":   []string{"payment"},
		"payload": map[string]interface{}{
			"payment": map[string]interface{}{
				"entity": map[string]interface{}{
					"id":              "pay_TestPaymentID001",
					"entity":          "payment",
					"amount":          150000,
					"currency":        "INR",
					"status":          "failed",
					"order_id":        "order_TestOrderID001",
					"method":          "upi",
					"email":           "test@example.com",
					"error_code":      "BAD_REQUEST_ERROR",
					"error_reason":    "payment_failed",
					"error_source":    "customer",
					"created_at":      1724600000,
				},
			},
		},
	}
	b, _ := json.Marshal(p)
	return b
}

// signPayload creates an HMAC-SHA256 signature matching Razorpay's algorithm.
func signPayload(body []byte, secret string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(body)
	return hex.EncodeToString(mac.Sum(nil))
}

// TestRazorpayWebhook_ValidSignature verifies that a correctly signed payload
// is accepted with HTTP 200 and status "accepted" or "missing_payment_payload".
func TestRazorpayWebhook_ValidSignature(t *testing.T) {
	const testSecret = "test-webhook-secret-abc123"
	os.Setenv("RAZORPAY_WEBHOOK_SECRET", testSecret)
	defer os.Unsetenv("RAZORPAY_WEBHOOK_SECRET")

	DB = nil // DB not required for signature verification test
	app := newTestApp()

	body := razorpayTestPayload()
	sig := signPayload(body, testSecret)

	req := httptest.NewRequest("POST", "/api/v1/webhooks/razorpay", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Razorpay-Signature", sig)

	resp, err := app.Test(req, -1)
	if err != nil {
		t.Fatalf("app.Test: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != fiber.StatusOK {
		t.Errorf("valid signature: want 200, got %d", resp.StatusCode)
	}

	var body2 map[string]interface{}
	json.NewDecoder(resp.Body).Decode(&body2)
	status, _ := body2["status"].(string)
	if status != "accepted" && status != "missing_payment_payload" {
		t.Errorf("want status='accepted', got %q", status)
	}
}

// TestRazorpayWebhook_InvalidSignature verifies that a tampered/wrong signature
// returns HTTP 400 with error code INVALID_SIGNATURE.
func TestRazorpayWebhook_InvalidSignature(t *testing.T) {
	const testSecret = "test-webhook-secret-abc123"
	os.Setenv("RAZORPAY_WEBHOOK_SECRET", testSecret)
	defer os.Unsetenv("RAZORPAY_WEBHOOK_SECRET")

	DB = nil
	app := newTestApp()

	body := razorpayTestPayload()
	// Use a deliberately wrong signature.
	wrongSig := signPayload(body, "wrong-secret-that-wont-match")

	req := httptest.NewRequest("POST", "/api/v1/webhooks/razorpay", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Razorpay-Signature", wrongSig)

	resp, err := app.Test(req, -1)
	if err != nil {
		t.Fatalf("app.Test: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != fiber.StatusBadRequest {
		t.Errorf("invalid signature: want 400, got %d", resp.StatusCode)
	}

	var errBody ErrorResponse
	json.NewDecoder(resp.Body).Decode(&errBody)
	if errBody.Error != "INVALID_SIGNATURE" {
		t.Errorf("want error='INVALID_SIGNATURE', got %q", errBody.Error)
	}
}

// TestRazorpayWebhook_MissingSignature verifies that a request without any
// X-Razorpay-Signature header returns HTTP 400.
func TestRazorpayWebhook_MissingSignature(t *testing.T) {
	const testSecret = "test-webhook-secret-abc123"
	os.Setenv("RAZORPAY_WEBHOOK_SECRET", testSecret)
	defer os.Unsetenv("RAZORPAY_WEBHOOK_SECRET")

	DB = nil
	app := newTestApp()

	body := razorpayTestPayload()

	// Send with no X-Razorpay-Signature header at all.
	req := httptest.NewRequest("POST", "/api/v1/webhooks/razorpay", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")

	resp, err := app.Test(req, -1)
	if err != nil {
		t.Fatalf("app.Test: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != fiber.StatusBadRequest {
		t.Errorf("missing signature: want 400, got %d", resp.StatusCode)
	}

	var errBody ErrorResponse
	json.NewDecoder(resp.Body).Decode(&errBody)
	if errBody.Error != "INVALID_SIGNATURE" {
		t.Errorf("want error='INVALID_SIGNATURE', got %q", errBody.Error)
	}
}
