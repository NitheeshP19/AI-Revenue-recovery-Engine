package main

import (
	"encoding/json"
	"log/slog"
	"os"
	"strings"
	"time"

	"github.com/gofiber/fiber/v2"
	"github.com/google/uuid"
	"gorm.io/datatypes"
)

// =============================================================================
//  Valid enum sets (mirrors Phase 1 ENUM types defined in schema.sql)
// =============================================================================

var validPaymentMethods = map[string]bool{
	"UPI": true, "Credit Card": true, "Debit Card": true,
	"Net Banking": true, "Wallet": true, "BNPL": true,
}

var validFailureReasons = map[string]bool{
	"insufficient_funds": true, "gateway_timeout": true, "incorrect_pin": true,
	"expired_card": true, "risk_flag": true, "do_not_honor": true, "unknown": true,
}

// =============================================================================
//  GET /health
// =============================================================================

// HealthHandler returns the real-time API and database connectivity status.
// A 200 response means both the API and DB are healthy.
// A 503 response means the API is up but the database is unreachable.
func HealthHandler(c *fiber.Ctx) error {
	dbConnected := GetDBStatus()

	status := "ok"
	httpStatus := fiber.StatusOK
	if !dbConnected {
		status = "degraded"
		httpStatus = fiber.StatusServiceUnavailable
	}

	return c.Status(httpStatus).JSON(HealthResponse{
		Status:      status,
		DBConnected: dbConnected,
		Version:     "1.0.0",
		Environment: os.Getenv("APP_ENV"),
	})
}

// =============================================================================
//  POST /api/v1/events/failure
// =============================================================================

