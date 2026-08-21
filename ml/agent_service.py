"""
===============================================================================
  AI Revenue Recovery System — Groq Agent Service
  Phase 4 | FastAPI Decision Engine
  Author  : Lead AI Engineer
  Version : 1.0.0
-------------------------------------------------------------------------------
  PURPOSE:
    Serves as the core LLM reasoning agent using the Groq API (Llama-3).
    Receives a payload containing customer profile, payment context, system 
    status, and the classification output from the ML classifier.
    Evaluates context to choose one of four recovery actions:
      • retry_now
      • retry_later
      • switch_method
      • give_up
    Ensures strict JSON output format, handles rate limits (HTTP 429), timeouts,
    and falls back to rule-based decisions if Groq API is unavailable.

  SETUP:
    pip install fastapi uvicorn[standard] groq python-dotenv pydantic
    Set GROQ_API_KEY environment variable.

  RUN:
    python agent_api.py
===============================================================================
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
from groq import Groq, GroqError, APIConnectionError, APITimeoutError, RateLimitError

# Load environment variables from .env
load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt = "%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("agent_service")

# Initialize Groq Client
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    log.warning("Warning: GROQ_API_KEY environment variable is not set. Service will run in FALLBACK-ONLY mode.")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Supported Groq Models (verified as active on Groq API — 2026-08-20)
DEFAULT_MODEL = "groq/compound"
SUPPORTED_GROQ_MODELS = {
    "groq/compound",
    "groq/compound-mini",
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
}

# ══════════════════════════════════════════════════════════════════════════════
#  PYDANTIC SCHEMAS (Aligns with Contract B and Contract C)
# ══════════════════════════════════════════════════════════════════════════════

class AgentConfig(BaseModel):
    model_id:               str     = DEFAULT_MODEL
    decision_version:       int     = 1
    confidence_threshold:   float   = Field(default=0.50, ge=0.0, le=1.0)
    enable_chain_of_thought: bool   = True


class PaymentContext(BaseModel):
    transaction_id:     str
    customer_id:        str
    amount:             float
    currency:           str = "INR"
    payment_method:     str
    failure_reason_raw: Optional[str] = None
    gateway_name:       Optional[str] = None
    gateway_error_code: Optional[str] = None
    merchant_id:        Optional[str] = None


class CustomerProfile(BaseModel):
    customer_ltv:                 float
    recent_retries:               int
    time_since_last_attempt_mins: int
    preferred_payment_methods:    List[str] = Field(default_factory=list)
    account_age_days:             int = 0
    is_vip:                       bool = False


class HistoricalContext(BaseModel):
    total_failed_payments_30d:      Optional[int] = None
    last_successful_payment_method: Optional[str] = None
    last_successful_payment_at:     Optional[str] = None
    average_transaction_value:      Optional[float] = None


class SystemContext(BaseModel):
    gateway_health_status:          Optional[str] = None  # healthy | degraded | down
    current_gateway_error_rate_pct: Optional[float] = None
    is_peak_hour:                   Optional[bool] = None
    estimated_gateway_recovery_mins: Optional[int] = None


class DecisionRequest(BaseModel):
    """
    Contract B request payload representing input context.
    Features robust model validator to automatically maps customer_context -> customer_profile
    and classification_output -> ml_classification for seamless integrations.
    """
    job_id:            str
    failed_payment_id: str
    request_timestamp: Optional[str] = None
    agent_config:      AgentConfig = Field(default_factory=AgentConfig)
    payment_context:   PaymentContext
    customer_profile:  Optional[CustomerProfile] = None
    customer_context:  Optional[CustomerProfile] = None
    historical_context: HistoricalContext = Field(default_factory=HistoricalContext)
    system_context:    SystemContext      = Field(default_factory=SystemContext)
    
    # ML classifier parameters
    ml_classification:     Optional[dict] = None
    classification_output: Optional[dict] = None

    @model_validator(mode='before')
    @classmethod
    def resolve_aliases(cls, data: dict) -> dict:
        if isinstance(data, dict):
            # Fallback customer_context -> customer_profile
            if "customer_context" in data and "customer_profile" not in data:
                data["customer_profile"] = data["customer_context"]
            # Fallback classification_output -> ml_classification
            if "classification_output" in data and "ml_classification" not in data:
                data["ml_classification"] = data["classification_output"]
        return data

    @property
    def resolved_customer_profile(self) -> CustomerProfile:
        if self.customer_profile is not None:
            return self.customer_profile
        if self.customer_context is not None:
            return self.customer_context
        # Safe default if entirely omitted
        return CustomerProfile(customer_ltv=0.0, recent_retries=0, time_since_last_attempt_mins=0)

    @property
    def predicted_failure_reason(self) -> str:
        if self.ml_classification and "predicted_failure_reason" in self.ml_classification:
            return self.ml_classification["predicted_failure_reason"]
        if self.classification_output and "predicted_failure_reason" in self.classification_output:
            return self.classification_output["predicted_failure_reason"]
        if self.payment_context.failure_reason_raw:
            return self.payment_context.failure_reason_raw
        return "unknown"

    @property
    def classification_confidence(self) -> float:
        if self.ml_classification and "confidence_score" in self.ml_classification:
            return float(self.ml_classification["confidence_score"])
        if self.classification_output and "confidence_score" in self.classification_output:
            return float(self.classification_output["confidence_score"])
        return 1.0


# ── Contract C Output Schemas ──────────────────────────────────────────────────

class ActionParameters(BaseModel):
    recommended_retry_delay_mins: Optional[int] = None
    recommended_channel:          Optional[str] = None
    customer_communication_hint:  str
    send_notification:            bool = True
    notification_channel:         str = "email"


class ConsideredAction(BaseModel):
    action:   str
    score:    float
    selected: bool


class ReasoningTrace(BaseModel):
    summary:            str
    decision_path:      str
    feature_vector:     dict
    triggered_rules:    List[str]
    considered_actions: List[ConsideredAction]
    chain_of_thought:   str
    llm_model_used:     str
    prompt_tokens:      int
    completion_tokens:  int
    agent_latency_ms:   int
    schema_version:     str = "1.0.0"


class DecisionResponse(BaseModel):
    """Contract C compliant agent response structure."""
    job_id:            str
    failed_payment_id: str
    transaction_id:    str
    decision_version:  int
    decided_at:        str
    decision:          str  # retry_now | retry_later | switch_method | give_up
    confidence_score:  float
    action_parameters: ActionParameters
    reasoning_trace:   ReasoningTrace


# ══════════════════════════════════════════════════════════════════════════════
#  FALLBACK RULE ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def make_fallback_response(request: DecisionRequest, reason: str) -> DecisionResponse:
    """
    Rule-based fallback logic invoked when the Groq LLM API is unavailable,
    rate-limited, or times out.
    """
    pred_reason = request.predicted_failure_reason
    cust_profile = request.resolved_customer_profile
    
    decision = "retry_later"
    delay = 30
    channel = None
    comm_hint = "We encountered a temporary payment processing issue. We will automatically retry in 30 minutes."
    triggered_rules = ["FALLBACK: GROQ_SERVICE_UNAVAILABLE"]

    # Simple deterministic heuristics
    if cust_profile.recent_retries >= 3:
        decision = "give_up"
        comm_hint = "Payment failed repeatedly. Please verify your payment details and retry manually."
        triggered_rules.append("FALLBACK_RULE: exceeded_retry_limit (recent_retries >= 3) -> give_up")
    elif pred_reason == "expired_card":
        decision = "switch_method"
        comm_hint = "Your card has expired. Please choose a different payment method to complete the payment."
        triggered_rules.append("FALLBACK_RULE: expired_card -> switch_method")
        if cust_profile.preferred_payment_methods:
            channel = cust_profile.preferred_payment_methods[0]
    elif pred_reason in ["insufficient_funds", "incorrect_pin"]:
        decision = "retry_later"
        comm_hint = "Please check your account balance or PIN and retry. We will try again in 30 minutes."
        triggered_rules.append(f"FALLBACK_RULE: customer_side_fault_{pred_reason} -> retry_later")
    else:
        triggered_rules.append("FALLBACK_RULE: transient_error -> retry_later")

    return DecisionResponse(
        job_id            = request.job_id,
        failed_payment_id = request.failed_payment_id,
        transaction_id    = request.payment_context.transaction_id,
        decision_version  = request.agent_config.decision_version,
        decided_at        = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        decision          = decision,
        confidence_score  = 0.5000,
        action_parameters = ActionParameters(
            recommended_retry_delay_mins = delay if decision == "retry_later" else None,
            recommended_channel          = channel,
            customer_communication_hint  = comm_hint,
            send_notification            = True,
            notification_channel         = "email"
        ),
        reasoning_trace   = ReasoningTrace(
            summary            = f"Fallback decision activated. Reason: {reason}",
            decision_path      = f"fallback_rule -> {decision}",
            feature_vector     = {
                "payment_method":     request.payment_context.payment_method,
                "failure_reason_raw": request.payment_context.failure_reason_raw,
                "predicted_failure_reason": pred_reason,
                "recent_retries":     float(cust_profile.recent_retries),
                "customer_ltv":       cust_profile.customer_ltv,
                "is_vip":             cust_profile.is_vip
            },
            triggered_rules    = triggered_rules,
            considered_actions = [
                ConsideredAction(action=decision, score=0.5000, selected=True),
                ConsideredAction(action="retry_now" if decision != "retry_now" else "retry_later", score=0.2000, selected=False)
            ],
            chain_of_thought   = f"The Groq LLM agent was unavailable or timed out ({reason}). Activating rule-based fallback decision.",
            llm_model_used     = "fallback-rule-engine",
            prompt_tokens      = 0,
            completion_tokens  = 0,
            agent_latency_ms   = 0,
            schema_version     = "1.0.0"
        )
    )


# ══════════════════════════════════════════════════════════════════════════════
#  FASTAPI APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title       = "AI Revenue Recovery — Groq Agent Service",
    description = "Decides recovery action using Groq LLM reasoning (Llama-3).",
    version     = "1.0.0",
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins     = os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_methods     = ["GET", "POST", "OPTIONS"],
    allow_headers     = ["*"],
    allow_credentials = False,
)


@app.get("/health", tags=["System"])
async def health_check():
    """Liveness probe. Returns healthy status and agent details."""
    return {
        "status":        "ok",
        "service":       "groq-agent-service",
        "groq_loaded":   client is not None,
        "default_model": DEFAULT_MODEL,
        "version":       "1.0.0"
    }


def clean_json_str(raw: str) -> str:
    """Extracts clean JSON substring from the LLM output."""
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    return raw.strip()


# Direct Mapping to /agent/decide and /agent/v1/decide
@app.post("/agent/decide", response_model=DecisionResponse, tags=["Agent"])
@app.post("/agent/v1/decide", response_model=DecisionResponse, tags=["Agent"])
async def decide(request: DecisionRequest) -> DecisionResponse:
    """
    Ingests classification, customer context, and system status to decide on recovery action.
    Uses Llama-3 (Groq API) with structured output. Falls back to default rules on errors.
    """
    t_start = time.perf_counter()

    # If client is not initialized, go straight to fallback
    if not client:
        log.warning(f"job_id={request.job_id} | Groq Client not initialized. Running fallback.")
        return make_fallback_response(request, "Groq client not configured (Missing GROQ_API_KEY)")

    cust_profile = request.resolved_customer_profile
    pred_reason = request.predicted_failure_reason
    confidence = request.classification_confidence

    # Select the model ID
    model_id = request.agent_config.model_id
    if model_id not in SUPPORTED_GROQ_MODELS:
        log.warning(f"Requested model '{model_id}' is not in supported Groq models list. Defaulting to '{DEFAULT_MODEL}'.")
        model_id = DEFAULT_MODEL

    # Craft System Prompt
    system_prompt = """You are the core AI Revenue Recovery Decision Engine.
