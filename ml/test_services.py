"""
===============================================================================
  AI Revenue Recovery System — Service Tests
  Tests for: ML Inference Service and Groq Agent Service
  Runner  : pytest
  Strategy: FastAPI TestClient — no live server required.
===============================================================================
"""
from __future__ import annotations

import os
import sys
import pytest


# ──────────────────────────────────────────────────────────────────────────────
#  SHARED FIXTURES & TEST DATA
# ──────────────────────────────────────────────────────────────────────────────

VALID_CONTRACT_B = {
    "job_id":            "job-test-001",
    "failed_payment_id": "fp-test-001",
    "payment_context": {
        "transaction_id":     "550e8400-e29b-41d4-a716-446655440000",
        "customer_id":        "550e8400-e29b-41d4-a716-446655440001",
        "amount":             1500.0,
        "currency":           "INR",
        "payment_method":     "UPI",
        "failure_reason_raw": "gateway_timeout",
    },
    "customer_profile": {
        "customer_ltv":                   5000.0,
        "recent_retries":                 1,
        "time_since_last_attempt_mins":   15,
    },
    "agent_config": {
        "model_id":               "llama-3.1-8b-instant",
        "decision_version":       1,
        "confidence_threshold":   0.50,
        "enable_chain_of_thought": True,
    },
    "system_context": {
        "gateway_health_status":          "healthy",
        "current_gateway_error_rate_pct": 2.0,
        "is_peak_hour":                   False,
    },
}

VALID_AGENT_PAYLOAD = {
    "job_id":            "job-agent-001",
    "failed_payment_id": "fp-agent-001",
    "payment_context": {
        "transaction_id":     "550e8400-e29b-41d4-a716-446655440000",
        "customer_id":        "550e8400-e29b-41d4-a716-446655440001",
        "amount":             2500.0,
        "currency":           "INR",
        "payment_method":     "UPI",
        "failure_reason_raw": "insufficient_funds",
    },
    "customer_profile": {
        "customer_ltv":                   8000.0,
        "recent_retries":                 1,
        "time_since_last_attempt_mins":   30,
        "preferred_payment_methods":      [],
        "account_age_days":               365,
        "is_vip":                         False,
    },
    "agent_config": {
        "model_id":               "groq/compound",
        "decision_version":       1,
        "confidence_threshold":   0.5,
        "enable_chain_of_thought": True,
    },
    "system_context": {
        "gateway_health_status":          "healthy",
        "current_gateway_error_rate_pct": 2.0,
        "is_peak_hour":                   False,
    },
}

VALID_DECISIONS = {"retry_now", "retry_later", "switch_method", "give_up"}


# ══════════════════════════════════════════════════════════════════════════════
#  INFERENCE SERVICE TESTS
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def inference_client():
    """Return a FastAPI TestClient for inference_service.py using lifespan context."""
    from fastapi.testclient import TestClient
    try:
        import inference_service as svc
        # Using "with" block is required to execute the lifespan context manager
        # and load the model artifacts (classifier.json, etc.)
        with TestClient(svc.app) as client:
            yield client
    except Exception as e:
        pytest.skip(f"Could not start inference TestClient: {e}")


def test_inference_health(inference_client):
    """GET /health should return 200 with status ok and model_loaded=true."""
    r = inference_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    assert body.get("model_loaded") is True


def test_inference_predict_valid(inference_client):
    """POST /predict with a valid Contract B payload returns a prediction."""
    r = inference_client.post("/predict", json=VALID_CONTRACT_B)
    assert r.status_code == 200
    body = r.json()
    assert "predicted_failure_reason" in body
    assert 0.0 <= body["confidence_score"] <= 1.0


def test_inference_predict_invalid_payment_method(inference_client):
    """Invalid payment_method enum should return 422."""
    bad = {**VALID_CONTRACT_B}
    bad["payment_context"] = {**VALID_CONTRACT_B["payment_context"], "payment_method": "Crypto"}
    r = inference_client.post("/predict", json=bad)
    assert r.status_code == 422


def test_inference_predict_negative_amount(inference_client):
    """Negative amount should return 422."""
    bad = {**VALID_CONTRACT_B}
    bad["payment_context"] = {**VALID_CONTRACT_B["payment_context"], "amount": -1.0}
    r = inference_client.post("/predict", json=bad)
    assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
#  AGENT SERVICE TESTS
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def agent_client():
    """Return a FastAPI TestClient for agent_service."""
    from fastapi.testclient import TestClient
    try:
        import agent_service as svc
        with TestClient(svc.app) as client:
            yield client
    except Exception as e:
        pytest.skip(f"Could not start agent TestClient: {e}")


def test_agent_health(agent_client):
    """GET /health should return 200 with status ok."""
    r = agent_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    assert body.get("service") == "groq-agent-service"


def test_agent_fallback_no_key(agent_client):
    """
    Force client to None on the agent service to verify the fallback
    rule-based decision logic works correctly and returns HTTP 200.
    """
    import agent_service as svc
    # Override global Groq client to None to trigger fallback block
    original_client = svc.client
    svc.client = None

    try:
        r = agent_client.post("/agent/decide", json=VALID_AGENT_PAYLOAD)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert body.get("decision") in VALID_DECISIONS, \
            f"Expected one of {VALID_DECISIONS}, got: {body.get('decision')}"
        # Confirm fallback path was taken
        trace_summary = body.get("reasoning_trace", {}).get("summary", "")
        assert "fallback" in trace_summary.lower() or "Fallback" in trace_summary, \
            f"Expected fallback in reasoning trace, got: {trace_summary!r}"
    finally:
        # Restore original client
        svc.client = original_client
