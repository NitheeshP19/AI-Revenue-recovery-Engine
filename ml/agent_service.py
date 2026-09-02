"""
===============================================================================
  AI Revenue Recovery System — AI Agent Service (Gemini / Groq)
  Phase 4 | FastAPI Decision Engine
  Author  : Lead AI Engineer
  Version : 2.0.0
-------------------------------------------------------------------------------
  PURPOSE:
    Serves as the core LLM reasoning agent using Google Gemini (or Groq).
    Receives a payload containing customer profile, payment context, system 
    status, and the classification output from the ML classifier.
    Evaluates context to choose one of four recovery actions:
      • retry_now
      • retry_later
      • switch_method
      • give_up
    Ensures strict JSON output format, handles rate limits, timeouts,
    and falls back to rule-based decisions if LLM API is unavailable.

  SETUP:
    pip install fastapi uvicorn[standard] google-genai groq python-dotenv pydantic
    Set GEMINI_API_KEY (or GROQ_API_KEY) environment variable.

  RUN:
    python agent_service.py
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

from pathlib import Path

# Load environment variables from .env
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path)
else:
    load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt = "%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("agent_service")

# ── LLM Client Initialization ──────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

gemini_client = None
if GEMINI_API_KEY:
    try:
        from google import genai
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        log.info("Google Gemini client initialized successfully.")
    except Exception as e:
        log.error(f"Failed to initialize Google Gemini client: {e}")

groq_client = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
        log.info("Groq client initialized successfully.")
    except Exception as e:
        log.error(f"Failed to initialize Groq client: {e}")

if not gemini_client and not groq_client:
    log.warning("Warning: Neither GEMINI_API_KEY nor GROQ_API_KEY is set. Service will run in FALLBACK-ONLY mode.")

# Supported Models
DEFAULT_GEMINI_MODEL = "gemini-3.7-flash"
SUPPORTED_GEMINI_MODELS = {
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.6-flash",
}

DEFAULT_GROQ_MODEL = "groq/compound"
SUPPORTED_GROQ_MODELS = {
    "groq/compound",
    "groq/compound-mini",
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
}

DEFAULT_MODEL = DEFAULT_GEMINI_MODEL if gemini_client else DEFAULT_GROQ_MODEL

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
    action_rationale:  str
    action_parameters: ActionParameters
    reasoning_trace:   ReasoningTrace
    # Populated ONLY when the LLM agent was unavailable and the response was
    # generated by the rule-based fallback engine instead. None means genuine AI.
    fallback_status:   Optional[str] = None  # "agent_unavailable_fallback" | None


# ══════════════════════════════════════════════════════════════════════════════
#  FALLBACK RULE ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def make_fallback_response(request: DecisionRequest, reason: str) -> DecisionResponse:
    """
    Rule-based fallback logic invoked when the LLM API is unavailable,
    rate-limited, or times out.
    """
    pred_reason = request.predicted_failure_reason
    cust_profile = request.resolved_customer_profile
    
    decision = "retry_later"
    delay = 30
    channel = None
    comm_hint = "We encountered a temporary payment processing issue. We will automatically retry in 30 minutes."
    triggered_rules = [f"FALLBACK: {reason.upper()}"]

    # Simple deterministic heuristics
    if cust_profile.recent_retries >= 3:
        decision = "give_up"
        comm_hint = "Payment failed repeatedly. Please verify your payment details and retry manually."
        triggered_rules.append("FALLBACK_RULE: exceeded_retry_limit (recent_retries >= 3) -> give_up")
    elif pred_reason == "expired_card":
        decision = "switch_method"
        comm_hint = "Your card has expired. Please choose a different payment method to complete the payment."
        triggered_rules.append("FALLBACK_RULE: permanent_instrument_failure (expired_card) -> switch_method")
    elif pred_reason == "gateway_timeout":
        decision = "retry_now"
        delay = None
        comm_hint = "Payment gateway timed out. Retrying your transaction immediately."
        triggered_rules.append("FALLBACK_RULE: transient_network_error (gateway_timeout) -> retry_now")
    elif pred_reason == "insufficient_funds":
        decision = "retry_later"
        delay = 60
        comm_hint = "Insufficient funds detected. We will re-attempt your payment shortly."
        triggered_rules.append("FALLBACK_RULE: customer_balance_insufficient (insufficient_funds) -> retry_later")
    elif pred_reason == "risk_flag":
        decision = "retry_later"
        delay = 120
        comm_hint = "Transaction flagged by risk filters. Retrying after safety cooldown."
        triggered_rules.append("FALLBACK_RULE: fraud_velocity_cooldown (risk_flag) -> retry_later")
    elif pred_reason == "incorrect_pin":
        decision = "switch_method"
        comm_hint = "Authentication failed. Please verify credentials or switch payment method."
        triggered_rules.append("FALLBACK_RULE: authentication_credential_error (incorrect_pin) -> switch_method")

    considered = [
        ConsideredAction(action=decision, score=0.99, selected=True),
        ConsideredAction(action="retry_now" if decision != "retry_now" else "retry_later", score=0.01, selected=False)
    ]

    return DecisionResponse(
        job_id            = request.job_id,
        failed_payment_id = request.failed_payment_id,
        transaction_id    = request.payment_context.transaction_id,
        decision_version  = request.agent_config.decision_version,
        decided_at        = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        decision          = decision,
        confidence_score  = 0.50,
        action_rationale  = f"Decision made by rule-based fallback engine: {reason}",
        action_parameters = ActionParameters(
            recommended_retry_delay_mins = delay,
            recommended_channel          = channel,
            customer_communication_hint  = comm_hint,
            send_notification            = True,
            notification_channel         = "email"
        ),
        reasoning_trace   = ReasoningTrace(
            summary            = f"Fallback heuristic applied due to: {reason}",
            decision_path      = f"{pred_reason} -> [FALLBACK] -> {decision}",
            feature_vector     = {
                "payment_method":           request.payment_context.payment_method,
                "failure_reason_raw":       request.payment_context.failure_reason_raw,
                "predicted_failure_reason": pred_reason,
                "recent_retries":           float(cust_profile.recent_retries),
                "customer_ltv":             cust_profile.customer_ltv,
                "is_vip":                   cust_profile.is_vip,
                "gateway_health":           request.system_context.gateway_health_status or "healthy"
            },
            triggered_rules    = triggered_rules,
            considered_actions = considered,
            chain_of_thought   = f"LLM agent unavailable ({reason}). Executed baseline rule heuristic based on failure reason '{pred_reason}' and retry count {cust_profile.recent_retries}.",
            llm_model_used     = "rule-based-fallback-engine",
            prompt_tokens      = 0,
            completion_tokens  = 0,
            agent_latency_ms   = 0,
            schema_version     = "1.0.0"
        ),
        fallback_status = "agent_unavailable_fallback"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  FASTAPI APP
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title       = "AI Revenue Recovery System — AI Agent Service",
    description = "LLM Recovery Reasoning Agent supporting Google Gemini and Groq with fallback heuristics",
    version     = "2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


@app.get("/health", tags=["System"])
async def health():
    return {
        "status":  "healthy",
        "service": "ai-agent-service",
        "version": "2.0.0",
        "active_provider": "gemini" if gemini_client else ("groq" if groq_client else "fallback_only")
    }


@app.get("/agent/status", tags=["System"])
async def agent_status():
    """Reports whether the LLM client is available or running in fallback-only mode."""
    available = (gemini_client is not None) or (groq_client is not None)
    return {
        "gemini_available": gemini_client is not None,
        "groq_available":   groq_client is not None,
        "llm_available":    available,
        "fallback_only":    not available,
        "fallback_reason":  "No valid GEMINI_API_KEY or GROQ_API_KEY configured" if not available else None,
        "status":           "ok" if available else "degraded",
        "active_provider":  "gemini" if gemini_client else ("groq" if groq_client else "fallback_only")
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
    Uses Google Gemini (or Groq) with structured output. Falls back to default rules on errors.
    """
    t_start = time.perf_counter()

    # If neither client is initialized, go straight to fallback
    if not gemini_client and not groq_client:
        log.warning(f"job_id={request.job_id} | No LLM Client initialized. Running fallback.")
        return make_fallback_response(request, "LLM client not configured (Missing GEMINI_API_KEY / GROQ_API_KEY)")

    cust_profile = request.resolved_customer_profile
    pred_reason = request.predicted_failure_reason
    confidence = request.classification_confidence

    # Select model ID and provider
    req_model = request.agent_config.model_id

    if gemini_client:
        provider = "gemini"
        if req_model in SUPPORTED_GEMINI_MODELS:
            model_id = req_model
        else:
            model_id = DEFAULT_GEMINI_MODEL
    else:
        provider = "groq"
        if req_model in SUPPORTED_GROQ_MODELS:
            model_id = req_model
        else:
            model_id = DEFAULT_GROQ_MODEL

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
  "action_rationale": "<a one-sentence plain-English explanation of why this action was chosen>",
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

    try:
        response_text = ""
        prompt_tokens = 0
        completion_tokens = 0

        if provider == "gemini":
            log.info(f"job_id={request.job_id} | Sending decision request to Google Gemini using model={model_id}")
            from google.genai import types
            gemini_config = types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            )
            candidate_models = [model_id] + [m for m in ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.6-flash"] if m != model_id]
            last_err = None
            gemini_response = None
            for candidate in candidate_models:
                try:
                    gemini_response = gemini_client.models.generate_content(
                        model=candidate,
                        contents=f"{system_prompt}\n\nDecide recovery action for job_id {request.job_id}.",
                        config=gemini_config
                    )
                    model_id = candidate
                    break
                except Exception as ex:
                    last_err = ex
                    log.warning(f"job_id={request.job_id} | Model {candidate} returned error: {ex}. Trying next candidate...")
            
            if gemini_response is not None:
                response_text = gemini_response.text or ""
                if hasattr(gemini_response, "usage_metadata") and gemini_response.usage_metadata:
                    prompt_tokens = getattr(gemini_response.usage_metadata, "prompt_token_count", 0) or 0
                    completion_tokens = getattr(gemini_response.usage_metadata, "candidates_token_count", 0) or 0
            elif groq_client:
                # Secondary LLM backup: Groq
                log.info(f"job_id={request.job_id} | All Gemini models busy/exhausted. Falling back to Groq LLM...")
                provider = "groq"
                model_id = DEFAULT_GROQ_MODEL
                chat_completion = groq_client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Decide recovery action for job_id {request.job_id}."}
                    ],
                    model=model_id,
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    timeout=10.0,
                )
                response_text = chat_completion.choices[0].message.content
                usage = chat_completion.usage
                prompt_tokens = usage.prompt_tokens if usage else 0
                completion_tokens = usage.completion_tokens if usage else 0
            else:
                raise last_err or Exception("All LLM providers failed")
        else:
            log.info(f"job_id={request.job_id} | Sending decision request to Groq using model={model_id}")
            chat_completion = groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Decide recovery action for job_id {request.job_id}."}
                ],
                model=model_id,
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=10.0,
            )
            response_text = chat_completion.choices[0].message.content
            usage = chat_completion.usage
            prompt_tokens = usage.prompt_tokens if usage else 0
            completion_tokens = usage.completion_tokens if usage else 0

        log.debug(f"job_id={request.job_id} | Raw Response: {response_text}")

        # Clean and Parse JSON response
        cleaned_json = clean_json_str(response_text)
        decision_data = json.loads(cleaned_json)

        latency_ms = int((time.perf_counter() - t_start) * 1000.0)

        # Assemble full Contract C DecisionResponse
        action_params = decision_data.get("action_parameters", {})
        trace_data = decision_data.get("reasoning_trace", {})

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

        if not considered_actions:
            considered_actions = [
                ConsideredAction(action=decision_data.get("decision", "retry_later"), score=decision_data.get("confidence_score", 0.5), selected=True)
            ]

        # Construct final output
        response = DecisionResponse(
            job_id            = request.job_id,
            failed_payment_id = request.failed_payment_id,
            transaction_id    = request.payment_context.transaction_id,
            decision_version  = request.agent_config.decision_version,
            decided_at        = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            decision          = decision_data.get("decision", "retry_later"),
            confidence_score  = round(float(decision_data.get("confidence_score", 0.5)), 4),
            action_rationale  = decision_data.get("action_rationale", "AI decided recovery action based on payment failure characteristics."),
            action_parameters = ActionParameters(
                recommended_retry_delay_mins = action_params.get("recommended_retry_delay_mins"),
                recommended_channel          = action_params.get("recommended_channel"),
                customer_communication_hint  = action_params.get("customer_communication_hint", "Payment processing failed. We will retry soon."),
                send_notification            = bool(action_params.get("send_notification", True)),
                notification_channel         = action_params.get("notification_channel", "email")
            ),
            reasoning_trace   = ReasoningTrace(
                summary            = trace_data.get("summary", f"Decision completed by {provider.capitalize()} Agent."),
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
                chain_of_thought   = trace_data.get("chain_of_thought", f"Decision reasoned by {provider.capitalize()} agent."),
                llm_model_used     = model_id,
                prompt_tokens      = prompt_tokens,
                completion_tokens  = completion_tokens,
                agent_latency_ms   = latency_ms,
                schema_version     = "2.0.0"
            )
        )
        
        log.info(
            f"job_id={request.job_id} | DECISION='{response.decision}' | CONF={response.confidence_score:.4f} | "
            f"LATENCY={latency_ms}ms | provider={provider} | model={model_id}"
        )
        return response

    except json.JSONDecodeError as e:
        log.error(f"job_id={request.job_id} | Failed to decode JSON from {provider} output: {str(e)}")
        return make_fallback_response(request, f"{provider} output was not valid JSON")
    except Exception as e:
        log.error(f"job_id={request.job_id} | Unhandled error in agent decision pipeline: {str(e)}", exc_info=True)
        return make_fallback_response(request, f"{provider} error: {str(e)}")


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
