"""
demo_runner.py — Deterministic demo dataset generator and recovery simulation runner
"""

import os
import sys
from datetime import datetime, timedelta
import asyncio

# Ensure parent directory is in sys.path if run directly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import (get_db, init_db, upsert_transaction, 
                    upsert_checkout_session, upsert_subscription, upsert_invoice,
                    create_merchant)
from recovery import run_batch_recovery, execute_recovery
from auth import hash_password

# Fixed inputs to guarantee deterministic output every single run
CUSTOMERS = [
    {"name": "Rahul Sharma", "email": "rahul.sharma@gmail.com", "phone": "+919876543210"},
    {"name": "Priya Patel", "email": "priya.patel@outlook.com", "phone": "+919123456789"},
    {"name": "Amit Verma", "email": "amit.verma@yahoo.com", "phone": "+919988776655"},
    {"name": "Sneha Iyer", "email": "sneha.iyer@gmail.com", "phone": "+918765432109"},
    {"name": "Kiran Reddy", "email": "kiran.reddy@hotmail.com", "phone": "+917654321098"},
    {"name": "Meera Nair", "email": "meera.nair@gmail.com", "phone": "+916543210987"},
    {"name": "Arjun Singh", "email": "arjun.singh@gmail.com", "phone": "+915432109876"},
    {"name": "Deepa Krishnan", "email": "deepa.k@gmail.com", "phone": "+914321098765"},
    {"name": "Vikram Joshi", "email": "vikram.j@outlook.com", "phone": "+913210987654"},
    {"name": "Ananya Gupta", "email": "ananya.g@gmail.com", "phone": "+912109876543"},
]

PRODUCTS = [
    {"name": "Annual SaaS Subscription", "amount": 299900},
    {"name": "Monthly Premium Plan", "amount": 49900},
    {"name": "E-commerce Order #91", "amount": 85000},
    {"name": "Flight Booking", "amount": 450000},
    {"name": "Hotel Reservation", "amount": 220000},
    {"name": "Insurance Premium", "amount": 180000},
]


