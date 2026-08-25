package main

// =============================================================================
//  Razorpay Webhook Integration
//  Route: POST /api/v1/webhooks/razorpay
// =============================================================================
//
//  This file implements:
//    a) HMAC-SHA256 signature verification against X-Razorpay-Signature header.
//    b) Parsing of Razorpay's real payment.failed webhook payload shape.
//    c) A real recovery ACTION: when the event is payment.failed, we call
//       Razorpay's Payment Links API (sandbox/test mode) to create a new
//       payment link for the failed amount and log the Razorpay response
//       (id, status) back to the razorpay_recovery_actions table.
// =============================================================================

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/gofiber/fiber/v2"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

// =============================================================================
//  GORM Model — razorpay_recovery_actions
// =============================================================================

// RazorpayRecoveryActionLog persists the outcome of every real Razorpay API
// call triggered by a payment.failed webhook event.
type RazorpayRecoveryActionLog struct {
	ID uuid.UUID `gorm:"type:uuid;primaryKey;default:gen_random_uuid()" json:"id"`

	// Source identifiers from the webhook
	RazorpayPaymentID string `gorm:"type:varchar(64);not null;column:razorpay_payment_id" json:"razorpay_payment_id"`
	RazorpayOrderID   string `gorm:"type:varchar(64);column:razorpay_order_id"            json:"razorpay_order_id"`
	WebhookEventID    string `gorm:"type:varchar(128);column:webhook_event_id"            json:"webhook_event_id"`

	// Razorpay Payment Link response fields
	RecoveryAction      string `gorm:"type:varchar(32);not null;column:recovery_action"       json:"recovery_action"` // "payment_link_created" | "api_error" | "disabled"
	PaymentLinkID       string `gorm:"type:varchar(64);column:payment_link_id"               json:"payment_link_id"`
	PaymentLinkStatus   string `gorm:"type:varchar(32);column:payment_link_status"           json:"payment_link_status"`
	PaymentLinkShortURL string `gorm:"type:varchar(512);column:payment_link_short_url"       json:"payment_link_short_url"`

	// Amount in paise (Razorpay's unit, 1 INR = 100 paise)
	AmountPaise int64  `gorm:"not null;column:amount_paise"      json:"amount_paise"`
	Currency    string `gorm:"type:char(3);column:currency"      json:"currency"`

	// Error capture if the Razorpay API call fails
	ErrorMessage string `gorm:"type:text;column:error_message" json:"error_message,omitempty"`

	// Raw Razorpay API response for audit
	RawAPIResponse string `gorm:"type:text;column:raw_api_response" json:"raw_api_response,omitempty"`

	CreatedAt time.Time `gorm:"column:created_at" json:"created_at"`
}

func (RazorpayRecoveryActionLog) TableName() string {
	return "razorpay_recovery_actions"
}

// =============================================================================
//  Razorpay Webhook Payload Shape
//  Mirrors: https://razorpay.com/docs/webhooks/payloads/payments/#failed-payment
// =============================================================================

// RazorpayWebhookEnvelope is the top-level shape Razorpay sends for all events.
type RazorpayWebhookEnvelope struct {
	Entity    string                   `json:"entity"`
	AccountID string                   `json:"account_id"`
	Event     string                   `json:"event"`    // e.g. "payment.failed"
	Contains  []string                 `json:"contains"` // e.g. ["payment"]
	Payload   RazorpayWebhookPayload   `json:"payload"`
}

// RazorpayWebhookPayload wraps the entity-specific payload.
type RazorpayWebhookPayload struct {
	Payment *RazorpayPaymentWrapper `json:"payment,omitempty"`
}

// RazorpayPaymentWrapper wraps the payment entity.
type RazorpayPaymentWrapper struct {
	Entity RazorpayPaymentEntity `json:"entity"`
}

