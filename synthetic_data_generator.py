"""
===============================================================================
  AI Revenue Recovery System — Synthetic Data Generator
  Author  : Lead Data Engineer (AI Fintech Architecture)
  Version : 1.0.0
  Seed    : 42 (fully reproducible)
-------------------------------------------------------------------------------
  Generates 5,000 failed transaction records with STATISTICALLY MEANINGFUL
  correlations designed to train ML classifiers on the ideal_recovery_action
  target variable.

  Correlation Design:
    - UPI             → higher gateway_timeout rate          (≈45%)
    - Credit Card     → higher expired_card / risk_flag      (≈30%)
    - Debit Card      → higher insufficient_funds            (≈40%)
    - incorrect_pin   → NEVER maps to 'retry_now'
    - recent_retries  > 2 → heavily skews toward 'give_up' or 'switch_method'
    - High customer_ltv  → bias toward 'switch_method' (preserve relationship)
    - gateway_timeout    → high probability of 'retry_later' (transient error)
    - risk_flag          → forces 'give_up' (compliance / fraud)

  Output : failed_transactions.csv
===============================================================================
"""

import uuid
import numpy as np
import pandas as pd
from pathlib import Path

# ── Reproducibility ────────────────────────────────────────────────────────────
np.random.seed(42)

N = 5_000
OUTPUT_FILE = Path(__file__).parent / "failed_transactions.csv"

# ── Constants / Mappings ───────────────────────────────────────────────────────
PAYMENT_METHODS = ["UPI", "Credit Card", "Debit Card"]

FAILURE_REASONS = [
    "insufficient_funds",
    "gateway_timeout",
    "incorrect_pin",
    "expired_card",
    "risk_flag",
]

RECOVERY_ACTIONS = ["retry_now", "retry_later", "switch_method", "give_up"]

# Payment-method → failure-reason conditional probability matrix
# Rows: UPI, Credit Card, Debit Card
# Cols: insufficient_funds, gateway_timeout, incorrect_pin, expired_card, risk_flag
FAILURE_PROB_MATRIX = {
    "UPI":         [0.15, 0.45, 0.20, 0.08, 0.12],
    "Credit Card": [0.15, 0.20, 0.12, 0.25, 0.28],
    "Debit Card":  [0.40, 0.22, 0.18, 0.12, 0.08],
}


def sample_payment_method(n: int) -> np.ndarray:
    """Sample payment methods with realistic market-share weights."""
    return np.random.choice(PAYMENT_METHODS, size=n, p=[0.50, 0.28, 0.22])


def sample_failure_reason(methods: np.ndarray) -> np.ndarray:
    """Sample failure reason conditioned on payment method."""
    reasons = np.empty(len(methods), dtype=object)
    for method, probs in FAILURE_PROB_MATRIX.items():
        mask = methods == method
        reasons[mask] = np.random.choice(
            FAILURE_REASONS, size=mask.sum(), p=probs
        )
    return reasons


def map_recovery_action(row: pd.Series) -> str:
    """
    Deterministic-with-noise mapping that creates learnable ML signal.

    Priority order (highest to lowest):
      1. risk_flag              → always give_up
      2. recent_retries > 2    → give_up (60%) or switch_method (40%)
      3. incorrect_pin         → retry_later (50%) or switch_method (50%)
                                 — NEVER retry_now
      4. gateway_timeout       → retry_later (70%) or retry_now (30%)
      5. expired_card          → switch_method (80%) or give_up (20%)
      6. insufficient_funds
           - high LTV (≥1000)  → retry_later (50%) switch_method (40%) give_up(10%)
           - low  LTV (<1000)  → give_up (50%) retry_later (35%) switch_method(15%)
      7. recent_retries == 0   → retry_now (70%) retry_later (30%)
    """
    reason   = row["failure_reason_raw"]
    retries  = row["recent_retries"]
    ltv      = row["customer_ltv"]

    # --- Hard rules ---
    if reason == "risk_flag":
        return "give_up"

    if retries > 2:
        return np.random.choice(
            ["give_up", "switch_method"],
            p=[0.60, 0.40]
        )

    # --- Reason-specific logic ---
    if reason == "incorrect_pin":
        # NEVER retry_now per business rule
        return np.random.choice(
            ["retry_later", "switch_method"],
            p=[0.50, 0.50]
        )

    if reason == "gateway_timeout":
        return np.random.choice(
            ["retry_later", "retry_now"],
            p=[0.70, 0.30]
        )

    if reason == "expired_card":
        return np.random.choice(
            ["switch_method", "give_up"],
            p=[0.80, 0.20]
        )

    if reason == "insufficient_funds":
        if ltv >= 1_000:
            return np.random.choice(
                ["retry_later", "switch_method", "give_up"],
                p=[0.50, 0.40, 0.10]
            )
        else:
            return np.random.choice(
                ["give_up", "retry_later", "switch_method"],
                p=[0.50, 0.35, 0.15]
            )

    # --- Default (retries == 0, benign) ---
    return np.random.choice(
        ["retry_now", "retry_later"],
        p=[0.70, 0.30]
    )


