"""
===============================================================================
  AI Revenue Recovery System — ML Inference Service
  Phase 3 | FastAPI Local Inference API
  Author  : Lead ML Engineer
  Version : 1.0.0
-------------------------------------------------------------------------------
  PURPOSE:
    Serves the trained XGBClassifier as a low-latency REST endpoint.
    Accepts a Contract B payload, applies the IDENTICAL feature engineering
    used in train_model.py, runs inference, and returns:
      • predicted_failure_reason  — most likely failure class
      • confidence_score          — P(predicted class) from predict_proba
      • all_class_probabilities   — full softmax distribution
      • latency_ms                — wall-clock inference time

  SETUP:
    pip install fastapi uvicorn[standard] xgboost pydantic numpy

  RUN:
    Place classifier.json, label_map.json, feature_cols.json in this
    directory, then:
        python inference_service.py
    
    API docs: http://127.0.0.1:8001/docs
===============================================================================
"""

from __future__ import annotations

import json
import time
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import uvicorn
import xgboost as xgb
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt = "%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("inference_api")

# ── Artifact paths (configurable via env vars for Docker / cloud deploy) ───────
ARTIFACTS_DIR    = Path(os.getenv("ARTIFACTS_DIR", Path(__file__).parent))
MODEL_PATH       = ARTIFACTS_DIR / "classifier.json"
LABEL_MAP_PATH   = ARTIFACTS_DIR / "label_map.json"
FEATURE_COLS_PATH = ARTIFACTS_DIR / "feature_cols.json"


# ══════════════════════════════════════════════════════════════════════════════
#  GLOBAL MODEL STATE
#  Loaded once at startup — zero per-request overhead from file I/O.
# ══════════════════════════════════════════════════════════════════════════════

class ModelState:
    """Container for globally loaded model artefacts."""
    model:        Optional[xgb.XGBClassifier] = None
    label_map:    Optional[Dict[str, str]]     = None   # {"0": "expired_card", ...}
    feature_cols: Optional[List[str]]          = None   # ordered feature list
    n_classes:    int                          = 0


STATE = ModelState()