Your task is to analyze a failed payment transaction and determine the best recovery action:
- 'retry_now': Use for transient gateway errors (e.g. gateway_timeout) when gateway health is 'healthy' or recovery is immediate.
- 'retry_later': Use for transient errors when gateway health is 'degraded'/'down', or customer-side issues (e.g. insufficient_funds, incorrect_pin) where the customer is high value (high LTV) and needs a buffer window (15-60 mins) to add funds or verify details.
- 'switch_method': Use for payment method-specific failures (e.g. expired_card) or repeated failures where alternatives (UPI, Card, Net Banking) exist.
- 'give_up': Use when retry budget is exhausted (recent_retries >= 3) or the error is permanent and no recovery is viable.

Evaluate:
- ML Classifier Prediction: {predicted_failure_reason} (confidence: {confidence_score})
- Payment context (method: {payment_method}, amount: {amount} INR)
- Customer Profile (LTV: {customer_ltv}, recent retries: {recent_retries}, is VIP: {is_vip}, preferred methods: {preferred_payment_methods})
- System Health (gateway: {gateway_health_status}, error rate: {current_gateway_error_rate_pct}%)

You MUST output your decision in strict JSON format matching this exact schema:
{{
  "decision": "retry_now" | "retry_later" | "switch_method" | "give_up",
  "confidence_score": <float between 0.0 and 1.0>,
  "action_parameters": {{
    "recommended_retry_delay_mins": <integer retry delay in minutes, or null>,
    "recommended_channel": <string name of payment method to switch to, or null>,
    "customer_communication_hint": "<user-facing message explaining status & action>",
    "send_notification": <boolean>,
    "notification_channel": "push" | "sms" | "email" | "whatsapp"
  }},
  "reasoning_trace": {{
    "summary": "<one-sentence summary of decision>",
    "decision_path": "<breadcrumb, e.g. gateway_timeout -> degraded -> retry_later>",
    "triggered_rules": ["<rule 1>", "<rule 2>"],
    "considered_actions": [
      {{"action": "retry_now", "score": <float>, "selected": <boolean>}},
      {{"action": "retry_later", "score": <float>, "selected": <boolean>}},
      {{"action": "switch_method", "score": <float>, "selected": <boolean>}},
      {{"action": "give_up", "score": <float>, "selected": <boolean>}}
    ],
    "chain_of_thought": "<step-by-step reasoning explaining analysis of inputs and decision>"
  }}
}}
Return ONLY the JSON payload, without markdown code fences or conversational text.
""".format(
        predicted_failure_reason = pred_reason,
        confidence_score         = f"{confidence:.4f}",
        payment_method           = request.payment_context.payment_method,
        amount                   = request.payment_context.amount,
        customer_ltv             = cust_profile.customer_ltv,
        recent_retries           = cust_profile.recent_retries,
        is_vip                   = cust_profile.is_vip,
        preferred_payment_methods = ", ".join(cust_profile.preferred_payment_methods),
        gateway_health_status    = request.system_context.gateway_health_status or "healthy",
        current_gateway_error_rate_pct = request.system_context.current_gateway_error_rate_pct or 0.0
    )

    # Invoke Groq API within a try-except block
    try:
        log.info(f"job_id={request.job_id} | Sending decision request to Groq using model={model_id}")
        
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Decide recovery action for job_id {request.job_id}."}
            ],
            model=model_id,
            response_format={"type": "json_object"},
            temperature=0.1,  # Low temperature for highly consistent decision making
            timeout=10.0,     # Prevent hanging requests
        )

        response_text = chat_completion.choices[0].message.content
        log.debug(f"job_id={request.job_id} | Raw Groq Response: {response_text}")

        # Clean and Parse JSON response
        cleaned_json = clean_json_str(response_text)
        decision_data = json.loads(cleaned_json)

        latency_ms = int((time.perf_counter() - t_start) * 1000.0)

        # Assemble full Contract C DecisionResponse
        # Use defaults in case model returns missing keys
        action_params = decision_data.get("action_parameters", {})
        trace_data = decision_data.get("reasoning_trace", {})

        # Re-build considered actions to guarantee schema conformity
        raw_considered = trace_data.get("considered_actions", [])
        considered_actions = []
        for act in raw_considered:
            if isinstance(act, dict) and "action" in act:
                considered_actions.append(
                    ConsideredAction(
                        action   = act.get("action"),
                        score    = float(act.get("score", 0.0)),
                        selected = bool(act.get("selected", False))
                    )
                )

        # Ensure we fall back if no actions were returned
        if not considered_actions:
            considered_actions = [
                ConsideredAction(action=decision_data.get("decision", "retry_later"), score=decision_data.get("confidence_score", 0.5), selected=True)
            ]

        # Extract prompt / usage stats
        usage = chat_completion.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        # Construct final output
        response = DecisionResponse(
            job_id            = request.job_id,
            failed_payment_id = request.failed_payment_id,
            transaction_id    = request.payment_context.transaction_id,
            decision_version  = request.agent_config.decision_version,
            decided_at        = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            decision          = decision_data.get("decision", "retry_later"),
            confidence_score  = round(float(decision_data.get("confidence_score", 0.5)), 4),
            action_parameters = ActionParameters(
                recommended_retry_delay_mins = action_params.get("recommended_retry_delay_mins"),
                recommended_channel          = action_params.get("recommended_channel"),
                customer_communication_hint  = action_params.get("customer_communication_hint", "Payment processing failed. We will retry soon."),
                send_notification            = bool(action_params.get("send_notification", True)),
                notification_channel         = action_params.get("notification_channel", "email")
            ),
            reasoning_trace   = ReasoningTrace(
                summary            = trace_data.get("summary", "Decision completed by Groq Agent."),
                decision_path      = trace_data.get("decision_path", f"{pred_reason} -> {decision_data.get('decision')}"),
                feature_vector     = {
                    "payment_method":           request.payment_context.payment_method,
                    "failure_reason_raw":       request.payment_context.failure_reason_raw,
                    "predicted_failure_reason": pred_reason,
                    "recent_retries":           float(cust_profile.recent_retries),
                    "customer_ltv":             cust_profile.customer_ltv,
                    "is_vip":                   cust_profile.is_vip,
                    "gateway_health":           request.system_context.gateway_health_status or "healthy"
                },
                triggered_rules    = trace_data.get("triggered_rules", []),
                considered_actions = considered_actions,
                chain_of_thought   = trace_data.get("chain_of_thought", "Decision reasoned by Groq agent."),
                llm_model_used     = model_id,
                prompt_tokens      = prompt_tokens,
                completion_tokens  = completion_tokens,
                agent_latency_ms   = latency_ms,
                schema_version     = "1.0.0"
            )
        )
        
        log.info(
            f"job_id={request.job_id} | DECISION='{response.decision}' | CONF={response.confidence_score:.4f} | "
            f"LATENCY={latency_ms}ms | model={model_id}"
        )
        return response

    except (RateLimitError, APITimeoutError, APIConnectionError) as e:
        log.error(f"job_id={request.job_id} | Groq API transient failure: {e.__class__.__name__}: {str(e)}")
        return make_fallback_response(request, f"Groq transient error: {e.__class__.__name__}")
    except GroqError as e:
        log.error(f"job_id={request.job_id} | Groq API fatal error: {str(e)}")
        return make_fallback_response(request, f"Groq fatal error: {str(e)}")
    except json.JSONDecodeError as e:
        log.error(f"job_id={request.job_id} | Failed to decode JSON from Groq output: {str(e)}")
        return make_fallback_response(request, "Groq output was not valid JSON")
    except Exception as e:
        log.error(f"job_id={request.job_id} | Unhandled error in agent decision pipeline: {str(e)}", exc_info=True)
        return make_fallback_response(request, f"Internal pipeline error: {str(e)}")


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT — run locally on port 8002
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(
        "agent_service:app",
        host       = "127.0.0.1",
        port       = 8002,
        reload     = False,
        log_level  = "info",
        access_log = True,
    )