# ── Feature Generation ─────────────────────────────────────────────────────────

print("[*]  Generating UUIDs...")
transaction_ids = [str(uuid.uuid4()) for _ in range(N)]
customer_ids    = [str(uuid.uuid4()) for _ in range(N)]

print("[*]  Sampling payment methods...")
payment_method = sample_payment_method(N)

print("[*]  Sampling failure reasons (conditioned on payment method)...")
failure_reason_raw = sample_failure_reason(payment_method)

print("[*]  Generating continuous & count features...")

# Amount: log-normal → realistic long tail (₹50 – ₹1,50,000 range)
amount = np.round(
    np.clip(np.random.lognormal(mean=7.5, sigma=1.3, size=N), 50, 150_000),
    decimals=2,
)

# Customer LTV: bimodal — most customers are low-value, some are premium
ltv_low    = np.random.exponential(scale=400,   size=int(N * 0.70))
ltv_high   = np.random.exponential(scale=2_500, size=int(N * 0.30))
customer_ltv = np.round(
    np.clip(np.concatenate([ltv_low, ltv_high])[:N], 10, 50_000),
    decimals=2,
)
np.random.shuffle(customer_ltv)

# Recent retries: zero-inflated (most failed on first attempt)
recent_retries = np.random.choice(
    [0, 1, 2, 3, 4, 5],
    size=N,
    p=[0.45, 0.25, 0.15, 0.08, 0.04, 0.03],
)

# Time since last attempt (minutes): 0–1440 (up to 24 h), skewed early
time_since_last_attempt_mins = np.random.exponential(scale=60, size=N).astype(int)
time_since_last_attempt_mins = np.clip(time_since_last_attempt_mins, 0, 1440)

# ── Assemble DataFrame ─────────────────────────────────────────────────────────

print("[*]  Assembling DataFrame...")
df = pd.DataFrame(
    {
        "transaction_id":             transaction_ids,
        "customer_id":                customer_ids,
        "amount":                     amount,
        "payment_method":             payment_method,
        "failure_reason_raw":         failure_reason_raw,
        "customer_ltv":               customer_ltv,
        "recent_retries":             recent_retries,
        "time_since_last_attempt_mins": time_since_last_attempt_mins,
    }
)

# ── Apply Recovery Action Mapping (vectorised via apply) ───────────────────────

print("[*]  Mapping ideal_recovery_action (applying business rules row-wise)...")
np.random.seed(42)  # Re-seed so apply order is deterministic
df["ideal_recovery_action"] = df.apply(map_recovery_action, axis=1)

# ── Validation & QA Assertions ─────────────────────────────────────────────────

print("\n[?]  Running QA assertions...")

# 1. No NaN anywhere
assert df.isnull().sum().sum() == 0, "[FAIL]  NaN values detected!"

# 2. incorrect_pin NEVER maps to retry_now
pin_retries = df.loc[
    df["failure_reason_raw"] == "incorrect_pin", "ideal_recovery_action"
]
assert "retry_now" not in pin_retries.values, (
    "[FAIL]  Business rule violated: incorrect_pin mapped to retry_now!"
)

# 3. risk_flag always maps to give_up
risk_actions = df.loc[
    df["failure_reason_raw"] == "risk_flag", "ideal_recovery_action"
]
assert (risk_actions == "give_up").all(), (
    "[FAIL]  Business rule violated: risk_flag not always give_up!"
)

# 4. recent_retries range check
assert df["recent_retries"].between(0, 5).all(), "[FAIL]  recent_retries out of range!"

# 5. All categorical values are valid
assert df["payment_method"].isin(PAYMENT_METHODS).all()
assert df["failure_reason_raw"].isin(FAILURE_REASONS).all()
assert df["ideal_recovery_action"].isin(RECOVERY_ACTIONS).all()

print("[OK]  All assertions passed.")

# ── Summary Statistics ─────────────────────────────────────────────────────────

print("\n[STATS]  Class distribution - ideal_recovery_action:")
print(df["ideal_recovery_action"].value_counts(normalize=True).mul(100).round(2))

print("\n[STATS]  Failure reason by payment method:")
print(
    df.groupby(["payment_method", "failure_reason_raw"])
    .size()
    .unstack(fill_value=0)
)

print("\n[STATS]  Average customer_ltv by recovery action:")
print(df.groupby("ideal_recovery_action")["customer_ltv"].mean().round(2))

# ── Export ─────────────────────────────────────────────────────────────────────

df.to_csv(OUTPUT_FILE, index=False)
print(f"\n[DONE]  Saved {N:,} records -> {OUTPUT_FILE}")
print(f"    Shape  : {df.shape}")
print(f"    Columns: {list(df.columns)}")
