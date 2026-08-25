"""
synthetic_data.py — Generate 50+ realistic failed payment records
"""

import random
import uuid
from datetime import datetime, timedelta

FAILURE_TYPES = [
    ("INSUFFICIENT_FUNDS", "Payment declined due to insufficient funds in account", 0.65),
    ("CARD_EXPIRED", "Card has expired. Please use a different card.", 0.55),
    ("BANK_DOWNTIME", "Bank server temporarily unavailable. Please retry.", 0.85),
    ("NETWORK_TIMEOUT", "Connection timed out. Transaction not processed.", 0.80),
    ("FRAUD_FLAGGED", "Transaction blocked by fraud detection system.", 0.20),
    ("WRONG_CVV", "Incorrect CVV entered. Card declined.", 0.60),
    ("LIMIT_EXCEEDED", "Daily transaction limit exceeded on card.", 0.50),
    ("UPI_TIMEOUT", "UPI payment timed out before authorization.", 0.82),
    ("MANDATE_FAILED", "Standing mandate debit failed for subscription.", 0.70),
    ("CARD_BLOCKED", "Card has been blocked by issuing bank.", 0.15),
]

CUSTOMERS = [
    ("Rahul Sharma", "rahul.sharma@gmail.com", "+919876543210"),
    ("Priya Patel", "priya.patel@outlook.com", "+919123456789"),
    ("Amit Verma", "amit.verma@yahoo.com", "+919988776655"),
    ("Sneha Iyer", "sneha.iyer@gmail.com", "+918765432109"),
    ("Kiran Reddy", "kiran.reddy@hotmail.com", "+917654321098"),
    ("Meera Nair", "meera.nair@gmail.com", "+916543210987"),
    ("Arjun Singh", "arjun.singh@gmail.com", "+915432109876"),
    ("Deepa Krishnan", "deepa.k@gmail.com", "+914321098765"),
    ("Vikram Joshi", "vikram.j@outlook.com", "+913210987654"),
    ("Ananya Gupta", "ananya.g@gmail.com", "+912109876543"),
    ("Rohit Malhotra", "rohit.m@gmail.com", "+919871234567"),
    ("Pooja Desai", "pooja.d@gmail.com", "+918901234567"),
    ("Sanjay Kumar", "sanjay.k@company.com", "+917891234567"),
    ("Lakshmi Rao", "lakshmi.r@gmail.com", "+916781234567"),
    ("Nikhil Bansal", "nikhil.b@startup.in", "+915671234567"),
    ("Divya Menon", "divya.m@gmail.com", "+914561234567"),
    ("Suresh Pillai", "suresh.p@business.com", "+913451234567"),
    ("Kavya Sharma", "kavya.s@gmail.com", "+912341234567"),
    ("Arun Nambiar", "arun.n@gmail.com", "+919012345678"),
    ("Ritu Agarwal", "ritu.a@gmail.com", "+918012345678"),
]

PRODUCTS = [
    ("Annual SaaS Subscription", 299900),
    ("Monthly Premium Plan", 49900),
    ("E-commerce Order #", 85000),
    ("Flight Booking", 450000),
    ("Hotel Reservation", 220000),
    ("Insurance Premium", 180000),
    ("Online Course", 12999),
    ("Grocery Order", 3500),
    ("Electronics Purchase", 599900),
    ("Mutual Fund SIP", 500000),
    ("Utility Bill Payment", 250000),
    ("EMI Installment", 150000),
    ("Movie Tickets", 6000),
    ("Food Delivery", 4500),
    ("Cab Booking", 1500),
]


def generate_synthetic_transactions(count: int = 55) -> list[dict]:
    transactions = []
    base_time = datetime.utcnow() - timedelta(hours=48)

    for i in range(count):
        customer = random.choice(CUSTOMERS)
        product_name, base_amount = random.choice(PRODUCTS)
        amount = base_amount + random.randint(-1000, 5000)
        amount = max(100, amount)  # min 1 rupee

        failure_type, failure_reason, recovery_prob = random.choice(FAILURE_TYPES)
        created_at = base_time + timedelta(minutes=random.randint(0, 2880))

        txn = {
            "id": f"pay_{uuid.uuid4().hex[:16]}",
            "order_id": f"order_{uuid.uuid4().hex[:12]}",
            "customer_name": customer[0],
            "customer_email": customer[1],
            "customer_phone": customer[2],
            "amount": amount,
            "currency": "INR",
            "failure_type": failure_type,
            "failure_reason": failure_reason,
            "risk_score": round(random.uniform(0.3, 0.99), 2),
            "recovery_probability": recovery_prob,
            "status": "FAILED",
            "attempts": 0,
            "product_description": product_name,
            "created_at": created_at.isoformat(),
            "updated_at": created_at.isoformat(),
        }
        transactions.append(txn)

    # Sort by risk_score descending (highest priority first)
    transactions.sort(key=lambda x: x["risk_score"], reverse=True)
    return transactions


def get_failure_recovery_strategy(failure_type: str) -> dict:
    """Returns the recommended recovery strategy for each failure type."""
    strategies = {
        "INSUFFICIENT_FUNDS": {
            "primary_action": "PAYMENT_LINK",
            "message": "Offer partial payment or EMI option",
            "retry_after_hours": 24,
            "max_attempts": 2,
        },
        "CARD_EXPIRED": {
            "primary_action": "PAYMENT_LINK",
            "message": "Request card update via new payment link",
            "retry_after_hours": 1,
            "max_attempts": 3,
        },
        "BANK_DOWNTIME": {
            "primary_action": "AUTO_RETRY",
            "message": "Auto-retry after bank downtime window",
            "retry_after_hours": 2,
            "max_attempts": 3,
        },
        "NETWORK_TIMEOUT": {
            "primary_action": "AUTO_RETRY",
            "message": "Retry with idempotency key",
            "retry_after_hours": 0.25,
            "max_attempts": 3,
        },
        "FRAUD_FLAGGED": {
            "primary_action": "ESCALATE",
            "message": "Escalate to risk team for manual review",
            "retry_after_hours": 0,
            "max_attempts": 0,
        },
        "WRONG_CVV": {
            "primary_action": "PAYMENT_LINK",
            "message": "Send new payment link to customer",
            "retry_after_hours": 1,
            "max_attempts": 2,
        },
        "LIMIT_EXCEEDED": {
            "primary_action": "PAYMENT_LINK",
            "message": "Suggest alternate payment method or split payment",
            "retry_after_hours": 12,
            "max_attempts": 2,
        },
        "UPI_TIMEOUT": {
            "primary_action": "AUTO_RETRY",
            "message": "Retry UPI collect request",
            "retry_after_hours": 0.5,
            "max_attempts": 3,
        },
        "MANDATE_FAILED": {
            "primary_action": "PAYMENT_LINK",
            "message": "Send mandate re-registration link",
            "retry_after_hours": 2,
            "max_attempts": 2,
        },
        "CARD_BLOCKED": {
            "primary_action": "ESCALATE",
            "message": "Escalate — card blocked, customer needs to contact bank",
            "retry_after_hours": 0,
            "max_attempts": 0,
        },
    }
    return strategies.get(failure_type, {
        "primary_action": "PAYMENT_LINK",
        "message": "Send payment link",
        "retry_after_hours": 2,
        "max_attempts": 2,
    })