def clear_database():
    """Wipes the database tables completely to start with a fresh deterministic run."""
    conn = get_db()
    c = conn.cursor()
    tables = [
        "merchants", "transactions", "checkout_sessions", "subscriptions", 
        "invoices", "audit_trail", "recovery_sessions", "promise_to_pay"
    ]
    for table in tables:
        try:
            c.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def generate_deterministic_dataset_for_merchant(merchant_id: str, suffix: str, num_payments: int, num_checkouts: int, num_subs: int, num_invs: int) -> list[dict]:
    """Generates a partitioned synthetic recovery target dataset for a single merchant."""
    entities = []
    base_time = datetime(2026, 8, 24, 12, 0, 0)
    
    # ── 1. Generate Failed Payments ────────────────────────────────────────
    payment_types = [
        "BANK_DOWNTIME", "NETWORK_TIMEOUT", "UPI_TIMEOUT", 
        "INSUFFICIENT_FUNDS", "CARD_EXPIRED", "WRONG_CVV", 
        "LIMIT_EXCEEDED", "MANDATE_FAILED", "FRAUD_FLAGGED", "CARD_BLOCKED"
    ]
    
    for i in range(num_payments):
        cust = CUSTOMERS[i % len(CUSTOMERS)]
        prod = PRODUCTS[i % len(PRODUCTS)]
        fail_type = payment_types[i % len(payment_types)]
        
        # Risk score calculation
        risk_score = round(0.3 + (i % 7) * 0.1, 2)
        if risk_score > 0.99:
            risk_score = 0.95
            
        txn = {
            "id": f"pay_{suffix}_{i:04d}",
            "merchant_id": merchant_id,
            "order_id": f"order_{suffix}_{i:04d}",
            "customer_name": cust["name"],
            "customer_email": cust["email"],
            "customer_phone": cust["phone"],
            "amount": 500 if (i == 5 and suffix == "det1") else prod["amount"] + (i * 100),
            "currency": "INR",
            "failure_type": fail_type,
            "failure_reason": f"Payment failure code: {fail_type}",
            "risk_score": risk_score,
            "status": "FAILED",
            "attempts": 0,
            "product_description": prod["name"],
            "created_at": (base_time - timedelta(hours=i)).isoformat(),
            "source_type": "payment"
        }
        upsert_transaction(txn, merchant_id=merchant_id)
        entities.append(txn)

    # ── 2. Generate Abandoned Checkouts ─────────────────────────────────────
    checkout_types = ["PRICE_DROP_OFF", "FRICTION_DROP_OFF", "DISTRACTION_DROP_OFF"]
    for i in range(num_checkouts):
        cust = CUSTOMERS[i % len(CUSTOMERS)]
        prod = PRODUCTS[i % len(PRODUCTS)]
        fail_type = checkout_types[i % len(checkout_types)]
        
        risk_score = round(0.4 + (i % 5) * 0.12, 2)
        
        session = {
            "id": f"chk_{suffix}_{i:04d}",
            "merchant_id": merchant_id,
            "user_id": f"user_{suffix}_{i:04d}",
            "customer_name": cust["name"],
            "customer_email": cust["email"],
            "customer_phone": cust["phone"],
            "amount": prod["amount"] - (i * 50),
            "status": "ABANDONED",
            "created_at": (base_time - timedelta(hours=i)).isoformat(),
            "last_activity_at": (base_time - timedelta(minutes=15 + i)).isoformat(),
            "attempts": 0,
            "failure_type": fail_type,
            "failure_reason": f"Checkout abandoned category: {fail_type}",
            "risk_score": risk_score,
            "source_type": "checkout"
        }
        upsert_checkout_session(session, merchant_id=merchant_id)
        entities.append(session)

    # ── 3. Generate Subscription Failures ──────────────────────────────────
    sub_types = ["MANDATE_FAILED", "WILL_PAY_SOON", "NEED_REMINDER", "HIGH_RISK_DEFAULT"]
    for i in range(num_subs):
        cust = CUSTOMERS[i % len(CUSTOMERS)]
        prod = PRODUCTS[i % len(PRODUCTS)]
        fail_type = sub_types[i % len(sub_types)]
        
        risk_score = round(0.35 + (i % 6) * 0.1, 2)
        
        sub = {
            "id": f"sub_{suffix}_{i:04d}",
            "merchant_id": merchant_id,
            "user_id": f"user_{suffix}_{i:04d}",
            "customer_name": cust["name"],
            "customer_email": cust["email"],
            "customer_phone": cust["phone"],
            "amount": prod["amount"],
            "billing_cycle": "MONTHLY" if i % 2 == 0 else "ANNUAL",
            "retry_count": 0,
            "next_retry_at": (base_time + timedelta(hours=2)).isoformat(),
            "status": "FAILED",
            "created_at": (base_time - timedelta(days=2)).isoformat(),
            "failure_type": fail_type,
            "failure_reason": f"Mandate debit processing failure: {fail_type}",
            "risk_score": risk_score,
            "source_type": "subscription"
        }
        upsert_subscription(sub, merchant_id=merchant_id)
        entities.append(sub)

    # ── 4. Generate Overdue Invoices ────────────────────────────────────────
    invoice_types = ["NEED_REMINDER", "WILL_PAY_SOON", "HIGH_RISK_DEFAULT"]
    for i in range(num_invs):
        cust = CUSTOMERS[i % len(CUSTOMERS)]
        prod = PRODUCTS[i % len(PRODUCTS)]
        fail_type = invoice_types[i % len(invoice_types)]
        
        risk_score = round(0.45 + (i % 5) * 0.1, 2)
        
        inv = {
            "id": f"inv_{suffix}_{i:04d}",
            "merchant_id": merchant_id,
            "business_id": f"biz_{suffix}_{i:04d}",
            "customer_name": f"{cust['name']} Co.",
            "customer_email": cust["email"],
            "customer_phone": cust["phone"],
            "amount": prod["amount"] * 2,
            "due_date": (base_time - timedelta(days=3 + i)).date().isoformat(),
            "status": "UNPAID",
            "last_contacted_at": None,
            "attempts": 0,
            "created_at": (base_time - timedelta(days=10)).isoformat(),
            "failure_type": fail_type,
            "failure_reason": f"Invoice due date passed; issue: {fail_type}",
            "risk_score": risk_score,
            "source_type": "invoice"
        }
        upsert_invoice(inv, merchant_id=merchant_id)
        entities.append(inv)

    return entities


