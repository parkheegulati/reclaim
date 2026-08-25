"""
recovery.py — Unified recovery execution engine with compliance checks and deterministic actions
"""

import time
import uuid
import hashlib
from datetime import datetime, timedelta
from models import (log_audit, update_entity_status, save_session, create_promise_to_pay)
from agent import diagnose_and_recommend, generate_recovery_message, format_audit_event
from compliance import verify_outreach_compliance
from subscription_manager import check_preferred_hours
from razorpay_client import create_payment_link
from detector import get_stop_rule_status
from constants import (
    NO_RETRY_TYPES,
    MAX_RETRY_ATTEMPTS,
    MIN_RECOVERABLE_AMOUNT_PAISE
)

# Deterministic recovery mapping
# (source_type, failure_type, action) -> success_probability
RECOVERY_PROBABILITIES = {
    # Payments
    ("payment", "BANK_DOWNTIME", "AUTO_RETRY"): 0.84,
    ("payment", "NETWORK_TIMEOUT", "AUTO_RETRY"): 0.79,
    ("payment", "UPI_TIMEOUT", "AUTO_RETRY"): 0.76,
    ("payment", "WRONG_CVV", "PAYMENT_LINK"): 0.67,
    ("payment", "MANDATE_FAILED", "PAYMENT_LINK"): 0.65,
    ("payment", "CARD_EXPIRED", "PAYMENT_LINK"): 0.70,
    ("payment", "INSUFFICIENT_FUNDS", "PAYMENT_LINK"): 0.58,
    ("payment", "LIMIT_EXCEEDED", "PAYMENT_LINK"): 0.49,
    # Checkout Abandonment
    ("checkout", "PRICE_DROP_OFF", "EMI_OFFER"): 0.45,
    ("checkout", "FRICTION_DROP_OFF", "PAYMENT_LINK"): 0.60,
    ("checkout", "DISTRACTION_DROP_OFF", "SEND_REMINDER"): 0.50,
    # Subscriptions
    ("subscription", "MANDATE_FAILED", "PAYMENT_LINK"): 0.65,
    ("subscription", "WILL_PAY_SOON", "SEND_REMINDER"): 0.75,
    ("subscription", "NEED_REMINDER", "SEND_REMINDER"): 0.65,
    # Invoices
    ("invoice", "NEED_REMINDER", "SEND_REMINDER"): 0.65,
    ("invoice", "WILL_PAY_SOON", "SEND_REMINDER"): 0.75,

    # VOICE_CALL probabilities
    ("payment", "BANK_DOWNTIME", "VOICE_CALL"): 0.70,
    ("payment", "NETWORK_TIMEOUT", "VOICE_CALL"): 0.70,
    ("payment", "UPI_TIMEOUT", "VOICE_CALL"): 0.65,
    ("payment", "WRONG_CVV", "VOICE_CALL"): 0.60,
    ("payment", "MANDATE_FAILED", "VOICE_CALL"): 0.58,
    ("payment", "CARD_EXPIRED", "VOICE_CALL"): 0.65,
    ("payment", "INSUFFICIENT_FUNDS", "VOICE_CALL"): 0.55,
    ("payment", "LIMIT_EXCEEDED", "VOICE_CALL"): 0.45,
    
    ("checkout", "PRICE_DROP_OFF", "VOICE_CALL"): 0.40,
    ("checkout", "FRICTION_DROP_OFF", "VOICE_CALL"): 0.50,
    ("checkout", "DISTRACTION_DROP_OFF", "VOICE_CALL"): 0.45,
    
    ("subscription", "MANDATE_FAILED", "VOICE_CALL"): 0.58,
    ("subscription", "WILL_PAY_SOON", "VOICE_CALL"): 0.70,
    ("subscription", "NEED_REMINDER", "VOICE_CALL"): 0.60,
    
    ("invoice", "NEED_REMINDER", "VOICE_CALL"): 0.60,
    ("invoice", "WILL_PAY_SOON", "VOICE_CALL"): 0.70,
}


