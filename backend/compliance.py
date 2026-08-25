"""
compliance.py — Outreach compliance guardrails layer
"""

import json
import logging
from datetime import datetime, timedelta
from models import get_db, log_audit
from constants import COOLDOWN_MINUTES, MAX_OUTREACH_ATTEMPTS

# Mock blocklist for DNC (Do Not Contact) customers/businesses
DO_NOT_CONTACT_LIST = {
    "dnc@startup.in",
    "dnc@company.com",
    "+910000000000",
    "business_dnc",
    "user_dnc_99"
}


def is_dnc(customer_email: str = None, customer_phone: str = None, customer_id: str = None) -> bool:
    """
    Checks if the customer identifier matches the Do Not Contact (DNC) list.
    """
    if customer_email and customer_email.lower() in DO_NOT_CONTACT_LIST:
        return True
    if customer_phone and customer_phone in DO_NOT_CONTACT_LIST:
        return True
    if customer_id and customer_id in DO_NOT_CONTACT_LIST:
        return True
    return False


def verify_outreach_compliance(entity_id: str, merchant_id: str, customer_email: str = None, 
                               customer_phone: str = None, source_type: str = "payment") -> dict:
    """
    Validates if an outreach attempt to a customer is compliant.
    Checks:
    1. Do Not Contact (DNC) flag.
    2. Maximum outreach limit (3).
    3. Minimum cooldown window (30 minutes) between any contact actions.
    
    Returns dict {"allowed": bool, "reason": str or None}.
    """
    if not merchant_id:
        raise ValueError("merchant_id is required for compliance check")

    # 1. Check DNC List (Global)
    if is_dnc(customer_email, customer_phone, entity_id):
        reason = "COMPLIANCE_VIOLATION: Customer is on the Do Not Contact (DNC) list."
        log_audit(
            transaction_id=entity_id,
            action="COMPLIANCE_BLOCK",
            reasoning=reason,
            outcome="BLOCKED",
            source_type=source_type,
            classification="HARD_DECLINE",
            confidence=1.0,
            merchant_id=merchant_id
        )
        return {"allowed": False, "reason": reason}

    # Query audit trail for past outreach actions for this entity under this merchant
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM audit_trail 
        WHERE merchant_id = ? AND transaction_id = ? AND action IN (
            'AUTO_RETRY', 'PAYMENT_LINK', 'SEND_REMINDER', 
            'VOICE_CALL', 'EMI_OFFER', 'STOP_RULE_APPLIED'
        )
        ORDER BY timestamp DESC
    """, (merchant_id, entity_id))
    
    logs = [dict(r) for r in c.fetchall()]
    conn.close()

    # 2. Check total outreach count
    # Exclude STOP_RULE_APPLIED and COMPLIANCE_BLOCK when counting actual contact attempts
    outreach_attempts = [l for l in logs if l["action"] in [
        'AUTO_RETRY', 'PAYMENT_LINK', 'SEND_REMINDER', 'VOICE_CALL', 'EMI_OFFER'
    ]]
    
    if len(outreach_attempts) >= MAX_OUTREACH_ATTEMPTS:
        reason = f"COMPLIANCE_VIOLATION: Max outreach attempts ({MAX_OUTREACH_ATTEMPTS}) reached for this entity."
        log_audit(
            transaction_id=entity_id,
            action="COMPLIANCE_BLOCK",
            reasoning=reason,
            outcome="BLOCKED",
            source_type=source_type,
            classification="HARD_DECLINE",
            confidence=1.0,
            merchant_id=merchant_id
        )
        return {"allowed": False, "reason": reason}

    # 3. Check cooldown window gap (30 minutes)
    if outreach_attempts:
        last_outreach = outreach_attempts[0]
        last_time_str = last_outreach["timestamp"]
        try:
            # Parse ISO string
            last_time = datetime.fromisoformat(last_time_str.split(".")[0])
            time_diff = datetime.utcnow() - last_time
            if time_diff < timedelta(minutes=COOLDOWN_MINUTES):
                minutes_left = COOLDOWN_MINUTES - (time_diff.total_seconds() / 60)
                reason = f"COMPLIANCE_VIOLATION: Minimum cooldown ({COOLDOWN_MINUTES} mins) not met. {int(minutes_left)} mins remaining."
                log_audit(
                    transaction_id=entity_id,
                    action="COMPLIANCE_BLOCK",
                    reasoning=reason,
                    outcome="BLOCKED",
                    source_type=source_type,
                    classification="COOLDOWN_BLOCK",
                    confidence=1.0,
                    merchant_id=merchant_id
                )
                return {"allowed": False, "reason": reason}
        except Exception as e:
            logging.warning(
                f"Compliance cooldown check failed to parse timestamp '{last_time_str}' "
                f"for entity {entity_id}. Error: {e}. Failing closed."
            )
            reason = "COMPLIANCE_VIOLATION: Timestamp parsing failed during cooldown check."
            log_audit(
                transaction_id=entity_id,
                action="COMPLIANCE_BLOCK",
                reasoning=reason,
                outcome="BLOCKED",
                source_type=source_type,
                classification="COOLDOWN_BLOCK",
                confidence=1.0,
                merchant_id=merchant_id
            )
            return {"allowed": False, "reason": reason}

    return {"allowed": True, "reason": None}