// IngestFailureEventHandler is the primary ingestion endpoint.
// It parses a Contract A payload, validates it, maps it to the GORM model,
// and writes it to the failed_payments table.
//
// Response codes:
//
//	201 Created   — event persisted, recovery agent queued
//	400 Bad Req.  — malformed JSON or invalid UUID
//	409 Conflict  — duplicate transaction_id (idempotent response)
//	422 Unproc.   — semantic validation failure (enum, range, missing field)
//	500 Server    — database write failed
func IngestFailureEventHandler(c *fiber.Ctx) error {
	// ── Derive a correlation ID for end-to-end tracing ─────────────────────────
	correlationID := c.Get("X-Correlation-ID", "")
	if correlationID == "" {
		correlationID = uuid.New().String()
	}

	log := slog.With(
		"handler", "IngestFailureEvent",
		"correlation_id", correlationID,
		"remote_ip", c.IP(),
		"method", c.Method(),
		"path", c.Path(),
	)

	// ── 1. Parse Contract A JSON body ──────────────────────────────────────────
	var req EventIngestionRequest
	if err := c.BodyParser(&req); err != nil {
		log.Error("Failed to parse request body", "error", err.Error())
		return c.Status(fiber.StatusBadRequest).JSON(ErrorResponse{
			Status:  "error",
			Error:   "INVALID_JSON",
			Details: "Request body must be valid JSON matching Contract A schema (v1.0.0)",
		})
	}

	// ── 2. Semantic validation ─────────────────────────────────────────────────
	if errMsg := validateIngestionRequest(&req); errMsg != "" {
		log.Warn("Contract A validation failed",
			"validation_error", errMsg,
			"event_type", req.EventType,
		)
		return c.Status(fiber.StatusUnprocessableEntity).JSON(ErrorResponse{
			Status:  "error",
			Error:   "VALIDATION_FAILED",
			Details: errMsg,
		})
	}

	log.Info("Processing failed payment event",
		"transaction_id", req.Payment.TransactionID,
		"payment_method", req.Payment.PaymentMethod,
		"failure_reason", req.Payment.FailureReasonRaw,
		"amount", req.Payment.Amount,
		"currency", req.Payment.Currency,
		"gateway", req.Payment.GatewayName,
	)

	// ── 3. Parse and validate UUIDs ────────────────────────────────────────────
	txnID, err := uuid.Parse(req.Payment.TransactionID)
	if err != nil {
		log.Error("Invalid transaction_id UUID format", "value", req.Payment.TransactionID)
		return c.Status(fiber.StatusBadRequest).JSON(ErrorResponse{
			Status:  "error",
			Error:   "INVALID_UUID",
			Details: "payment.transaction_id must be a valid UUID v4",
		})
	}

	customerID, err := uuid.Parse(req.Payment.CustomerID)
	if err != nil {
		log.Error("Invalid customer_id UUID format", "value", req.Payment.CustomerID)
		return c.Status(fiber.StatusBadRequest).JSON(ErrorResponse{
			Status:  "error",
			Error:   "INVALID_UUID",
			Details: "payment.customer_id must be a valid UUID v4",
		})
	}

	// merchant_id is optional — parse if provided, silently skip on bad format
	var merchantID *uuid.UUID
	if strings.TrimSpace(req.Payment.MerchantID) != "" {
		mid, parseErr := uuid.Parse(req.Payment.MerchantID)
		if parseErr != nil {
			log.Warn("Invalid merchant_id UUID — field ignored",
				"value", req.Payment.MerchantID)
		} else {
			merchantID = &mid
		}
	}

	// ── 4. Normalise optional fields ───────────────────────────────────────────
	currency := strings.ToUpper(strings.TrimSpace(req.Payment.Currency))
	if currency == "" {
		currency = "INR"
	}

	// Parse the event timestamp; fall back to server time if absent / malformed.
	failedAt := time.Now().UTC()
	if req.EventTimestamp != "" {
		if parsed, parseErr := time.Parse(time.RFC3339Nano, req.EventTimestamp); parseErr == nil {
			failedAt = parsed.UTC()
		} else {
			log.Warn("Could not parse event_timestamp — defaulting to now()",
				"raw_value", req.EventTimestamp)
		}
	}

	// ── 5. Build JSONB gateway_raw_payload ─────────────────────────────────────
	// Store gateway-specific identifiers and event metadata as a structured JSONB
	// blob for future replay / audit, without polluting typed columns.
	var rawPayload datatypes.JSON
	gatewayMeta := map[string]string{
		"event_id":           req.EventID,
		"api_version":        req.APIVersion,
		"gateway_order_id":   req.Payment.GatewayOrderID,
		"gateway_payment_id": req.Payment.GatewayPaymentID,
		"idempotency_key":    req.Payment.IdempotencyKey,
		"source_system":      req.Metadata.SourceSystem,
		"correlation_id":     correlationID,
	}
	if rawBytes, marshalErr := json.Marshal(gatewayMeta); marshalErr == nil {
		rawPayload = datatypes.JSON(rawBytes)
	}

	// ── 6. Hydrate the GORM model ──────────────────────────────────────────────
	event := FailedPaymentEvent{
		TransactionID:            txnID,
		CustomerID:               customerID,
		MerchantID:               merchantID,
		Amount:                   req.Payment.Amount,
		Currency:                 currency,
		PaymentMethod:            req.Payment.PaymentMethod,
		FailureReasonRaw:         req.Payment.FailureReasonRaw,
		CustomerLTV:              req.CustomerContext.CustomerLTV,
		RecentRetries:            int16(req.CustomerContext.RecentRetries),
		TimeSinceLastAttemptMins: req.CustomerContext.TimeSinceLastAttemptMins,
		GatewayName:              ptrStr(req.Payment.GatewayName),
		GatewayErrorCode:         ptrStr(req.Payment.GatewayErrorCode),
		GatewayRawPayload:        rawPayload,
		FailedAt:                 failedAt,
		IsArchived:               false,
	}

	// ── 7. Persist to failed_payments table ────────────────────────────────────
	result := DB.Create(&event)
	if result.Error != nil {
		errStr := result.Error.Error()

		// Duplicate transaction_id → idempotent 409 (not a 5xx)
		if strings.Contains(errStr, "duplicate key") ||
			strings.Contains(errStr, "unique constraint") ||
			strings.Contains(errStr, "23505") { // PostgreSQL unique_violation SQLSTATE
			log.Warn("Duplicate transaction_id received — returning idempotent 409",
				"transaction_id", txnID.String())
			return c.Status(fiber.StatusConflict).JSON(ErrorResponse{
				Status:  "error",
				Error:   "DUPLICATE_TRANSACTION",
				Details: "A failed payment record with this transaction_id already exists",
			})
		}

		log.Error("Database write failed",
			"error", errStr,
			"transaction_id", txnID.String(),
		)
		return c.Status(fiber.StatusInternalServerError).JSON(ErrorResponse{
			Status:  "error",
			Error:   "DATABASE_ERROR",
			Details: "Failed to persist the failed payment event — please retry",
		})
	}

	log.Info("Failed payment event persisted",
		"failed_payment_id", event.ID.String(),
		"transaction_id", txnID.String(),
		"rows_affected", result.RowsAffected,
	)

	// ── 8. Return 201 Created (Contract A success response) ────────────────────
	c.Set("X-Correlation-ID", correlationID)
	return c.Status(fiber.StatusCreated).JSON(IngestionSuccessResponse{
		Status:          "accepted",
		Message:         "Event ingested successfully. Recovery agent queued.",
		FailedPaymentID: event.ID.String(),
		TransactionID:   txnID.String(),
		Timestamp:       time.Now().UTC().Format(time.RFC3339Nano),
	})
}