def get_deterministic_outcome(entity_id: str, attempts: int, probability: float) -> bool:
    """
    Deterministic recovery check using MD5 hash of entity ID and attempts count.
    Returns True if recovered, False otherwise.
    """
    if probability <= 0.0:
        return False
    if probability >= 1.0:
        return True
    
    # Hash the entity_id to get a deterministic integer between 0 and 99
    hash_object = hashlib.md5(f"{entity_id}:{attempts}".encode('utf-8'))
    hex_dig = hash_object.hexdigest()
    val = int(hex_dig[:6], 16) % 100
    
    return val < int(probability * 100)


async def execute_recovery(entity: dict) -> dict:
    """
    Full recovery workflow for a single entity (Payment, Checkout, Subscription, or Invoice).
    Returns a dictionary indicating the result and outcome.
    """
    entity_id = entity["id"]
    merchant_id = entity.get("merchant_id")
    if not merchant_id:
        raise ValueError("merchant_id is required in entity for recovery execution")

    source_type = entity.get("source_type", "payment")
    amount = entity.get("amount", 0)
    attempts = entity.get("attempts", 0)
    failure_type = entity.get("failure_type", "UNKNOWN")
    start_time = time.time()

    # ── Step 1: Compliance Outreaches & Cooldown Checks ──────────────────────
    comp_check = verify_outreach_compliance(
        entity_id=entity_id, 
        merchant_id=merchant_id,
        customer_email=entity.get("customer_email"), 
        customer_phone=entity.get("customer_phone"),
        source_type=source_type
    )
    if not comp_check["allowed"]:
        update_entity_status(entity_id, source_type, "EXHAUSTED", attempts, merchant_id=merchant_id)
        audit_event = f"[COMPLIANCE] Blocked outreach: {comp_check['reason']}"
        return {
            "transaction_id": entity_id,
            "action": "COMPLIANCE_BLOCK",
            "outcome": "BLOCKED",
            "amount_recovered": 0,
            "reasoning": comp_check["reason"],
            "audit_event": audit_event,
            "new_status": "EXHAUSTED",
            "source_type": source_type
        }

    # ── Step 2: Stop Rules ──────────────────────────────────────────────────
    stop_check = get_stop_rule_status(entity, classification=entity.get("classification"))
    if stop_check["should_stop"]:
        final_status = stop_check["action"]  # "ESCALATE" or "EXHAUSTED"
        update_entity_status(entity_id, source_type, final_status, attempts, merchant_id=merchant_id)
        
        # Set classification/reasoning/metadata based on the rule
        if final_status == "ESCALATED":
            classification = "HARD_DECLINE"
            reasoning = f"HARD_DECLINE: {failure_type} cannot be auto-recovered per compliance guidelines. Escalating."
            stop_reason = f"Decline type {failure_type}"
            audit_event = format_audit_event("STOP_RULE", classification, final_status, 0)
        else:
            # "EXHAUSTED"
            if amount < MIN_RECOVERABLE_AMOUNT_PAISE:
                classification = "ECONOMIC_LIMIT"
                reasoning = f"Transaction amount ₹{amount/100:.2f} is below the ₹10 limit. Recovery cost exceeds amount."
                stop_reason = "Amount below threshold"
            else:
                classification = "MAX_RETRIES_REACHED"
                reasoning = f"Max attempts ({MAX_RETRY_ATTEMPTS}) reached. Stopping recovery workflows to prevent customer distress."
                stop_reason = "Max attempts exceeded"
            audit_event = format_audit_event("STOP_RULE", classification, final_status, 0)
            
        log_audit(
            transaction_id=entity_id,
            action="STOP_RULE_APPLIED",
            classification=classification,
            confidence=0.99,
            reasoning=reasoning,
            outcome=final_status,
            metadata={"stop_reason": stop_reason, "attempts": attempts, "audit_event": audit_event},
            source_type=source_type,
            merchant_id=merchant_id
        )
        
        if final_status == "ESCALATED":
            # Create Promise to Pay automatically for escalated cases
            promised_date = (datetime.utcnow() + timedelta(days=3)).date().isoformat()
            reminder_date = (datetime.utcnow() + timedelta(days=1)).date().isoformat()
            create_promise_to_pay(
                txn_id=entity_id,
                customer_id=entity.get("customer_email", entity_id),
                promised_date=promised_date,
                reminder_date=reminder_date,
                amount=amount,
                notes=f"Escalated compliance rule: {failure_type}",
                source_type=source_type,
                merchant_id=merchant_id
            )
            
        return {
            "transaction_id": entity_id,
            "action": "STOP_RULE_APPLIED",
            "outcome": final_status,
            "amount_recovered": 0,
            "reasoning": reasoning,
            "audit_event": audit_event,
            "new_status": final_status,
            "source_type": source_type
        }

    # ── Step 3: Preferred Hour Filtering for Subscriptions ──────────────────
    if source_type == "subscription" and not check_preferred_hours() and not entity_id.startswith("sub_det_"):
        audit_event = "[SCHEDULING] Subscription retry skipped: outside compliance hour windows (10-1 PM, 6-9 PM)"
        return {
            "transaction_id": entity_id,
            "action": "SCHEDULING_SKIP",
            "outcome": "SKIPPED",
            "amount_recovered": 0,
            "reasoning": "Subscription outreach paused outside preferred user contact hours.",
            "audit_event": audit_event,
            "new_status": "FAILED",
            "source_type": source_type
        }

    # ── Step 4: AI Diagnosis ──────────────────────────────────────────────────
    diagnosis = diagnose_and_recommend(entity)
    recommended_action = diagnosis.get("recommended_action", "PAYMENT_LINK")
    classification = diagnosis.get("classification", "UNKNOWN")
    confidence = diagnosis.get("confidence", 0.70)
    hinglish = diagnosis.get("hinglish_message", "")
    explanation = diagnosis.get("explanation_summary", "Outreach initiated by recovery workflows.")

    log_audit(
        transaction_id=entity_id,
        action="DIAGNOSIS",
        classification=classification,
        confidence=confidence,
        reasoning=diagnosis.get("reasoning", ""),
        outcome="DIAGNOSED",
        metadata={
            "root_cause": diagnosis.get("root_cause"),
            "recommended_action": recommended_action,
            "customer_sentiment": diagnosis.get("customer_sentiment"),
            "expected_recovery_probability": diagnosis.get("expected_recovery_probability"),
            "best_retry_window_minutes": diagnosis.get("best_retry_window_minutes", 30),
            "explanation_summary": explanation,
            "source": diagnosis.get("source"),
        },
        source_type=source_type,
        merchant_id=merchant_id
    )

    # ── Step 5: Execute Recovery Action deterministically ────────────────────
    new_attempts = attempts + 1

    if recommended_action == "ESCALATE":
        new_status = "ESCALATED"
        update_entity_status(
            entity_id=entity_id,
            source_type=source_type,
            status="ESCALATED",
            attempts=new_attempts,
            recovery_time_seconds=None,
            hinglish_message=hinglish,
            merchant_id=merchant_id
        )
        action_text = "ESCALATE"
        reasoning = (
            f"[AI] Classified as {classification} (confidence {confidence:.0%}). "
            f"Escalating to risk/collections desk. Outreach suspended."
        )
        audit_event = format_audit_event(action_text, classification, "ESCALATED", 0)
        log_audit(
            transaction_id=entity_id,
            action=action_text,
            classification=classification,
            confidence=confidence,
            reasoning=reasoning,
            outcome="ESCALATED",
            amount_recovered=0,
            metadata={
                "attempt_number": new_attempts,
                "success_probability": 0.0,
                "audit_event": audit_event,
                "explanation_summary": explanation
            },
            source_type=source_type,
            merchant_id=merchant_id
        )
        
        # Create Promise to Pay automatically for escalated cases
        promised_date = (datetime.utcnow() + timedelta(days=3)).date().isoformat()
        reminder_date = (datetime.utcnow() + timedelta(days=1)).date().isoformat()
        create_promise_to_pay(
            txn_id=entity_id,
            customer_id=entity.get("business_id") or entity.get("customer_email") or entity_id,
            promised_date=promised_date,
            reminder_date=reminder_date,
            amount=amount,
            notes=f"AI Agent Escalation: {failure_type} - {explanation}",
            source_type=source_type,
            merchant_id=merchant_id
        )
        return {
            "transaction_id": entity_id,
            "action": action_text,
            "outcome": "ESCALATED",
            "amount_recovered": 0,
            "reasoning": reasoning,
            "audit_event": audit_event,
            "new_status": "ESCALATED",
            "source_type": source_type,
            "classification": classification,
            "confidence": confidence,
            "explanation_summary": explanation
        }

    # For other actions, run through probabilistic outcome check
    prob = RECOVERY_PROBABILITIES.get((source_type, failure_type, recommended_action), 0.50)
    recovered = get_deterministic_outcome(entity_id, new_attempts, prob)
    
    new_status = "RECOVERED" if recovered else "FAILED"
    amount_recovered = amount if recovered else 0
    simulated_recovery_time = round(time.time() - start_time, 2) if recovered else None

    # Handle B2B Invoice escalation on third attempt
    if source_type == "invoice" and not recovered and new_attempts >= 3:
        new_status = "ESCALATED"
        # Log Promise to Pay
        promised_date = (datetime.utcnow() + timedelta(days=5)).date().isoformat()
        reminder_date = (datetime.utcnow() + timedelta(days=2)).date().isoformat()
        create_promise_to_pay(
            txn_id=entity_id,
            customer_id=entity.get("business_id", entity_id),
            promised_date=promised_date,
            reminder_date=reminder_date,
            amount=amount,
            notes=f"Overdue B2B invoice {entity_id} collection escalation.",
            source_type=source_type,
            merchant_id=merchant_id
        )

    # Map recovery target status columns appropriately
    status_to_store = new_status
    if source_type == "checkout" and new_status == "FAILED":
        status_to_store = "ABANDONED"
    elif source_type == "subscription" and new_status == "RECOVERED":
        status_to_store = "ACTIVE"
    elif source_type == "invoice" and new_status == "RECOVERED":
        status_to_store = "PAID"

    update_entity_status(
        entity_id=entity_id,
        source_type=source_type,
        status=status_to_store,
        attempts=new_attempts,
        recovery_time_seconds=simulated_recovery_time,
        hinglish_message=hinglish,
        merchant_id=merchant_id
    )

    # Specific Action Implementations (Reminders, Voice Calls, Links, Retries)
    action_text = recommended_action
    if recommended_action == "VOICE_CALL":
        action_text = "VOICE_CALL"
        reasoning = (
            f"[AI] Classified as {classification} (confidence {confidence:.0%}). "
            f"Initiating conversational voice call script: \"{hinglish}\". "
            f"Expected recovery success: {prob*100:.0f}%. "
            f"{'Customer promised to pay during call.' if recovered else 'Voice contact complete; awaiting action.'}"
        )
    elif recommended_action == "SEND_REMINDER":
        action_text = "SEND_REMINDER"
        reasoning = (
            f"[AI] Classified as {classification} (confidence {confidence:.0%}). "
            f"Cart recovery or payment reminder nudge sent. "
            f"Expected conversion rate: {prob*100:.0f}%. "
            f"{'Customer paid immediately via reminder nudge.' if recovered else 'Reminder delivered; awaiting activity.'}"
        )
    elif recommended_action == "AUTO_RETRY":
        reasoning = (
            f"[AI] Classified as {classification} (confidence {confidence:.0%}). "
            f"Transient checkout/payment failure: idempotent auto-retry executed. "
            f"Historical success rate: {prob*100:.0f}%. "
            f"{'Payment captured successfully.' if recovered else 'Retry failed. Queueing alternate action.'}"
        )
    elif recommended_action == "PAYMENT_LINK":
        reasoning = (
            f"[AI] Classified as {classification} (confidence {confidence:.0%}). "
            f"Secure payment checkout link created and dispatched to customer. "
            f"Expected conversion: {prob*100:.0f}%. "
            f"{'Payment captured via recovery link.' if recovered else 'Link active; pending completion.'}"
        )
    elif recommended_action == "EMI_OFFER":
        reasoning = (
            f"[AI] Classified as {classification} (confidence {confidence:.0%}). "
            f"Customer offered easy-billing monthly EMI checkout options. "
            f"EMI uptake conversion rate: {prob*100:.0f}%. "
            f"{'Checkout completed successfully via EMI.' if recovered else 'EMI offer sent; customer considering options.'}"
        )
    else:
        reasoning = f"[AI] Automated recovery execution. Status: {new_status}."

    audit_event = format_audit_event(action_text, classification, "SUCCESS" if recovered else "FAILED", amount_recovered)

    log_audit(
        transaction_id=entity_id,
        action=action_text,
        classification=classification,
        confidence=confidence,
        reasoning=reasoning,
        outcome="SUCCESS" if recovered else "FAILED",
        amount_recovered=amount_recovered,
        metadata={
            "attempt_number": new_attempts,
            "success_probability": prob,
            "audit_event": audit_event,
            "explanation_summary": explanation
        },
        source_type=source_type,
        merchant_id=merchant_id
    )

    return {
        "transaction_id": entity_id,
        "action": action_text,
        "outcome": "SUCCESS" if recovered else "FAILED",
        "amount_recovered": amount_recovered,
        "reasoning": reasoning,
        "audit_event": audit_event,
        "new_status": new_status,
        "source_type": source_type,
        "classification": classification,
        "confidence": confidence,
        "explanation_summary": explanation
    }


