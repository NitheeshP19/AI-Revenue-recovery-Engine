package main

import (
	"time"

	"github.com/google/uuid"
	"gorm.io/datatypes"
)

// =============================================================================
//  GORM Model — maps to the failed_payments table (Phase 1 schema.sql)
// =============================================================================

// FailedPaymentEvent is the authoritative Go representation of a row in the
// failed_payments table. Every column name, type, and constraint matches the
// Phase 1 PostgreSQL schema exactly.
type FailedPaymentEvent struct {
	// ── Identity ──────────────────────────────────────────────────────────────
	ID            uuid.UUID  `gorm:"type:uuid;primaryKey;default:gen_random_uuid()" json:"id"`
	TransactionID uuid.UUID  `gorm:"type:uuid;uniqueIndex;not null;column:transaction_id"      json:"transaction_id"`
	CustomerID    uuid.UUID  `gorm:"type:uuid;not null;column:customer_id"                     json:"customer_id"`
	MerchantID    *uuid.UUID `gorm:"type:uuid;column:merchant_id"                              json:"merchant_id,omitempty"`

	// ── Payment Details ───────────────────────────────────────────────────────
	Amount           float64 `gorm:"type:numeric(14,2);not null;column:amount"                       json:"amount"`
	Currency         string  `gorm:"type:char(3);not null;default:'INR';column:currency"             json:"currency"`
	// GORM writes the Go string value directly into the payment_method_enum column.
	// The DB validates the enum constraint — no second validation needed here.
	PaymentMethod    string  `gorm:"type:payment_method_enum;not null;column:payment_method"         json:"payment_method"`
	FailureReasonRaw string  `gorm:"type:failure_reason_enum;not null;column:failure_reason_raw"     json:"failure_reason_raw"`

	// ── Customer Context (snapshot at time of failure) ────────────────────────
	CustomerLTV              float64 `gorm:"type:numeric(14,2);not null;default:0.00;column:customer_ltv"           json:"customer_ltv"`
	RecentRetries            int16   `gorm:"type:smallint;not null;default:0;column:recent_retries"                 json:"recent_retries"`
	TimeSinceLastAttemptMins int     `gorm:"not null;default:0;column:time_since_last_attempt_mins"                 json:"time_since_last_attempt_mins"`

	// ── Gateway / Source Metadata ─────────────────────────────────────────────
	GatewayName      *string        `gorm:"type:varchar(64);column:gateway_name"          json:"gateway_name,omitempty"`
	GatewayErrorCode *string        `gorm:"type:varchar(64);column:gateway_error_code"    json:"gateway_error_code,omitempty"`
	// GatewayRawPayload stores the complete gateway metadata as JSONB.
	// datatypes.JSON marshals/unmarshals seamlessly with pgx/lib-pq.
	GatewayRawPayload datatypes.JSON `gorm:"type:jsonb;column:gateway_raw_payload"         json:"gateway_raw_payload,omitempty"`

	// ── Lifecycle ─────────────────────────────────────────────────────────────
	IsArchived bool      `gorm:"not null;default:false;column:is_archived" json:"is_archived"`
	FailedAt   time.Time `gorm:"not null;default:now();column:failed_at"    json:"failed_at"`
	CreatedAt  time.Time `gorm:"column:created_at"                          json:"created_at"`
	UpdatedAt  time.Time `gorm:"column:updated_at"                          json:"updated_at"`
}

// TableName overrides GORM's default pluralisation so it targets failed_payments.
func (FailedPaymentEvent) TableName() string {
	return "failed_payments"
}

// =============================================================================
//  Contract A — Request DTO (nested, exactly mirrors the Phase 1 JSON contract)
// =============================================================================

// EventIngestionRequest is the top-level struct for the Contract A JSON body
// sent by the upstream payment system to POST /api/v1/events/failure.
type EventIngestionRequest struct {
	EventType      string          `json:"event_type"`
	EventID        string          `json:"event_id"`
	EventTimestamp string          `json:"event_timestamp"`
	APIVersion     string          `json:"api_version"`
	Payment        PaymentPayload  `json:"payment"`
	CustomerContext CustomerContext `json:"customer_context"`
	Metadata       MetadataPayload `json:"metadata"`
}

// PaymentPayload maps the "payment" object in Contract A.
type PaymentPayload struct {
	TransactionID    string  `json:"transaction_id"`
	CustomerID       string  `json:"customer_id"`
	MerchantID       string  `json:"merchant_id"`
	Amount           float64 `json:"amount"`
	Currency         string  `json:"currency"`
	PaymentMethod    string  `json:"payment_method"`
	FailureReasonRaw string  `json:"failure_reason_raw"`
	GatewayName      string  `json:"gateway_name"`
	GatewayErrorCode string  `json:"gateway_error_code"`
	GatewayOrderID   string  `json:"gateway_order_id"`
	GatewayPaymentID string  `json:"gateway_payment_id"`
	IdempotencyKey   string  `json:"idempotency_key"`
}

// CustomerContext maps the "customer_context" object in Contract A.
type CustomerContext struct {
	CustomerLTV              float64  `json:"customer_ltv"`
	RecentRetries            int      `json:"recent_retries"`
	TimeSinceLastAttemptMins int      `json:"time_since_last_attempt_mins"`
	PreferredPaymentMethods  []string `json:"preferred_payment_methods"`
	AccountAgeDays           int      `json:"account_age_days"`
	IsVIP                    bool     `json:"is_vip"`
}

// MetadataPayload maps the "metadata" object in Contract A.
type MetadataPayload struct {
	SourceSystem  string `json:"source_system"`
	Environment   string `json:"environment"`
	CorrelationID string `json:"correlation_id"`
}

// =============================================================================
//  Standard API Response Envelopes
// =============================================================================

// IngestionSuccessResponse is the 201 Created body returned after a successful
// event write — mirrors the "Success Response" defined in Contract A.
type IngestionSuccessResponse struct {
	Status          string `json:"status"`
	Message         string `json:"message"`
	FailedPaymentID string `json:"failed_payment_id"`
	TransactionID   string `json:"transaction_id"`
	Timestamp       string `json:"timestamp"`
}

// ErrorResponse is the uniform error envelope returned on 4xx / 5xx.
type ErrorResponse struct {
	Status  string `json:"status"`
	Error   string `json:"error"`
	Details string `json:"details,omitempty"`
}

// HealthResponse is the body returned by GET /health.
type HealthResponse struct {
	Status      string `json:"status"`
	DBConnected bool   `json:"db_connected"`
	Version     string `json:"version"`
	Environment string `json:"environment"`
}