// =============================================================================
//  Validation
// =============================================================================

// validateIngestionRequest runs semantic validation on all Contract A fields.
// It returns a human-readable error string, or "" if the request is valid.
// Note: UUID format validation is intentionally left to the UUID parse step in
// the handler — this function validates presence and enum membership only.
func validateIngestionRequest(req *EventIngestionRequest) string {
	// Event-level
	if req.EventType != "payment.failed" {
		return "event_type must be exactly 'payment.failed'"
	}
	if strings.TrimSpace(req.EventID) == "" {
		return "event_id is required"
	}

	// Payment object
	if strings.TrimSpace(req.Payment.TransactionID) == "" {
		return "payment.transaction_id is required"
	}
	if strings.TrimSpace(req.Payment.CustomerID) == "" {
		return "payment.customer_id is required"
	}
	if req.Payment.Amount <= 0 {
		return "payment.amount must be greater than 0"
	}
	if strings.TrimSpace(req.Payment.PaymentMethod) == "" {
		return "payment.payment_method is required"
	}
	if !validPaymentMethods[req.Payment.PaymentMethod] {
		return "payment.payment_method must be one of: UPI, Credit Card, Debit Card, Net Banking, Wallet, BNPL"
	}
	if strings.TrimSpace(req.Payment.FailureReasonRaw) == "" {
		return "payment.failure_reason_raw is required"
	}
	if !validFailureReasons[req.Payment.FailureReasonRaw] {
		return "payment.failure_reason_raw must be one of the known failure reason enum values"
	}
	if strings.TrimSpace(req.Payment.IdempotencyKey) == "" {
		return "payment.idempotency_key is required"
	}

	// Customer context
	if req.CustomerContext.CustomerLTV < 0 {
		return "customer_context.customer_ltv must be >= 0"
	}
	if req.CustomerContext.RecentRetries < 0 || req.CustomerContext.RecentRetries > 10 {
		return "customer_context.recent_retries must be between 0 and 10"
	}
	if req.CustomerContext.TimeSinceLastAttemptMins < 0 {
		return "customer_context.time_since_last_attempt_mins must be >= 0"
	}

	return ""
}

// =============================================================================
//  Helpers
// =============================================================================

// ptrStr converts an empty string to a nil pointer — used to represent
// nullable VARCHAR columns (GORM writes nil as SQL NULL).
func ptrStr(s string) *string {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	return &s
}
