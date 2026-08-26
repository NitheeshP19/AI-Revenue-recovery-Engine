"""
AI Revenue Recovery Engine — Audit Logger
Provides functions to log recovery decisions and write simulation summaries.
"""

from __future__ import annotations

import json
from pathlib import Path

# Paths relative to the root directory
BASE_DIR = Path(__file__).parent
LOG_PATH = BASE_DIR / "audit_log.jsonl"
SUMMARY_PATH = BASE_DIR / "audit_summary.json"


def log_decision(entry: dict) -> None:
    """
    Appends one JSON line to audit_log.jsonl in the root directory.
    Creates the file if it does not exist.
    """
    required_keys = [
        "timestamp",
        "transaction_id",
        "amount",
        "failure_reason",
        "root_cause_predicted",
        "root_cause_confidence",
        "customer_ltv",
        "retry_attempt",
        "action_chosen",
        "action_rationale",
        "stopping_rule_triggered",
        "outcome",
        "revenue_recovered",
    ]
    
    # Ensure entry has float types and correct keys
    validated_entry = {}
    for key in required_keys:
        if key not in entry:
            raise KeyError(f"Missing required field in audit log entry: {key}")
        validated_entry[key] = entry[key]

    # Convert types to ensure strict JSON schemas
    validated_entry["amount"] = float(validated_entry["amount"])
    validated_entry["customer_ltv"] = float(validated_entry["customer_ltv"])
    validated_entry["retry_attempt"] = int(validated_entry["retry_attempt"])
    validated_entry["revenue_recovered"] = float(validated_entry["revenue_recovered"])
    
    # Convert stopping_rule_triggered to None if empty string
    if not validated_entry["stopping_rule_triggered"]:
        validated_entry["stopping_rule_triggered"] = None

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(validated_entry, ensure_ascii=False) + "\n")


def write_summary(metrics: dict) -> None:
    """
    Writes/overwrites audit_summary.json with the final run totals.
    """
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