// RazorpayPaymentEntity is Razorpay's real payment object shape.
// Fields match: https://razorpay.com/docs/api/payments/#payment-entity
type RazorpayPaymentEntity struct {
	ID          string `json:"id"`           // "pay_XXXXXXXXXXXXXXXX"
	Entity      string `json:"entity"`       // "payment"
	Amount      int64  `json:"amount"`       // Amount in paise
	Currency    string `json:"currency"`     // "INR"
	Status      string `json:"status"`       // "failed"
	OrderID     string `json:"order_id"`     // "order_XXXXXXXXXXXXXXXX"
	Description string `json:"description"`
	Method      string `json:"method"`       // "upi" | "card" | "netbanking" | "wallet"
	Email       string `json:"email"`
	Contact     string `json:"contact"`
	ErrorCode   string `json:"error_code"`
	ErrorDesc   string `json:"error_description"`
	ErrorSource string `json:"error_source"`
	ErrorStep   string `json:"error_step"`
	ErrorReason string `json:"error_reason"`
	CreatedAt   int64  `json:"created_at"`
}

// =============================================================================
//  Razorpay Payment Links API Request / Response
// =============================================================================

// PaymentLinkRequest is the body sent to POST https://api.razorpay.com/v1/payment_links
type PaymentLinkRequest struct {
	Amount      int64  `json:"amount"`      // paise
	Currency    string `json:"currency"`    // "INR"
	Description string `json:"description"` // human-readable reason
	CallbackURL string `json:"callback_url,omitempty"`
	CallbackMethod string `json:"callback_method,omitempty"`
}

// PaymentLinkResponse is the relevant subset of Razorpay's payment_links response.
type PaymentLinkResponse struct {
	ID       string `json:"id"`        // "plink_XXXXXXXX"
	Status   string `json:"status"`    // "created"
	ShortURL string `json:"short_url"` // "https://rzp.io/i/XXXX"
}

// =============================================================================
//  HMAC-SHA256 Signature Verification
// =============================================================================

// verifyRazorpaySignature validates the X-Razorpay-Signature header.
// Razorpay signs the raw webhook body with HMAC-SHA256 using the webhook secret.
// See: https://razorpay.com/docs/webhooks/validate-test/
func verifyRazorpaySignature(body []byte, signature, secret string) bool {
	if secret == "" || signature == "" {
		return false
	}
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(body)
	expected := hex.EncodeToString(mac.Sum(nil))
	// Use hmac.Equal for constant-time comparison (prevents timing attacks).
	return hmac.Equal([]byte(expected), []byte(signature))
}

// =============================================================================
//  Recovery Action — Razorpay Payment Links API
// =============================================================================