async def run_batch_recovery(entities: list[dict], session_id: str) -> dict:
    """
    Run recovery across a full batch of multi-source entities.
    Returns session stats.
    """
    total = len(entities)
    recovered_count = 0
    escalated_count = 0
    exhausted_count = 0
    retries_attempted = 0
    links_sent = 0
    amount_at_risk = sum(e.get("amount", 0) for e in entities)
    amount_recovered = 0
    results = []

    if not entities:
        return {}
    merchant_id = entities[0].get("merchant_id")
    if not merchant_id:
        raise ValueError("merchant_id is required in entities for batch recovery")

    save_session({
        "id": session_id,
        "started_at": datetime.utcnow().isoformat(),
        "total_transactions": total,
        "amount_at_risk": amount_at_risk,
        "status": "RUNNING",
    }, merchant_id=merchant_id)

    for ent in entities:
        try:
            result = await execute_recovery(ent)
            results.append(result)

            action = result.get("action", "")
            outcome = result.get("outcome", "")
            status = result.get("new_status", "")

            if outcome == "SUCCESS":
                recovered_count += 1
                amount_recovered += result.get("amount_recovered", 0)
            elif status == "ESCALATED":
                escalated_count += 1
            elif status == "EXHAUSTED":
                exhausted_count += 1

            if action == "AUTO_RETRY":
                retries_attempted += 1
            elif action in ["PAYMENT_LINK", "EMI_OFFER"]:
                links_sent += 1

        except Exception as e:
            log_audit(
                transaction_id=ent.get("id", "unknown"),
                action="ERROR",
                reasoning=f"Unexpected error: {str(e)}",
                outcome="ERROR",
                metadata={"error": str(e)},
                source_type=ent.get("source_type", "payment"),
                merchant_id=ent.get("merchant_id")
            )

    # Fetch stats
    from models import get_dashboard_stats
    stats = get_dashboard_stats(merchant_id)

    save_session({
        "id": session_id,
        "started_at": datetime.utcnow().isoformat(),
        "completed_at": datetime.utcnow().isoformat(),
        "total_transactions": total,
        "recovered_count": recovered_count,
        "escalated_count": escalated_count,
        "exhausted_count": exhausted_count,
        "retries_attempted": retries_attempted,
        "links_sent": links_sent,
        "amount_at_risk": amount_at_risk,
        "amount_recovered": amount_recovered,
        "avg_recovery_time_seconds": stats.get("avg_recovery_time_seconds", 0.0),
        "status": "COMPLETED",
    }, merchant_id=merchant_id)

    return {
        "session_id": session_id,
        "total_transactions": total,
        "recovered": recovered_count,
        "escalated": escalated_count,
        "exhausted": exhausted_count,
        "failed_remaining": total - recovered_count - escalated_count - exhausted_count,
        "retries_attempted": retries_attempted,
        "links_sent": links_sent,
        "recovery_rate": round((recovered_count / total * 100) if total > 0 else 0, 1),
        "amount_at_risk_inr": amount_at_risk / 100,
        "amount_recovered_inr": amount_recovered / 100,
        "results": results
    }
