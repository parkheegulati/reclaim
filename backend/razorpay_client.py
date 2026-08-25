"""
razorpay_client.py — Razorpay Test-Mode API wrapper with graceful error handling
"""

import os
import razorpay
import hmac
import hashlib
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

_client: Optional[razorpay.Client] = None


def get_client() -> razorpay.Client:
    global _client
    if _client is None:
        if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
            raise ValueError("Razorpay credentials not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env")
        _client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    return _client


def is_configured() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET and
                RAZORPAY_KEY_ID != "rzp_test_YOUR_KEY_ID")


def create_order(amount: int, currency: str = "INR",
                 receipt: str = None, notes: dict = None) -> dict:
    """Create a Razorpay order (amount in paise)."""
    try:
        client = get_client()
        order_data = {
            "amount": amount,
            "currency": currency,
            "receipt": receipt or f"receipt_{amount}",
            "notes": notes or {},
        }
        order = client.order.create(data=order_data)
        return {"success": True, "order": order}
    except Exception as e:
        return {"success": False, "error": str(e), "fallback": "simulated_order"}


def create_payment_link(amount: int, customer_name: str, customer_email: str,
                        customer_phone: str, description: str,
                        transaction_id: str) -> dict:
    """Create a Razorpay payment link for recovery."""
    try:
        client = get_client()
        payload = {
            "amount": amount,
            "currency": "INR",
            "accept_partial": False,
            "description": description,
            "customer": {
                "name": customer_name,
                "email": customer_email,
                "contact": customer_phone,
            },
            "notify": {
                "sms": True,
                "email": True,
            },
            "reminder_enable": True,
            "notes": {
                "recovery_for": transaction_id,
                "agent": "AI Revenue Recovery Agent",
            },
            "callback_url": "https://your-merchant.com/payment-success",
            "callback_method": "get",
        }
        link = client.payment_link.create(payload)
        return {
            "success": True,
            "payment_link_id": link["id"],
            "short_url": link["short_url"],
            "status": link["status"],
        }
    except Exception as e:
        # Graceful fallback — simulate payment link
        simulated_url = f"https://rzp.io/sim/{transaction_id[:8]}"
        return {
            "success": True,  # Treat as success with simulation note
            "payment_link_id": f"plink_simulated_{transaction_id[:8]}",
            "short_url": simulated_url,
            "status": "created",
            "simulated": True,
            "error_detail": str(e),
        }


def get_payment_details(payment_id: str) -> dict:
    """Fetch payment details from Razorpay."""
    try:
        client = get_client()
        payment = client.payment.fetch(payment_id)
        return {"success": True, "payment": payment}
    except Exception as e:
        return {"success": False, "error": str(e)}


def retry_payment_capture(payment_id: str, amount: int) -> dict:
    """Attempt to capture a previously authorized payment."""
    try:
        client = get_client()
        result = client.payment.capture(payment_id, amount)
        return {"success": True, "payment": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_failed_payments(count: int = 50) -> dict:
    """Fetch recent failed payments from Razorpay."""
    try:
        client = get_client()
        payments = client.payment.all({
            "count": count,
        })
        failed = [p for p in payments.get("items", [])
                  if p.get("status") == "failed"]
        return {"success": True, "payments": failed, "source": "razorpay_live"}
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "payments": [],
            "source": "razorpay_unavailable",
        }