// triggerRazorpayRecovery calls Razorpay's Payment Links API to create a new
// payment link for the failed transaction amount, then persists the result.
// It uses the Razorpay TEST-mode API keys from the environment.
//
// If RAZORPAY_KEY_ID or RAZORPAY_KEY_SECRET are not configured, the function
// logs a warning and records the action as "disabled" so the webhook still
// returns 200 (Razorpay expects 200 to stop retries).
func triggerRazorpayRecovery(payment RazorpayPaymentEntity, webhookEventID string) {
	keyID := os.Getenv("RAZORPAY_KEY_ID")
	keySecret := os.Getenv("RAZORPAY_KEY_SECRET")

	actionLog := RazorpayRecoveryActionLog{
		RazorpayPaymentID: payment.ID,
		RazorpayOrderID:   payment.OrderID,
		WebhookEventID:    webhookEventID,
		AmountPaise:       payment.Amount,
		Currency:          "INR",
	}

	if keyID == "" || keySecret == "" {
		slog.Warn("Razorpay API keys not configured — recovery action skipped",
			"payment_id", payment.ID,
			"hint", "Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to enable live recovery",
		)
		actionLog.RecoveryAction = "disabled"
		actionLog.ErrorMessage = "RAZORPAY_KEY_ID or RAZORPAY_KEY_SECRET not set"
		persistRecoveryLog(&actionLog)
		return
	}

	slog.Info("Triggering Razorpay recovery via Payment Links API",
		"payment_id", payment.ID,
		"amount_paise", payment.Amount,
		"currency", "INR",
	)

	// Build the payment link request.
	reqBody := PaymentLinkRequest{
		Amount:   payment.Amount,
		Currency: "INR",
		Description: fmt.Sprintf(
			"Recovery link for failed payment %s (reason: %s)",
			payment.ID, payment.ErrorReason,
		),
	}
	reqBytes, err := json.Marshal(reqBody)
	if err != nil {
		slog.Error("Failed to marshal PaymentLinkRequest", "error", err.Error())
		actionLog.RecoveryAction = "api_error"
		actionLog.ErrorMessage = "json marshal error: " + err.Error()
		persistRecoveryLog(&actionLog)
		return
	}

	// POST to Razorpay Payment Links API with Basic Auth.
	const razorpayAPIBase = "https://api.razorpay.com/v1"
	httpReq, err := http.NewRequest(
		http.MethodPost,
		razorpayAPIBase+"/payment_links",
		bytes.NewReader(reqBytes),
	)
	if err != nil {
		slog.Error("Failed to create HTTP request for Razorpay", "error", err.Error())
		actionLog.RecoveryAction = "api_error"
		actionLog.ErrorMessage = err.Error()
		persistRecoveryLog(&actionLog)
		return
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.SetBasicAuth(keyID, keySecret)

	httpClient := &http.Client{Timeout: 15 * time.Second}
	resp, err := httpClient.Do(httpReq)
	if err != nil {
		slog.Error("Razorpay Payment Links API call failed", "error", err.Error())
		actionLog.RecoveryAction = "api_error"
		actionLog.ErrorMessage = err.Error()
		persistRecoveryLog(&actionLog)
		return
	}
	defer resp.Body.Close()

	respBytes, _ := io.ReadAll(resp.Body)
	actionLog.RawAPIResponse = string(respBytes)

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		slog.Error("Razorpay Payment Links API returned non-2xx",
			"status", resp.StatusCode,
			"payment_id", payment.ID,
			"response_body", string(respBytes),
		)
		actionLog.RecoveryAction = "api_error"
		actionLog.ErrorMessage = fmt.Sprintf("HTTP %d: %s", resp.StatusCode, string(respBytes))
		persistRecoveryLog(&actionLog)
		return
	}

	// Parse the successful response.
	var plResp PaymentLinkResponse
	if jsonErr := json.Unmarshal(respBytes, &plResp); jsonErr != nil {
		slog.Error("Failed to parse Razorpay Payment Links response",
			"error", jsonErr.Error(),
			"raw", string(respBytes),
		)
		actionLog.RecoveryAction = "api_error"
		actionLog.ErrorMessage = "response parse error: " + jsonErr.Error()
		persistRecoveryLog(&actionLog)
		return
	}

	// ✅ Success — log the real Razorpay response fields.
	slog.Info("Razorpay Payment Link created successfully",
		"payment_link_id", plResp.ID,
		"status", plResp.Status,
		"short_url", plResp.ShortURL,
		"original_payment_id", payment.ID,
	)

	actionLog.RecoveryAction    = "payment_link_created"
	actionLog.PaymentLinkID     = plResp.ID
	actionLog.PaymentLinkStatus = plResp.Status
	actionLog.PaymentLinkShortURL = plResp.ShortURL

	persistRecoveryLog(&actionLog)
}

// persistRecoveryLog writes the action log to the database.
// DB failures are logged but never panic — the webhook has already been processed.
func persistRecoveryLog(actionLog *RazorpayRecoveryActionLog) {
	if DB == nil {
		slog.Warn("DB not connected — recovery action log not persisted",
			"payment_id", actionLog.RazorpayPaymentID,
		)
		return
	}
	if result := DB.Create(actionLog); result.Error != nil {
		slog.Error("Failed to persist RazorpayRecoveryActionLog",
			"error", result.Error.Error(),
			"payment_id", actionLog.RazorpayPaymentID,
		)
	}
}

