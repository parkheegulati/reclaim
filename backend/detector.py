"""
detector.py — Failure detection and classification engine
"""

import random
from datetime import datetime
from models import log_audit, upsert_transaction
from synthetic_data import generate_synthetic_transactions, get_failure_recovery_strategy
from constants import (
    NO_RETRY_TYPES,
    MAX_RETRY_ATTEMPTS,
    MIN_RECOVERABLE_AMOUNT_PAISE
)


def classify_failure(failure_type: str) -> dict:
    """
    Classify the failure and determine recovery strategy.
    Returns structured classification with recommended action.
    """
    strategy = get_failure_recovery_strategy(failure_type)

    category_map = {
        "INSUFFICIENT_FUNDS": "SOFT_DECLINE",
        "CARD_EXPIRED": "SOFT_DECLINE",
        "BANK_DOWNTIME": "TRANSIENT",
        "NETWORK_TIMEOUT": "TRANSIENT",
        "FRAUD_FLAGGED": "HARD_DECLINE",
        "WRONG_CVV": "SOFT_DECLINE",
        "LIMIT_EXCEEDED": "SOFT_DECLINE",
        "UPI_TIMEOUT": "TRANSIENT",
        "MANDATE_FAILED": "SOFT_DECLINE",
        "CARD_BLOCKED": "HARD_DECLINE",
    }

    urgency_map = {
        "INSUFFICIENT_FUNDS": "MEDIUM",
        "CARD_EXPIRED": "LOW",
        "BANK_DOWNTIME": "HIGH",
        "NETWORK_TIMEOUT": "HIGH",
        "FRAUD_FLAGGED": "CRITICAL",
        "WRONG_CVV": "MEDIUM",
        "LIMIT_EXCEEDED": "MEDIUM",
        "UPI_TIMEOUT": "HIGH",
        "MANDATE_FAILED": "MEDIUM",
        "CARD_BLOCKED": "CRITICAL",
    }

    return {
        "failure_category": category_map.get(failure_type, "UNKNOWN"),
        "urgency": urgency_map.get(failure_type, "MEDIUM"),
        "recommended_action": strategy["primary_action"],
        "max_attempts": strategy["max_attempts"],
        "retry_after_hours": strategy["retry_after_hours"],
        "message_template": strategy["message"],
        "is_recoverable": failure_type not in NO_RETRY_TYPES,
    }


def compute_risk_score(txn: dict) -> float:
    """
    Compute a risk score (0-1) representing urgency/value of recovery.
    Higher = more urgent to recover.
    """
    amount_factor = min(txn["amount"] / 100000, 1.0)  # Cap at 1000 INR
    
    failure_weights = {
        "BANK_DOWNTIME": 0.9,
        "NETWORK_TIMEOUT": 0.85,
        "UPI_TIMEOUT": 0.80,
        "MANDATE_FAILED": 0.75,
        "WRONG_CVV": 0.65,
        "INSUFFICIENT_FUNDS": 0.60,
        "LIMIT_EXCEEDED": 0.55,
        "CARD_EXPIRED": 0.50,
        "FRAUD_FLAGGED": 0.10,
        "CARD_BLOCKED": 0.10,
    }
    failure_weight = failure_weights.get(txn.get("failure_type", ""), 0.5)

    # Combine factors
    score = (amount_factor * 0.4 + failure_weight * 0.6)
    return round(min(score, 1.0), 3)


def load_and_classify_transactions(merchant_id: str) -> list[dict]:
    """
    Load synthetic transactions, classify each failure, and store in DB under merchant_id.
    Returns list of classified transactions ready for recovery.
    """
    if not merchant_id:
        raise ValueError("merchant_id is required to load and classify transactions")

    raw_txns = generate_synthetic_transactions(55)
    classified = []

    for txn in raw_txns:
        classification = classify_failure(txn["failure_type"])
        txn["classification"] = classification
        txn["risk_score"] = compute_risk_score(txn)
        txn["merchant_id"] = merchant_id

        # Persist to DB
        upsert_transaction(txn, merchant_id=merchant_id)

        # Log detection event to audit trail
        log_audit(
            transaction_id=txn["id"],
            action="DETECTED",
            reasoning=(
                f"Payment of ₹{txn['amount']/100:.2f} failed. "
                f"Failure type: {txn['failure_type']} ({classification['failure_category']}). "
                f"Urgency: {classification['urgency']}. "
                f"Recommended action: {classification['recommended_action']}."
            ),
            outcome="CLASSIFIED",
            metadata={
                "failure_type": txn["failure_type"],
                "failure_category": classification["failure_category"],
                "urgency": classification["urgency"],
                "risk_score": txn["risk_score"],
                "is_recoverable": classification["is_recoverable"],
            },
            merchant_id=merchant_id
        )

        classified.append(txn)

    # Sort by risk score descending
    classified.sort(key=lambda x: x["risk_score"], reverse=True)
    return classified


def get_stop_rule_status(txn: dict, classification: dict = None) -> dict:
    """
    Check if stop rules apply to this transaction.
    Returns dict with should_stop and reason.
    """
    if classification is None:
        classification = txn.get("classification") or classify_failure(txn["failure_type"])
    attempts = txn.get("attempts", 0)
    max_attempts = classification.get("max_attempts", MAX_RETRY_ATTEMPTS)

    if txn["failure_type"] in NO_RETRY_TYPES:
        return {
            "should_stop": True,
            "reason": f"HARD_DECLINE: {txn['failure_type']} cannot be auto-recovered. Escalating to risk team.",
            "action": "ESCALATE"
        }

    if attempts >= max_attempts:
        return {
            "should_stop": True,
            "reason": f"Max retry attempts ({max_attempts}) exhausted. Stopping to avoid customer friction.",
            "action": "EXHAUSTED"
        }

    # Amount too small to recover (< ₹10)
    if txn.get("amount", 0) < MIN_RECOVERABLE_AMOUNT_PAISE:
        return {
            "should_stop": True,
            "reason": "Transaction amount below ₹10 threshold. Recovery cost exceeds amount.",
            "action": "EXHAUSTED"
        }

    return {"should_stop": False, "reason": None, "action": None}