# ── Startup / Shutdown lifecycle ───────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.
    Loads all ML artefacts into memory at startup; releases at shutdown.
    Using lifespan (not @app.on_event) is the modern FastAPI approach.
    """
    # ── STARTUP ───────────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("  AI Revenue Recovery — ML Inference API starting")
    log.info("=" * 60)

    for path, label in [
        (MODEL_PATH,        "classifier.json"),
        (LABEL_MAP_PATH,    "label_map.json"),
        (FEATURE_COLS_PATH, "feature_cols.json"),
    ]:
        if not path.exists():
            log.error(f"MISSING ARTEFACT: {path}")
            log.error("  Run train_model.py and download the 3 output files.")
            raise RuntimeError(f"Required artefact not found: {path}")

    # Load XGBClassifier from native JSON (matches train_model.py export)
    t0 = time.perf_counter()
    STATE.model = xgb.XGBClassifier()
    STATE.model.load_model(str(MODEL_PATH))
    load_ms = (time.perf_counter() - t0) * 1000
    log.info(f"  [OK] Model loaded from '{MODEL_PATH.name}' in {load_ms:.1f}ms")

    # Load label map: {"0": "expired_card", "1": "gateway_timeout", ...}
    with open(LABEL_MAP_PATH, encoding="utf-8") as f:
        STATE.label_map = json.load(f)
    STATE.n_classes = len(STATE.label_map)
    log.info(f"  [OK] Label map loaded — {STATE.n_classes} classes: "
             f"{list(STATE.label_map.values())}")

    # Load feature column list (guarantees alignment with training)
    with open(FEATURE_COLS_PATH, encoding="utf-8") as f:
        STATE.feature_cols = json.load(f)
    log.info(f"  [OK] Feature columns loaded ({len(STATE.feature_cols)} features)")

    # Warm-up inference (JIT compiles XGBoost's prediction path)
    dummy = np.zeros((1, len(STATE.feature_cols)), dtype=np.float32)
    _ = STATE.model.predict_proba(dummy)
    log.info("  [OK] Warm-up inference complete — model is hot")
    log.info(f"  Listening on http://127.0.0.1:8001")
    log.info("=" * 60)

    yield  # ← application runs here

    # ── SHUTDOWN ──────────────────────────────────────────────────────────────
    log.info("Inference API shutting down. Releasing model state.")
    STATE.model        = None
    STATE.label_map    = None
    STATE.feature_cols = None


# ══════════════════════════════════════════════════════════════════════════════
#  PYDANTIC SCHEMAS  (Contract B — inbound; PredictionResponse — outbound)
# ══════════════════════════════════════════════════════════════════════════════

class AgentConfig(BaseModel):
    model_id:               str     = "xgb-failure-classifier-v1"
    decision_version:       int     = 1
    confidence_threshold:   float   = Field(default=0.50, ge=0.0, le=1.0)
    enable_chain_of_thought: bool   = True


class PaymentContext(BaseModel):
    """Core payment fields from Contract B — payment_context object."""
    transaction_id:   str
    customer_id:      str
    amount:           float  = Field(..., gt=0, description="Transaction amount (INR)")
    currency:         str    = Field(default="INR", max_length=3)
    payment_method:   str    = Field(..., description="UPI | Credit Card | Debit Card | ...")
    # failure_reason_raw is intentionally OPTIONAL here:
    # The ML model predicts it; Gateway may have sent an ambiguous / missing code.
    failure_reason_raw: Optional[str] = Field(
        default=None,
        description="Gateway-reported failure reason. ML model will verify / infer this."
    )
    gateway_name:       Optional[str] = None
    gateway_error_code: Optional[str] = None
    merchant_id:        Optional[str] = None

    @field_validator("payment_method")
    @classmethod
    def validate_payment_method(cls, v: str) -> str:
        allowed = {"UPI", "Credit Card", "Debit Card", "Net Banking", "Wallet", "BNPL"}
        if v not in allowed:
            raise ValueError(f"payment_method must be one of {sorted(allowed)}, got '{v}'")
        return v

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("amount must be > 0")
        return round(v, 2)


class CustomerProfile(BaseModel):
    """Customer behavioural snapshot from Contract B — customer_profile object."""
    customer_ltv:               float = Field(..., ge=0)
    recent_retries:             int   = Field(..., ge=0, le=10)
    time_since_last_attempt_mins: int = Field(..., ge=0)
    preferred_payment_methods:  List[str] = Field(default_factory=list)
    account_age_days:           int   = Field(default=0, ge=0)
    is_vip:                     bool  = False


class HistoricalContext(BaseModel):
    """Optional enrichment from Go backend DB lookups."""
    total_failed_payments_30d:      Optional[int]   = None
    last_successful_payment_method: Optional[str]   = None
    last_successful_payment_at:     Optional[str]   = None
    average_transaction_value:      Optional[float] = None


class SystemContext(BaseModel):
    """Optional real-time gateway health signals."""
    gateway_health_status:          Optional[str]   = None  # healthy | degraded | down
    current_gateway_error_rate_pct: Optional[float] = None
    is_peak_hour:                   Optional[bool]  = None
    estimated_gateway_recovery_mins: Optional[int]  = None


class ContractBRequest(BaseModel):
    """
    Full Contract B payload — the exact JSON the Go backend sends to this service.
    See data_contracts.md for the complete specification.
    """
    job_id:            str
    failed_payment_id: str
    request_timestamp: Optional[str] = None

    agent_config:      AgentConfig       = Field(default_factory=AgentConfig)
    payment_context:   PaymentContext
    customer_profile:  CustomerProfile
    historical_context: HistoricalContext = Field(default_factory=HistoricalContext)
    system_context:    SystemContext      = Field(default_factory=SystemContext)


class ClassProbability(BaseModel):
    """Per-class probability for the dashboard's confidence breakdown display."""
    failure_reason: str
    probability:    float


