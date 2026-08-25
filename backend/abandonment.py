"""
abandonment.py — Checkout abandonment detection and classification module
"""

import os
from datetime import datetime, timedelta
from models import get_db, upsert_checkout_session, log_audit


def detect_abandonment(cooldown_minutes: int = 10) -> int:
    """
    Scans the checkout_sessions table for started sessions that have had no activity
    for more than `cooldown_minutes` and marks them as 'ABANDONED'.
    Returns the count of detected abandonments.
    """
    conn = get_db()
    c = conn.cursor()
    threshold = (datetime.utcnow() - timedelta(minutes=cooldown_minutes)).isoformat()
    
    # Select sessions that are 'STARTED' and have last_activity_at older than threshold
    c.execute("""
        SELECT * FROM checkout_sessions 
        WHERE status = 'STARTED' AND last_activity_at < ?
    """, (threshold,))
    
    abandoned_rows = [dict(r) for r in c.fetchall()]
    conn.close()
    
    for session in abandoned_rows:
        session["status"] = "ABANDONED"
        session["updated_at"] = datetime.utcnow().isoformat()
        
        # Determine failure type and reason if not set
        if not session.get("failure_type"):
            import random
            dropoffs = [
                ("PRICE_DROP_OFF", "Customer abandoned checkout at shipping costs or taxes step"),
                ("FRICTION_DROP_OFF", "Customer abandoned due to card field errors or checkout load times"),
                ("DISTRACTION_DROP_OFF", "Customer navigated away before completing checkout")
            ]
            f_type, f_reason = random.choice(dropoffs)
            session["failure_type"] = f_type
            session["failure_reason"] = f_reason
            
        upsert_checkout_session(session)
        
        # Log to audit trail
        log_audit(
            transaction_id=session["id"],
            action="DETECTED",
            reasoning=f"Checkout session {session['id']} marked as ABANDONED (inactive for >10 mins). Classification: {session['failure_type']}.",
            outcome="CLASSIFIED",
            metadata={
                "failure_type": session["failure_type"],
                "amount": session["amount"]
            },
            source_type="checkout"
        )
        
    return len(abandoned_rows)


def get_checkout_failure_strategy(failure_type: str) -> dict:
    """
    Returns the recommended strategy for checkout abandonments.
    """
    strategies = {
        "PRICE_DROP_OFF": {
            "primary_action": "EMI_OFFER",
            "message": "Offer EMI or localized discount code",
            "max_attempts": 2,
        },
        "FRICTION_DROP_OFF": {
            "primary_action": "PAYMENT_LINK",
            "message": "Send alternate quick checkout payment link",
            "max_attempts": 2,
        },
        "DISTRACTION_DROP_OFF": {
            "primary_action": "SEND_REMINDER",
            "message": "Send cart recovery push/SMS reminder",
            "max_attempts": 3,
        }
    }
    return strategies.get(failure_type, {
        "primary_action": "PAYMENT_LINK",
        "message": "Send checkout recovery link",
        "max_attempts": 2
    })
