"""
receivables.py — B2B Receivables and Invoices tracking module
"""

from datetime import datetime, timedelta
from models import get_db, upsert_invoice, create_promise_to_pay, log_audit


def detect_overdue_invoices() -> int:
    """
    Finds all invoices where status is 'UNPAID' and due_date has passed,
    marking them as 'OVERDUE'.
    Returns the count of invoices marked overdue.
    """
    conn = get_db()
    c = conn.cursor()
    today_str = datetime.utcnow().date().isoformat()
    
    c.execute("""
        SELECT * FROM invoices 
        WHERE status = 'UNPAID' AND due_date < ?
    """, (today_str,))
    
    overdue_rows = [dict(r) for r in c.fetchall()]
    conn.close()
    
    for invoice in overdue_rows:
        invoice["status"] = "OVERDUE"
        invoice["updated_at"] = datetime.utcnow().isoformat()
        
        # Enforce failure type if missing
        if not invoice.get("failure_type"):
            invoice["failure_type"] = "NEED_REMINDER"
            invoice["failure_reason"] = "Invoice payment due date has passed without capture"
            
        upsert_invoice(invoice)
        
        # Log to audit trail
        log_audit(
            transaction_id=invoice["id"],
            action="DETECTED",
            reasoning=f"Invoice {invoice['id']} for business {invoice['business_id']} marked as OVERDUE.",
            outcome="CLASSIFIED",
            metadata={
                "due_date": invoice["due_date"],
                "amount": invoice["amount"]
            },
            source_type="invoice"
        )
        
    return len(overdue_rows)


def process_invoice_followups(cooldown_hours: int = 48) -> int:
    """
    Checks overdue invoices and handles follow-up reminders if the cooldown period of 48 hours has passed.
    Also handles escalation to the Promise-to-Pay tracking ledger if attempts reach 3.
    """
    conn = get_db()
    c = conn.cursor()
    threshold = (datetime.utcnow() - timedelta(hours=cooldown_hours)).isoformat()
    
    # Query overdue invoices that need contact (last_contacted_at is null or older than threshold)
    c.execute("""
        SELECT * FROM invoices 
        WHERE status = 'OVERDUE' AND (last_contacted_at IS NULL OR last_contacted_at < ?)
    """, (threshold,))
    
    due_invoices = [dict(r) for r in c.fetchall()]
    conn.close()
    
    processed = 0
    for inv in due_invoices:
        inv["attempts"] += 1
        inv["last_contacted_at"] = datetime.utcnow().isoformat()
        inv["updated_at"] = datetime.utcnow().isoformat()
        
        if inv["attempts"] >= 3:
            # Escalate B2B Invoice to manual promise to pay
            inv["status"] = "ESCALATED"
            upsert_invoice(inv)
            
            # Create a promise to pay record automatically
            promised_date = (datetime.utcnow() + timedelta(days=5)).date().isoformat()
            reminder_date = (datetime.utcnow() + timedelta(days=2)).date().isoformat()
            create_promise_to_pay(
                txn_id=inv["id"],
                customer_id=inv["business_id"],
                promised_date=promised_date,
                reminder_date=reminder_date,
                amount=inv["amount"],
                notes=f"Auto-escalation of overdue B2B invoice {inv['id']} after 3 followups.",
                source_type="invoice"
            )
            
            log_audit(
                transaction_id=inv["id"],
                action="ESCALATED",
                reasoning=f"B2B Invoice {inv['id']} escalated to Collections. Promise-To-Pay follow-up scheduled for {reminder_date}.",
                outcome="ESCALATED",
                metadata={
                    "attempts": inv["attempts"],
                    "business_id": inv["business_id"]
                },
                source_type="invoice"
            )
        else:
            upsert_invoice(inv)
            log_audit(
                transaction_id=inv["id"],
                action="SEND_REMINDER",
                reasoning=f"Overdue invoice reminder #{inv['attempts']} sent to B2B business {inv['business_id']}.",
                outcome="REMINDER_SENT",
                metadata={
                    "attempts": inv["attempts"],
                    "business_id": inv["business_id"]
                },
                source_type="invoice"
            )
            
        processed += 1
        
    return processed


def get_invoice_failure_strategy(failure_type: str) -> dict:
    """
    Returns the recovery action strategy for overdue invoices.
    """
    strategies = {
        "NEED_REMINDER": {
            "primary_action": "SEND_REMINDER",
            "message": "Send invoice reminder with payment link",
            "max_attempts": 3,
        },
        "WILL_PAY_SOON": {
            "primary_action": "SEND_REMINDER",
            "message": "Customer promised soon; send soft reminder alert",
            "max_attempts": 3,
        },
        "HIGH_RISK_DEFAULT": {
            "primary_action": "ESCALATE",
            "message": "High-risk B2B account; escalate to Collections desk immediately",
            "max_attempts": 1,
        }
    }
    return strategies.get(failure_type, {
        "primary_action": "SEND_REMINDER",
        "message": "B2B Invoice follow-up workflow",
        "max_attempts": 3
    })