class PredictionResponse(BaseModel):
    """
    ML inference result — consumed by the Go backend and later merged into
    the LLM agent's reasoning_trace (Contract C).
    """
    job_id:                   str
    failed_payment_id:        str
    # ── Primary prediction ─────────────────────────────────────────────────────
    predicted_failure_reason: str    = Field(description="Most probable failure class")
    confidence_score:         float  = Field(description="P(predicted class) — [0, 1]")
    # ── Full softmax distribution ──────────────────────────────────────────────
    all_class_probabilities:  List[ClassProbability]
    # ── Gateway cross-check ────────────────────────────────────────────────────
    gateway_reason_reported:  Optional[str] = Field(
        default=None,
        description="failure_reason_raw as reported by the gateway (for cross-checking)"
    )
    prediction_agrees_with_gateway: Optional[bool] = Field(
        default=None,
        description="True if ML prediction matches the gateway-reported reason"
    )
    # ── Metadata ───────────────────────────────────────────────────────────────
    latency_ms:        float
    model_version:     str = "xgb-failure-classifier-v1"
    feature_vector:    Dict[str, float]  = Field(
        description="Engineered features passed to the model — for audit / explainability"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  FEATURE ENGINEERING  (exact mirror of train_model.py's engineer_features)
# ══════════════════════════════════════════════════════════════════════════════

# Canonical payment method list — must match train_model.py PAYMENT_METHODS
_PAYMENT_METHODS = ["UPI", "Credit Card", "Debit Card"]


def engineer_features(
    payment_context:  PaymentContext,
    customer_profile: CustomerProfile,
) -> Dict[str, float]:
    """
    Applies the IDENTICAL deterministic feature engineering from train_model.py
    to a single incoming Contract B request.

    Returns:
        Ordered dict {feature_name: value} with keys == STATE.feature_cols.

    IMPORTANT: Any change to this function MUST be reflected in train_model.py
    and the model must be retrained. Feature drift causes silent wrong predictions.
    """
    amount      = float(payment_context.amount)
    ltv         = float(customer_profile.customer_ltv)
    retries     = int(customer_profile.recent_retries)
    time_mins   = int(customer_profile.time_since_last_attempt_mins)
    method      = payment_context.payment_method

    # ── Feature 1: retry_velocity ──────────────────────────────────────────────
    retry_velocity = retries / (time_mins + 1.0)

    # ── Feature 2: log_amount ─────────────────────────────────────────────────
    log_amount = float(np.log1p(amount))

    # ── Feature 3: ltv_tier ───────────────────────────────────────────────────
    if ltv <= 500:
        ltv_tier = 0
    elif ltv <= 2_000:
        ltv_tier = 1
    else:
        ltv_tier = 2

    # ── Feature 4: One-hot payment_method ─────────────────────────────────────
    one_hot = {
        f"pm_{m.replace(' ', '_')}": int(method == m)
        for m in _PAYMENT_METHODS
    }

    raw_features = {
        "amount":                        amount,
        "log_amount":                    log_amount,
        "customer_ltv":                  ltv,
        "ltv_tier":                      float(ltv_tier),
        "recent_retries":                float(retries),
        "time_since_last_attempt_mins":  float(time_mins),
        "retry_velocity":                retry_velocity,
        **one_hot,
    }

    return raw_features


def build_feature_array(feature_dict: Dict[str, float]) -> np.ndarray:
    """
    Converts the feature dict → numpy array in the EXACT column order
    the model was trained on (read from feature_cols.json at startup).

    Raises KeyError if a required feature is missing — makes silent bugs loud.
    """
    return np.array(
        [feature_dict[col] for col in STATE.feature_cols],
        dtype=np.float32,
    ).reshape(1, -1)


# ══════════════════════════════════════════════════════════════════════════════
#  FASTAPI APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title       = "AI Revenue Recovery — ML Failure Classifier",
    description = (
        "Predicts the most probable failure reason for a failed payment "
        "transaction. Accepts a Contract B payload and returns a full "
        "probability distribution across all failure classes."
    ),
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# CORS — allows the React dashboard and Go backend to call this service
app.add_middleware(
    CORSMiddleware,
    allow_origins     = os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_methods     = ["GET", "POST", "OPTIONS"],
    allow_headers     = ["*"],
    allow_credentials = False,
)


# ── GET /health ────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check() -> dict:
    """
    Liveness probe — confirms the model is loaded and ready for inference.
    Returns 503 if artefacts failed to load at startup.
    """
    if STATE.model is None or STATE.label_map is None:
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail      = "Model artefacts are not loaded. Check startup logs.",
        )
    return {
        "status":        "ok",
        "model_loaded":  True,
        "n_classes":     STATE.n_classes,
        "classes":       list(STATE.label_map.values()),
        "n_features":    len(STATE.feature_cols),
        "model_version": "xgb-failure-classifier-v1",
    }


# ── POST /predict ──────────────────────────────────────────────────────────────

@app.post(
    "/predict",
    response_model = PredictionResponse,
    status_code    = status.HTTP_200_OK,
    tags           = ["Inference"],
    summary        = "Predict failure reason from a Contract B payload",
    description    = (
        "Accepts the Contract B JSON payload sent by the Go backend. "
        "Applies feature engineering, runs XGBoost inference, and returns "
        "the predicted failure reason with confidence scores and latency."
    ),
)
async def predict(request: ContractBRequest) -> PredictionResponse:
    """
    ML inference endpoint — full flow:
      1. Extract payment_context + customer_profile from Contract B
      2. Apply deterministic feature engineering (mirrors train_colab.py)
      3. Build feature array in correct column order
      4. Run model.predict_proba() → softmax distribution
      5. Extract predicted class and confidence score
      6. Cross-check against gateway-reported failure_reason_raw
      7. Return PredictionResponse with full audit trail
    """
    if STATE.model is None:
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail      = "Model not loaded — service is unavailable",
        )

    t_start = time.perf_counter()

    # ── Step 1–3: Feature engineering → array ─────────────────────────────────
    try:
        feature_dict  = engineer_features(
            request.payment_context,
            request.customer_profile,
        )
        feature_array = build_feature_array(feature_dict)
    except KeyError as e:
        log.error(f"Feature engineering error for job_id={request.job_id}: {e}")
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail      = f"Feature alignment error: missing column {e}. "
                          "Ensure feature_cols.json matches train_model.py.",
        )

    log.info(
        f"job_id={request.job_id} | "
        f"tx={request.payment_context.transaction_id} | "
        f"method={request.payment_context.payment_method} | "
        f"amount={request.payment_context.amount} | "
        f"retries={request.customer_profile.recent_retries}"
    )

    # ── Step 4: Inference ──────────────────────────────────────────────────────
    proba_vector: np.ndarray = STATE.model.predict_proba(feature_array)[0]
    # proba_vector shape: (n_classes,) — softmax, sums to 1.0

    # ── Step 5: Extract prediction and confidence ──────────────────────────────
    pred_idx:   int   = int(np.argmax(proba_vector))
    pred_name:  str   = STATE.label_map[str(pred_idx)]
    confidence: float = float(proba_vector[pred_idx])

    # ── Step 6: Cross-check vs gateway-reported reason ─────────────────────────
    gateway_reason    = request.payment_context.failure_reason_raw
    agrees_with_gateway = (
        (pred_name == gateway_reason)
        if gateway_reason is not None
        else None
    )
    if gateway_reason is not None and not agrees_with_gateway:
        log.warning(
            f"ML PREDICTION DISAGREES WITH GATEWAY | "
            f"job_id={request.job_id} | "
            f"gateway='{gateway_reason}' | "
            f"ml_pred='{pred_name}' | "
            f"ml_confidence={confidence:.4f}"
        )

    # Build full probability distribution for dashboard display
    all_probs = [
        ClassProbability(
            failure_reason = STATE.label_map[str(i)],
            probability    = round(float(p), 6),
        )
        for i, p in enumerate(proba_vector)
    ]
    # Sort descending by probability for readability
    all_probs.sort(key=lambda x: x.probability, reverse=True)

    latency_ms = (time.perf_counter() - t_start) * 1000.0

    log.info(
        f"PREDICTION | job_id={request.job_id} | "
        f"pred='{pred_name}' | conf={confidence:.4f} | "
        f"gateway_agrees={agrees_with_gateway} | "
        f"latency={latency_ms:.2f}ms"
    )

    return PredictionResponse(
        job_id                         = request.job_id,
        failed_payment_id              = request.failed_payment_id,
        predicted_failure_reason       = pred_name,
        confidence_score               = round(confidence, 6),
        all_class_probabilities        = all_probs,
        gateway_reason_reported        = gateway_reason,
        prediction_agrees_with_gateway = agrees_with_gateway,
        latency_ms                     = round(latency_ms, 3),
        model_version                  = "xgb-failure-classifier-v1",
        feature_vector                 = {k: round(v, 6) for k, v in feature_dict.items()},
    )


# ── GET /classes ───────────────────────────────────────────────────────────────

@app.get("/classes", tags=["System"])
async def get_classes() -> dict:
    """Returns the full label map — useful for the React dashboard dropdowns."""
    if STATE.label_map is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return {
        "classes":   STATE.label_map,
        "n_classes": STATE.n_classes,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT — run locally on port 8000
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(
        "inference_service:app",
        host         = "127.0.0.1",
        port         = 8001,      # Change from 8000 to 8001
        reload       = False,     
        log_level    = "info",
        access_log   = True,
    )
