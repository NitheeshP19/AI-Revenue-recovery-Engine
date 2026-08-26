"""
AI Revenue Recovery Engine — Stopping Rules
Implements compliance gates and transaction stopping rules.
"""

from __future__ import annotations


def check_stopping_rules(
    transaction_id: str,
    retry_attempt: int,
    customer_ltv: float,
    amount: float,
    failure_reason: str,
    hours_since_first_failure: float
) -> dict:
    """
    Evaluates a transaction against compliance recovery rules.

    Returns:
      {
        "should_stop": bool,
        "rule_triggered": str | None, # name of the rule that fired
        "reason": str | None # human-readable explanation
      }
    """
    # 1. MAX_RETRIES
    if retry_attempt >= 3:
        return {
            "should_stop": True,
            "rule_triggered": "MAX_RETRIES",
            "reason": "Maximum retry attempts (3) reached. Marking as unrecoverable."
        }

    # 2. LOW_LTV_LOW_AMOUNT
    if customer_ltv < 500.0 and amount < 200.0:
        return {
            "should_stop": True,
            "rule_triggered": "LOW_LTV_LOW_AMOUNT",
            "reason": "Customer LTV and transaction amount below recovery threshold. Not cost-effective to retry."
        }

    # 3. TIMEOUT_48H
    if hours_since_first_failure >= 48.0:
        return {
            "should_stop": True,
            "rule_triggered": "TIMEOUT_48H",
            "reason": "48-hour recovery window expired. Escalating to manual review queue."
        }

    # 4. PERMANENT_FAILURE
    permanent_failures = {"card_stolen", "fraud_block", "account_closed", "card_expired"}
    if failure_reason in permanent_failures:
        return {
            "should_stop": True,
            "rule_triggered": "PERMANENT_FAILURE",
            "reason": f"Failure reason '{failure_reason}' is non-retriable. No recovery possible."
        }

    # 5. DEFAULT (Continue)
    return {
        "should_stop": False,
        "rule_triggered": None,
        "reason": None
    }