def generate_deterministic_dataset() -> list[dict]:
    """
    Clears DB, seeds 2 distinct demo merchants, and builds partitioned synthetic sets:
    - Merchant 1 (Bharat Retail Co.) gets 90 items.
    - Merchant 2 (Second Merchant Pvt Ltd) gets 35 items.
    """
    init_db()
    clear_database()
    
    # Seed merchants
    create_merchant(
        merchant_id="merchant_demo_1",
        name="Bharat Retail Co.",
        email="demo1@reclaim.test",
        password_hash=hash_password("password123")
    )
    create_merchant(
        merchant_id="merchant_demo_2",
        name="Second Merchant Pvt Ltd",
        email="demo2@reclaim.test",
        password_hash=hash_password("password123")
    )

    print("\n" + "*" * 60)
    print("DEMO CREDENTIALS SEEDED:")
    print(" - Merchant 1 (Bharat Retail Co.)      : demo1@reclaim.test / password123")
    print(" - Merchant 2 (Second Merchant Pvt Ltd): demo2@reclaim.test / password123")
    print("*" * 60 + "\n")

    # Generate isolated data sets
    entities1 = generate_deterministic_dataset_for_merchant("merchant_demo_1", "det1", 50, 20, 10, 10)
    entities2 = generate_deterministic_dataset_for_merchant("merchant_demo_2", "det2", 20, 5, 5, 5)
    
    return entities1 + entities2


async def run_simulation() -> dict:
    """
    Builds the dataset and executes recovery in isolation for both demo merchants.
    Returns metrics and logs outcomes.
    """
    print("Generating deterministic demo dataset (Bharat Retail + Second Merchant)...")
    entities = generate_deterministic_dataset()
    
    ent1 = [e for e in entities if e["merchant_id"] == "merchant_demo_1"]
    ent2 = [e for e in entities if e["merchant_id"] == "merchant_demo_2"]
    
    print(f"Executing batch recovery engine for Merchant 1 ({len(ent1)} targets)...")
    summary1 = await run_batch_recovery(ent1, "session_demo_simulation_m1")
    
    print(f"Executing batch recovery engine for Merchant 2 ({len(ent2)} targets)...")
    summary2 = await run_batch_recovery(ent2, "session_demo_simulation_m2")
    
    # Calculate source breakdowns for print output (using M1 as print reference)
    from models import get_dashboard_stats
    stats = get_dashboard_stats("merchant_demo_1")
    
    print("\n" + "=" * 45)
    print("      reclaim. RECOVERY SIMULATION SUMMARY (Merchant 1)")
    print("=" * 45)
    print(f"Total revenue at risk: ₹{stats['amount_at_risk']/100:,.2f}")
    print(f"Total revenue recovered: ₹{stats['amount_recovered']/100:,.2f}")
    print(f"Overall recovery rate: {stats['recovery_rate']}%")
    print(f"Actionable amount: ₹{stats['actionable']/len(ent1)*stats['amount_at_risk']/100:,.2f}")
    print("-" * 45)
    
    print("Source Breakdown:")
    for src, metrics in stats["source_breakdown"].items():
        print(f" - {src.capitalize():12} : {metrics['recovered_count']}/{metrics['count']} recovered | "
              f"₹{metrics['recovered']/100:,.2f} of ₹{metrics['at_risk']/100:,.2f}")
              
    print("=" * 45)
    
    return summary1


if __name__ == "__main__":
    # Run the simulation directly
    asyncio.run(run_simulation())
