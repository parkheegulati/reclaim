"""
subscription_manager.py — Subscription recovery and mandate failure sequencer
"""

from datetime import datetime, timedelta
from models import get_db, upsert_subscription, log_audit


def check_preferred_hours(dt: datetime = None) -> bool:
    """
    Checks if the given time falls within the compliance-friendly preferred outreach windows:
    - 10:00 AM to 1:00 PM (10:00 - 13:00)
    - 6:00 PM to 9:00 PM (18:00 - 21:00)
    """
    if dt is None:
        dt = datetime.now()  # Use local time for user communication window
    hour = dt.hour
    return (10 <= hour < 13) or (18 <= hour < 21)


def get_subscription_retry_delay(retry_count: int) -> int:
    """
    Determine the next retry day sequence:
    - Retry 1: Day 1 (24 hours later)
    - Retry 2: Day 3 (72 hours from start, or 48 hours after Retry 1)
    - Retry 3: Day 5 (120 hours from start, or 48 hours after Retry 2)
    """
    if retry_count == 0:
        return 24  # 1 day in hours
    elif retry_count == 1:
        return 48  # 2 days in hours (Day 3 from start)
    elif retry_count == 2:
        return 48  # 2 days in hours (Day 5 from start)
    return 24


def process_subscription_retries() -> int:
    """
    Scans the subscriptions table and queues/processes due retries.
    Returns the count of processed subscription retries.
    """
    conn = get_db()
    c = conn.cursor()
    now_str = datetime.utcnow().isoformat()
    
    # Query subscriptions that are active/failed and due for a retry
    c.execute("""
        SELECT * FROM subscriptions 
        WHERE status = 'FAILED' AND next_retry_at <= ? AND retry_count < 3
    """, (now_str,))
    
    due_subs = [dict(r) for r in c.fetchall()]
    conn.close()
    
    processed = 0
    for sub in due_subs:
        # Check compliance for preferred outreach time
        if not check_preferred_hours():
            # If not in preferred window, do not execute contact right now
            continue
            
        # Update retry count and compute next schedule
        sub["retry_count"] += 1
        hours_delay = get_subscription_retry_delay(sub["retry_count"])
        sub["next_retry_at"] = (datetime.utcnow() + timedelta(hours=hours_delay)).isoformat()
        
        # Enforce max retry rule
        if sub["retry_count"] >= 3:
            sub["status"] = "EXHAUSTED"
        
        upsert_subscription(sub)
        processed += 1
        
        # Log to audit trail
        log_audit(
            transaction_id=sub["id"],
            action="AUTO_RETRY",
            reasoning=f"Subscription retry #{sub['retry_count']} executed within preferred hour window.",
            outcome="RETRY_SCHEDULED" if sub["status"] == "FAILED" else "EXHAUSTED",
            metadata={
                "retry_count": sub["retry_count"],
                "next_retry_at": sub["next_retry_at"]
            },
            source_type="subscription"
        )
        
    return processed


def get_subscription_failure_strategy(failure_type: str) -> dict:
    """
    Returns strategy mapping for subscription failures.
    """
    strategies = {
        "MANDATE_FAILED": {
            "primary_action": "PAYMENT_LINK",
            "message": "Standing mandate failed; request manual card payment/re-registration",
            "max_attempts": 3,
        },
        "WILL_PAY_SOON": {
            "primary_action": "SEND_REMINDER",
            "message": "Send gentle mandate payment reminder",
            "max_attempts": 3,
        },
        "NEED_REMINDER": {
            "primary_action": "SEND_REMINDER",
            "message": "Send mandate renewal alert",
            "max_attempts": 3,
        },
        "HIGH_RISK_DEFAULT": {
            "primary_action": "ESCALATE",
            "message": "Multiple mandate failures; escalate to B2B collections desk",
            "max_attempts": 1,
        }
    }
    return strategies.get(failure_type, {
        "primary_action": "PAYMENT_LINK",
        "message": "Subscription recovery workflow",
        "max_attempts": 2
    })
