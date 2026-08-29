# Benchmark Methodology & Experimental Integrity

## 1. Overview

The AI Revenue Recovery Engine includes an A/B simulation engine (`simulation_engine.py`) to benchmark intelligent AI-driven payment recovery against a standardized rule-based heuristic.

This document outlines the benchmark architecture, dataset origin, heuristic baseline specifications, and evaluation constraints.

---

## 2. Dataset Disclosure: Synthetic vs. Real Payment Data

### Why Synthetic Data?
**Real payment transaction datasets with granular failure codes and customer profiles are legally and regulatorily restricted under PCI-DSS, RBI payment data localization mandates, and GDPR.** Payment aggregators (Razorpay, Stripe, Adyen) do not publish raw failed payment logs containing PII, card brand identifiers, customer lifetime value (LTV), or retry counts.

### Data Generation Methodology
The dataset in `data/failed_transactions.csv` (5,000 records) is produced by `synthetic_data_generator.py` using calibrated real-world distributions:

- **Failure Reasons**: Derived from published gateway error benchmarks:
  - `gateway_timeout`: 35% (transient network / bank server drops)
  - `insufficient_funds`: 25% (customer balance issue)
  - `incorrect_pin`: 15% (customer auth error)
  - `risk_flag`: 15% (fraud scoring rules / velocity triggers)
  - `expired_card`: 10% (credential expiration)
- **Payment Instruments**: UPI (55%), Credit Card (25%), Debit Card (20%) mirroring Indian digital payment ecosystem statistics.
- **Customer Lifetime Value (LTV)**: Log-normal distribution ($μ=₹3,500$, range $₹200$–$₹50,000$).

---

## 3. Heuristic Baseline (Strategy A) Specification

To ensure a fair and rigorous comparison (avoiding a "strawman" baseline), Strategy A implements a multi-variable heuristic reflecting production practices from Stripe Smart Retries and Razorpay retry guidance:

| Priority | Condition | Action | Rationale |
|---|---|---|---|
| **1** | `recent_retries >= 3` | `give_up` | Strict retry exhaustion to protect merchant gateway health score. |
| **2** | `failure_reason == "expired_card"` | `switch_method` | Retrying an expired card always fails; must change instrument. |
| **3** | `failure_reason == "incorrect_pin"` | `switch_method` | Customer authentication error; switching avoids repeated credential lockout. |
| **4** | `failure_reason == "gateway_timeout"` | `retry_now` | Transient infrastructure failure; immediate retry has high success probability. |
| **5** | `failure_reason == "insufficient_funds"` | `retry_later` | Customer requires account top-up or salary cycle delay. |
| **6** | `failure_reason == "risk_flag"` | `retry_later` | Cooling period allows automated fraud review triggers to reset. |
| **7** | `amount < ₹100` | `retry_now` | Low transaction risk; rapid retry is economically optimal. |
| **8** | Default / Unknown | `retry_later` | Conservative delayed retry. |

---

## 4. Recovery Probability Matrix (Stochastic Engine)

Because simulation operates offline or on sandbox events without charging real customer bank accounts, recovery outcomes are computed via a stochastic matrix ($P(\text{recovery} \mid \text{failure\_reason}, \text{action})$) calibrated against published fintech benchmark recovery rates:

```json
{
  "gateway_timeout":    { "retry_now": 0.85, "retry_later": 0.72, "switch_method": 0.55, "give_up": 0.00 },
  "expired_card":       { "retry_now": 0.00, "retry_later": 0.00, "switch_method": 0.80, "give_up": 0.00 },
  "insufficient_funds": { "retry_now": 0.05, "retry_later": 0.65, "switch_method": 0.50, "give_up": 0.00 },
  "incorrect_pin":      { "retry_now": 0.10, "retry_later": 0.10, "switch_method": 0.70, "give_up": 0.00 },
  "risk_flag":          { "retry_now": 0.12, "retry_later": 0.45, "switch_method": 0.35, "give_up": 0.00 }
}
```

---

## 5. Fallback Accounting & Transparency

When calling the Groq LLM Agent (`POST /agent/decide`):
- If the agent endpoint is unavailable, experiences network timeout, or hits API rate limits, the system falls back to the deterministic heuristic.
- **Reporting Rule**: Transactions that trigger the fallback are tagged with `fallback_status: "agent_unavailable_fallback"`, tracked in a separate `fallback_bucket` in `metrics_summary.json`, and **excluded from the headline AI decision rate** to prevent distortion.

---

## 6. How to Reproduce

```bash
# 1. Regenerate synthetic transactions (optional)
python synthetic_data_generator.py

# 2. Run simulation with live Groq AI Agent:
cd ml && python agent_service.py &
python simulation_engine.py --sample 100

# 3. Or run purely offline (zero network dependency):
python simulation_engine.py --offline --sample 100
```
