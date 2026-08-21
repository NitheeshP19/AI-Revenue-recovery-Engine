"""
===============================================================================
  AI Revenue Recovery System — Recovery Simulation & Metrics Engine
  Phase 5 | Strategy Benchmark & Business Case Generator
  Author  : Senior Fintech Data & Simulation Engineer
  Version : 1.0.0
-------------------------------------------------------------------------------
  PURPOSE:
    Runs a side-by-side benchmark of two recovery strategies against a sample
    of real failed transactions from failed_transactions.csv:

    Strategy A — Baseline Rule-Based (Heuristic)
      Standard industry rules based purely on amount and retry count.

    Strategy B — AI Agent Strategy (Groq + XGBoost)
      Calls the Phase 4 Groq Agent Service (POST /agent/decide) for each
      transaction and uses its structured JSON decision. Falls back to the
      heuristic if the service is unreachable.

    A stochastic execution matrix converts each (failure_reason, action) pair
    into a probabilistic outcome, simulating real-world recovery rates.

    Outputs a structured metrics_summary.json for the React/Recharts dashboard.

  SETUP:
    pip install requests pandas numpy

  RUN:
    # Default (200 transactions, calls live agent at http://127.0.0.1:8002)
    python simulation_engine.py

    # Custom batch size and endpoint
    python simulation_engine.py --sample 500 --agent-url http://127.0.0.1:8002

    # Offline / fallback-only mode (skips HTTP calls, uses heuristic for AI too)
    python simulation_engine.py --offline
===============================================================================
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt = "%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("simulation_engine")

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
CSV_PATH       = BASE_DIR / "failed_transactions.csv"
OUTPUT_PATH    = BASE_DIR / "metrics_summary.json"

# ── Agent Endpoint ─────────────────────────────────────────────────────────────
DEFAULT_AGENT_URL   = "http://127.0.0.1:8002"
AGENT_DECIDE_PATH   = "/agent/decide"
AGENT_TIMEOUT_SEC   = 45.0   # Increased: Groq internal retries can take 30s+

# ── Rate-limit throttle: small gap avoids burst 429s without excessive delay ────
AGENT_REQUEST_DELAY_SEC = 0.5

# ── Simulation Config ──────────────────────────────────────────────────────────
DEFAULT_SAMPLE_SIZE = 200
RANDOM_SEED         = 42


# ══════════════════════════════════════════════════════════════════════════════
#  STOCHASTIC EXECUTION MATRIX
#  P(recovery | failure_reason, action) — informed by industry research and
#  realistic fintech recovery rates.
# ══════════════════════════════════════════════════════════════════════════════

RECOVERY_MATRIX: Dict[str, Dict[str, float]] = {
    # insufficient_funds: customer must top up or wait for salary cycle
    "insufficient_funds": {
        "retry_now":     0.05,   # Almost always fails immediately again
        "retry_later":   0.65,   # Strong success after salary cycle buffer
        "switch_method": 0.50,   # Switching works if another funded account exists
        "give_up":       0.00,
    },
    # expired_card: any retry is hopeless; must switch channel entirely
    "expired_card": {
        "retry_now":     0.00,
        "retry_later":   0.00,
        "switch_method": 0.80,   # Very high success if alternative method available
        "give_up":       0.00,
    },
    # gateway_timeout: purely transient; immediate retry has high success
    "gateway_timeout": {
        "retry_now":     0.85,   # High immediate success
        "retry_later":   0.72,   # Still very high after small delay
        "switch_method": 0.55,   # Moderate: new gateway may also be under load
        "give_up":       0.00,
    },
    # incorrect_pin: customer-side error, repeat retries ineffective
    "incorrect_pin": {
        "retry_now":     0.10,   # Rare accidental fix
        "retry_later":   0.10,   # Still unlikely — user must change PIN
        "switch_method": 0.70,   # High success if switched to UPI or saved method
        "give_up":       0.00,
    },
    # risk_flag: security holds; often requires human review or cool-down
    "risk_flag": {
        "retry_now":     0.12,   # Low — fraud systems usually block immediately
        "retry_later":   0.45,   # Moderate — cooling period helps automated review
        "switch_method": 0.35,   # Moderate — different instrument sometimes clears
        "give_up":       0.00,
    },
    # Catch-all for unknown failure reasons
    "unknown": {
        "retry_now":     0.20,
        "retry_later":   0.40,
        "switch_method": 0.30,
        "give_up":       0.00,
    },
}


def simulate_recovery_outcome(failure_reason: str, action: str) -> bool:
    """
    Probabilistic outcome simulation. Returns True (recovered) or False (failed).

    Draws from the stochastic RECOVERY_MATRIX using the canonical failure_reason.
    Any 'give_up' action unconditionally returns False (no recovery attempted).
    Unknown failure_reason values are handled by the 'unknown' bucket.
    """
    if action == "give_up":
        return False

    # Normalize to known bucket
    reason_key = failure_reason if failure_reason in RECOVERY_MATRIX else "unknown"
    probabilities = RECOVERY_MATRIX[reason_key]

    # Normalize action to known key
    action_key = action if action in probabilities else "retry_later"
    success_prob = probabilities[action_key]

    return random.random() < success_prob


# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY A — BASELINE RULE-BASED (HEURISTIC)
# ══════════════════════════════════════════════════════════════════════════════

def rule_based_decision(amount: float, recent_retries: int) -> str:
    """
    Standard industry heuristic decision engine.

    Rules (in priority order):
      1. recent_retries >= 3  → give_up   (retry budget exhausted)
      2. amount < 100         → retry_now  (low-value, fast retry)
      3. amount >= 100        → retry_later (high-value, allow delay)
    """
    if recent_retries >= 3:
        return "give_up"
    if amount < 100:
        return "retry_now"
    return "retry_later"


# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY B — AI AGENT (GROQ / LLAMA-3)
# ══════════════════════════════════════════════════════════════════════════════

def ai_agent_decision(
    transaction: dict,
    agent_url: str,
    session: requests.Session,
) -> Tuple[str, str, int]:
    """
    Calls the Phase 4 Groq Agent microservice to get a structured recovery decision.

    Returns:
        (action, reasoning_summary, latency_ms)

    On any HTTP/connection error, falls back to rule_based_decision and logs a warning.
    """
    payload = {
        "job_id":            f"sim_{transaction['transaction_id']}",
        "failed_payment_id": transaction["transaction_id"],
        "request_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent_config": {
            "model_id":               "llama-3.1-8b-instant",
            "decision_version":       1,
            "confidence_threshold":   0.50,
            "enable_chain_of_thought": True,
        },
        "payment_context": {
            "transaction_id":     transaction["transaction_id"],
            "customer_id":        transaction["customer_id"],
            "amount":             float(transaction["amount"]),
            "currency":           "INR",
            "payment_method":     transaction["payment_method"],
            "failure_reason_raw": transaction["failure_reason_raw"],
        },
        "customer_profile": {
            "customer_ltv":                   float(transaction["customer_ltv"]),
            "recent_retries":                 int(transaction["recent_retries"]),
            "time_since_last_attempt_mins":   int(transaction["time_since_last_attempt_mins"]),
            "preferred_payment_methods":      [],
            "account_age_days":               0,
            "is_vip":                         False,
        },
        "system_context": {
            "gateway_health_status":          "healthy",
            "current_gateway_error_rate_pct": 2.0,
            "is_peak_hour":                   False,
        },
    }

    t_start = time.perf_counter()
    try:
        resp = session.post(
            f"{agent_url}{AGENT_DECIDE_PATH}",
            json=payload,
            timeout=AGENT_TIMEOUT_SEC,
        )
        latency_ms = int((time.perf_counter() - t_start) * 1000)

        if resp.status_code == 200:
            data = resp.json()
            action = data.get("decision", "retry_later")
            summary = (
                data.get("reasoning_trace", {}).get("summary", "AI decision")
            )
            return action, summary, latency_ms
        else:
            log.warning(f"Agent returned HTTP {resp.status_code} for {transaction['transaction_id']} — using fallback")

    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        latency_ms = int((time.perf_counter() - t_start) * 1000)
        log.warning(f"Agent unreachable ({e.__class__.__name__}) — using rule-based fallback")

    # Graceful fallback: rule-based heuristic
    fallback_action = rule_based_decision(float(transaction["amount"]), int(transaction["recent_retries"]))
    latency_ms = int((time.perf_counter() - t_start) * 1000)
    return fallback_action, "Fallback: agent unreachable — heuristic applied", latency_ms


# ══════════════════════════════════════════════════════════════════════════════
#  DATA SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TransactionResult:
    transaction_id:    str
    amount:            float
    failure_reason:    str
    payment_method:    str

    # Strategy A
    rule_action:       str
    rule_recovered:    bool

    # Strategy B
    ai_action:         str
    ai_recovered:      bool
    ai_reasoning_trace: str
    ai_latency_ms:     int


@dataclass
class StrategyMetrics:
    name:               str
    total_transactions: int
    recovered_count:    int
    recovery_rate_pct:  float
    revenue_recovered:  float
    avg_latency_ms:     float
    action_breakdown:   Dict[str, int]


# ══════════════════════════════════════════════════════════════════════════════
#  DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_transactions(csv_path: Path, sample_size: int) -> List[dict]:
    """
    Loads the failed transactions CSV and returns a stratified random sample.
    Stratification ensures all failure_reason classes are represented proportionally.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    with open(csv_path, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    total = len(all_rows)
    log.info(f"Loaded {total} transactions from {csv_path.name}")

    if sample_size >= total:
        log.info(f"Sample size {sample_size} >= total {total}; using full dataset")
        sample = all_rows
    else:
        # Stratified sampling by failure_reason
        from collections import defaultdict
        by_reason: Dict[str, List[dict]] = defaultdict(list)
        for row in all_rows:
            by_reason[row["failure_reason_raw"]].append(row)

        sample = []
        for reason, rows in by_reason.items():
            n = max(1, round(sample_size * len(rows) / total))
            chosen = random.sample(rows, min(n, len(rows)))
            sample.extend(chosen)
            log.info(f"  [{reason}]: {len(chosen)}/{len(rows)} sampled")

        # Trim or top-up to exact sample_size
        random.shuffle(sample)
        sample = sample[:sample_size]

    log.info(f"Final sample size: {len(sample)} transactions")
    return sample


# ══════════════════════════════════════════════════════════════════════════════
#  METRICS COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

def compute_metrics(
    name: str,
    results: List[TransactionResult],
    action_key: str,
    recovered_key: str,
    latency_key: str,
) -> StrategyMetrics:
    """
    Computes all core business metrics for a given strategy from the results list.
    """
    total = len(results)
    recovered = [r for r in results if getattr(r, recovered_key)]

    recovery_rate  = (len(recovered) / total * 100) if total > 0 else 0.0
    revenue        = sum(r.amount for r in recovered)
    avg_latency    = (
        sum(getattr(r, latency_key) for r in results) / total
        if total > 0 else 0.0
    )

    # Action breakdown
    action_counts: Dict[str, int] = {}
    for r in results:
        act = getattr(r, action_key)
        action_counts[act] = action_counts.get(act, 0) + 1

    return StrategyMetrics(
        name               = name,
        total_transactions = total,
        recovered_count    = len(recovered),
        recovery_rate_pct  = round(recovery_rate, 2),
        revenue_recovered  = round(revenue, 2),
        avg_latency_ms     = round(avg_latency, 2),
        action_breakdown   = action_counts,
    )


def compute_revenue_lift(ai_revenue: float, rule_revenue: float) -> float:
    """Percentage increase in revenue recovered by AI vs rule-based."""
    if rule_revenue == 0:
        return 100.0 if ai_revenue > 0 else 0.0
    return round((ai_revenue - rule_revenue) / rule_revenue * 100, 2)


def compute_recovery_lift(ai_rate: float, rule_rate: float) -> float:
    """Percentage point increase in recovery rate (AI - Rule)."""
    return round(ai_rate - rule_rate, 2)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN SIMULATION LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run_simulation(
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    agent_url:   str = DEFAULT_AGENT_URL,
    offline:     bool = False,
) -> dict:
    """
    Runs the full A/B simulation. Returns the complete metrics payload dict
    ready to be serialised into metrics_summary.json.
    """
    random.seed(RANDOM_SEED)
    log.info("=" * 60)
    log.info("  AI Revenue Recovery — Phase 5 Simulation Engine")
    log.info("=" * 60)
    log.info(f"  Sample size : {sample_size}")
    log.info(f"  Agent URL   : {'[OFFLINE MODE]' if offline else agent_url}")
    log.info("=" * 60)

    # ── 1. Data loading ───────────────────────────────────────────────────────
    transactions = load_transactions(CSV_PATH, sample_size)

    # ── 2. HTTP session for agent calls (connection pooling) ──────────────────
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})

    # ── 3. Main processing loop ───────────────────────────────────────────────
    results: List[TransactionResult] = []
    total = len(transactions)

    log.info(f"\nProcessing {total} transactions...")
    for i, tx in enumerate(transactions, 1):
        amount         = float(tx["amount"])
        failure_reason = tx["failure_reason_raw"]
        retries        = int(tx["recent_retries"])

        # Strategy A: Rule-Based Heuristic (no latency — pure computation)
        rule_action    = rule_based_decision(amount, retries)
        rule_recovered = simulate_recovery_outcome(failure_reason, rule_action)

        # Strategy B: AI Agent (or offline fallback)
        if offline:
            ai_action    = rule_based_decision(amount, retries)
            ai_summary   = "Offline mode — heuristic applied"
            ai_latency   = 0
        else:
            ai_action, ai_summary, ai_latency = ai_agent_decision(tx, agent_url, session)
            # Throttle to respect Groq's 30 RPM rate limit (2s gap = ~30 req/min)
            time.sleep(AGENT_REQUEST_DELAY_SEC)

        ai_recovered = simulate_recovery_outcome(failure_reason, ai_action)

        results.append(TransactionResult(
            transaction_id     = tx["transaction_id"],
            amount             = amount,
            failure_reason     = failure_reason,
            payment_method     = tx["payment_method"],
            rule_action        = rule_action,
            rule_recovered     = rule_recovered,
            ai_action          = ai_action,
            ai_recovered       = ai_recovered,
            ai_reasoning_trace = ai_summary,
            ai_latency_ms      = ai_latency,
        ))

        if i % 25 == 0 or i == total:
            rule_so_far = sum(1 for r in results if r.rule_recovered)
            ai_so_far   = sum(1 for r in results if r.ai_recovered)
            log.info(f"  [{i:>4}/{total}] Rule recovered: {rule_so_far} | AI recovered: {ai_so_far}")

    session.close()

    # ── 4. Metrics computation ────────────────────────────────────────────────
    rule_metrics = compute_metrics(
        name          = "Rule-Based (Heuristic Baseline)",
        results       = results,
        action_key    = "rule_action",
        recovered_key = "rule_recovered",
        latency_key   = "ai_latency_ms",   # rule has no latency; use 0 baseline
    )
    # Override rule latency to ~0ms (pure in-process computation)
    rule_metrics.avg_latency_ms = 0.5

    ai_metrics = compute_metrics(
        name          = "AI Agent (Groq + Llama-3)",
        results       = results,
        action_key    = "ai_action",
        recovered_key = "ai_recovered",
        latency_key   = "ai_latency_ms",
    )

    revenue_lift  = compute_revenue_lift(ai_metrics.revenue_recovered, rule_metrics.revenue_recovered)
    recovery_lift = compute_recovery_lift(ai_metrics.recovery_rate_pct, rule_metrics.recovery_rate_pct)

    log.info("\n" + "=" * 60)
    log.info("  BENCHMARK RESULTS")
    log.info("=" * 60)
    log.info(f"  Rule-Based  | Recovery: {rule_metrics.recovery_rate_pct}% | Revenue: ₹{rule_metrics.revenue_recovered:,.2f}")
    log.info(f"  AI Agent    | Recovery: {ai_metrics.recovery_rate_pct}% | Revenue: ₹{ai_metrics.revenue_recovered:,.2f}")
    log.info(f"  Revenue Lift (AI vs Rule): {revenue_lift:+.2f}%")
    log.info(f"  Recovery Lift (AI vs Rule): {recovery_lift:+.2f} pp")
    log.info("=" * 60)

    # ── 5. Per-failure-reason breakdown ───────────────────────────────────────
    from collections import defaultdict

    reason_breakdown: List[dict] = []
    by_reason: Dict[str, List[TransactionResult]] = defaultdict(list)
    for r in results:
        by_reason[r.failure_reason].append(r)

    for reason, txns in sorted(by_reason.items()):
        rule_rec = sum(1 for t in txns if t.rule_recovered)
        ai_rec   = sum(1 for t in txns if t.ai_recovered)
        n        = len(txns)
        reason_breakdown.append({
            "failure_reason":        reason,
            "total":                 n,
            "rule_recovered":        rule_rec,
            "ai_recovered":          ai_rec,
            "rule_recovery_rate_pct": round(rule_rec / n * 100, 2),
            "ai_recovery_rate_pct":   round(ai_rec  / n * 100, 2),
            "lift_pct":               round((ai_rec - rule_rec) / max(n, 1) * 100, 2),
        })

    # ── 6. Latest 20 transaction outcomes (for dashboard table) ───────────────
    latest_20 = [
        {
            "transaction_id":    r.transaction_id,
            "amount":            round(r.amount, 2),
            "failure_reason":    r.failure_reason,
            "payment_method":    r.payment_method,
            "rule_action":       r.rule_action,
            "rule_recovered":    r.rule_recovered,
            "ai_action":         r.ai_action,
            "ai_recovered":      r.ai_recovered,
            "ai_reasoning_trace": r.ai_reasoning_trace,
            "ai_latency_ms":     r.ai_latency_ms,
        }
        for r in results[-20:]
    ]

    # ── 7. Assemble full metrics payload ──────────────────────────────────────
    summary = {
        "meta": {
            "simulation_id":    str(uuid.uuid4()),
            "generated_at":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "sample_size":      total,
            "random_seed":      RANDOM_SEED,
            "agent_url":        agent_url if not offline else "offline",
            "schema_version":   "1.0.0",
        },

        # KPI Cards
        "kpis": {
            "rule_recovery_rate_pct":  rule_metrics.recovery_rate_pct,
            "ai_recovery_rate_pct":    ai_metrics.recovery_rate_pct,
            "recovery_rate_lift_pp":   recovery_lift,

            "rule_revenue_recovered":  rule_metrics.revenue_recovered,
            "ai_revenue_recovered":    ai_metrics.revenue_recovered,
            "revenue_lift_pct":        revenue_lift,

            "rule_avg_latency_ms":     rule_metrics.avg_latency_ms,
            "ai_avg_latency_ms":       ai_metrics.avg_latency_ms,
        },

        # Full strategy objects (for Recharts bar/line charts)
        "strategies": {
            "rule_based": {
                "name":              rule_metrics.name,
                "total_transactions": rule_metrics.total_transactions,
                "recovered_count":   rule_metrics.recovered_count,
                "recovery_rate_pct": rule_metrics.recovery_rate_pct,
                "revenue_recovered": rule_metrics.revenue_recovered,
                "avg_latency_ms":    rule_metrics.avg_latency_ms,
                "action_breakdown":  rule_metrics.action_breakdown,
            },
            "ai_agent": {
                "name":              ai_metrics.name,
                "total_transactions": ai_metrics.total_transactions,
                "recovered_count":   ai_metrics.recovered_count,
                "recovery_rate_pct": ai_metrics.recovery_rate_pct,
                "revenue_recovered": ai_metrics.revenue_recovered,
                "avg_latency_ms":    ai_metrics.avg_latency_ms,
                "action_breakdown":  ai_metrics.action_breakdown,
            },
        },

        # Per-failure-reason breakdown (for Recharts grouped bar chart)
        "failure_reason_breakdown": reason_breakdown,

        # Recovery matrix used — exposes simulation assumptions
        "recovery_probability_matrix": RECOVERY_MATRIX,

        # Latest 20 individual transaction outcomes (for dashboard table)
        "transaction_outcomes": latest_20,
    }

    return summary


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Phase 5: Recovery Simulation & Metrics Engine"
    )
    parser.add_argument(
        "--sample", type=int, default=DEFAULT_SAMPLE_SIZE,
        help=f"Number of transactions to simulate (100–500, default {DEFAULT_SAMPLE_SIZE})"
    )
    parser.add_argument(
        "--agent-url", type=str, default=DEFAULT_AGENT_URL,
        help=f"Base URL of the Groq Agent Service (default: {DEFAULT_AGENT_URL})"
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="Skip agent HTTP calls; both strategies use the rule-based heuristic"
    )
    parser.add_argument(
        "--output", type=str, default=str(OUTPUT_PATH),
        help=f"Output JSON path (default: {OUTPUT_PATH})"
    )
    args = parser.parse_args()

    # Clamp sample size
    sample_size = max(100, min(500, args.sample))

    summary = run_simulation(
        sample_size = sample_size,
        agent_url   = args.agent_url,
        offline     = args.offline,
    )

    # Write JSON output
    out_path = Path(args.output)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    log.info(f"\n✅ Metrics exported → {out_path}")
    log.info(f"   Total transactions simulated: {summary['meta']['sample_size']}")
    log.info(f"   AI Recovery Rate:   {summary['kpis']['ai_recovery_rate_pct']}%")
    log.info(f"   Rule Recovery Rate: {summary['kpis']['rule_recovery_rate_pct']}%")
    log.info(f"   Revenue Lift:       {summary['kpis']['revenue_lift_pct']:+.2f}%")
    log.info(f"\n   Load metrics_summary.json into your React/Recharts dashboard.")


if __name__ == "__main__":
    main()