// =============================================================================
//  POST /api/v1/webhooks/razorpay
// =============================================================================

// HandleRazorpayWebhook is the Razorpay webhook receiver.
//
// Response codes:
//
//	200 OK        — signature verified and event processed (or non-payment event, safely ignored)
//	400 Bad Req.  — missing or invalid X-Razorpay-Signature
//	500 Server    — body read error (unlikely; Fiber buffers body)
func HandleRazorpayWebhook(c *fiber.Ctx) error {
	webhookSecret := os.Getenv("RAZORPAY_WEBHOOK_SECRET")

	// ── 1. Read raw body (needed for HMAC verification) ───────────────────────
	// Fiber buffers the body, so this is always safe.
	rawBody := c.Body()

	// ── 2. Verify X-Razorpay-Signature ────────────────────────────────────────
	signature := c.Get("X-Razorpay-Signature")
	if !verifyRazorpaySignature(rawBody, signature, webhookSecret) {
		slog.Warn("Razorpay webhook signature verification failed",
			"remote_ip", c.IP(),
			"signature_present", signature != "",
			"secret_configured", webhookSecret != "",
		)
		return c.Status(fiber.StatusBadRequest).JSON(ErrorResponse{
			Status:  "error",
			Error:   "INVALID_SIGNATURE",
			Details: "X-Razorpay-Signature header is missing or does not match the webhook secret",
		})
	}

	// ── 3. Parse Razorpay webhook envelope ────────────────────────────────────
	var envelope RazorpayWebhookEnvelope
	if err := json.Unmarshal(rawBody, &envelope); err != nil {
		slog.Error("Failed to parse Razorpay webhook body",
			"error", err.Error(),
			"remote_ip", c.IP(),
		)
		// Return 200 anyway to prevent Razorpay from retrying a bad payload.
		return c.Status(fiber.StatusOK).JSON(fiber.Map{"status": "parse_error_ignored"})
	}

	slog.Info("Razorpay webhook received",
		"event", envelope.Event,
		"account_id", envelope.AccountID,
	)

	// ── 4. Handle payment.failed events ───────────────────────────────────────
	if envelope.Event == "payment.failed" {
		if envelope.Payload.Payment == nil {
			slog.Warn("payment.failed event missing payment payload", "account_id", envelope.AccountID)
			return c.Status(fiber.StatusOK).JSON(fiber.Map{"status": "missing_payment_payload"})
		}

		payment := envelope.Payload.Payment.Entity
		slog.Info("Processing payment.failed recovery",
			"razorpay_payment_id", payment.ID,
			"amount_paise", payment.Amount,
			"method", payment.Method,
			"error_code", payment.ErrorCode,
			"error_reason", payment.ErrorReason,
		)

		// Trigger the real recovery action asynchronously so we don't block
		// the webhook acknowledgment. Razorpay expects a fast 200.
		webhookEventID := c.Get("X-Razorpay-Event-Id", "")
		go triggerRazorpayRecovery(payment, webhookEventID)
	}

	// ── 5. Acknowledge webhook ────────────────────────────────────────────────
	// Always return 200 for verified webhooks — Razorpay retries on non-2xx.
	return c.Status(fiber.StatusOK).JSON(fiber.Map{
		"status": "accepted",
		"event":  envelope.Event,
	})
}

// =============================================================================
//  AutoMigrate helper — called from main.go after ConnectDatabase()
// =============================================================================

// MigrateRazorpayTables creates the razorpay_recovery_actions table if it
// does not already exist. Safe to call on every startup (idempotent).
func MigrateRazorpayTables(db *gorm.DB) {
	if err := db.AutoMigrate(&RazorpayRecoveryActionLog{}); err != nil {
		slog.Error("Failed to auto-migrate razorpay_recovery_actions", "error", err.Error())
	} else {
		slog.Info("razorpay_recovery_actions table ready")
	}
}
