"""
agent.py — Gemini-powered AI diagnosis agent with structured reasoning output
"""

import os
import json
import logging
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

_model = None

# Hinglish recovery messages per failure type (extending for checkouts, subs, invoices)
HINGLISH_MESSAGES = {
    # Payment Failures
    "INSUFFICIENT_FUNDS": (
        "Sir, aapka payment process nahi ho saka kyunki account mein balance kam tha. "
        "Main aapko ek naya payment link bhej raha/rahi hoon — UPI ya doosra card use kar sakte hain. 🙏"
    ),
    "CARD_EXPIRED": (
        "Namaste! Aapka card expire ho gaya hai. "
        "Koi baat nahi — main abhi ek fresh payment link bhej rahi hoon, UPI se bhi kar sakte hain. ✅"
    ),
    "BANK_DOWNTIME": (
        "Aapka bank thodi der ke liye unavailable tha, isliye payment nahi hua. "
        "Hum automatically retry kar rahe hain — aapko kuch karne ki zaroorat nahi. ⏳"
    ),
    "NETWORK_TIMEOUT": (
        "Network issue ki wajah se payment timeout ho gaya. "
        "Hum turant dobara try kar rahe hain — bas ek minute ruko. 🔄"
    ),
    "FRAUD_FLAGGED": (
        "Aapka transaction security review ke liye roka gaya hai. "
        "Hamari team 24 ghante mein aapse contact karegi. Reference ID save kar lijiye. 🛡️"
    ),
    "WRONG_CVV": (
        "Card ka CVV galat tha. "
        "Main aapko ek naya payment link bhej raha/rahi hoon — sahi details se try karein. 💳"
    ),
    "LIMIT_EXCEEDED": (
        "Aaj ka card limit cross ho gaya. "
        "UPI se try karein — koi limit nahi hoti. Ya kal dobara try kar sakte hain. 📊"
    ),
    "UPI_TIMEOUT": (
        "UPI request timeout ho gayi. "
        "Main abhi fresh collect request bhej raha/rahi hoon aapke UPI app pe. 📱"
    ),
    "MANDATE_FAILED": (
        "Aapka subscription auto-debit fail ho gaya. "
        "Subscription active rakhne ke liye neeche diye link se payment complete karein. 🔔"
    ),
    "CARD_BLOCKED": (
        "Aapka card bank ne block kar diya hai. "
        "Please apne bank se contact karein ya doosra payment method use karein. ⚠️"
    ),
    # Checkout Abandonment
    "PRICE_DROP_OFF": (
        "Sir, aapne product cart mein choda hai. "
        "Kya aapko koi discount ya EMI option chahiye? Complete karne ke liye click karein. 💸"
    ),
    "FRICTION_DROP_OFF": (
        "Aapka checkout network ya verification slow hone se adhura reh gaya. "
        "Is direct link se instant pay karein bina kisi glitch ke. ⚡"
    ),
    "DISTRACTION_DROP_OFF": (
        "Aapka checkout session pending hai. "
        "Product check out karne ke liye link pe click karein. Item safe rakha hai! 🛒"
    ),
    # Subscriptions / Mandates / Invoices / Receivables
    "WILL_PAY_SOON": (
        "Ji namaste! Aapka subscription/invoice payment pending hai. "
        "Aapne jald payment karne ka kaha tha. Kripya is link se clear karein. 👍"
    ),
    "NEED_REMINDER": (
        "Aapke invoice ki payment due date nikal chuki hai. "
        "Kripya late fee se bachne ke liye link pe click karke pay karein. 🔔"
    ),
    "HIGH_RISK_DEFAULT": (
        "Aapka account multiple payment default ki wajah se manual risk review mein hai. "
        "Immediate payment karke block hatayein. 🚨"
    )
}

# Classification labels per failure type
FAILURE_CLASSIFICATIONS = {
    # Payment failures
    "BANK_DOWNTIME":      "TRANSIENT_FAILURE",
    "NETWORK_TIMEOUT":    "TRANSIENT_FAILURE",
    "UPI_TIMEOUT":        "TRANSIENT_FAILURE",
    "INSUFFICIENT_FUNDS": "SOFT_DECLINE",
    "CARD_EXPIRED":       "SOFT_DECLINE",
    "WRONG_CVV":          "SOFT_DECLINE",
    "LIMIT_EXCEEDED":     "SOFT_DECLINE",
    "MANDATE_FAILED":     "SOFT_DECLINE",
    "FRAUD_FLAGGED":      "HARD_DECLINE",
    "CARD_BLOCKED":       "HARD_DECLINE",
    # Checkout drop-offs
    "PRICE_DROP_OFF":      "PRICE_DROP_OFF",
    "FRICTION_DROP_OFF":   "FRICTION_DROP_OFF",
    "DISTRACTION_DROP_OFF":"DISTRACTION_DROP_OFF",
    # Receivables / Subscriptions
    "WILL_PAY_SOON":      "WILL_PAY_SOON",
    "NEED_REMINDER":      "NEED_REMINDER",
    "HIGH_RISK_DEFAULT":  "HIGH_RISK_DEFAULT",
}


GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def get_gemini_model():
    global _model
    if _model is None:
        try:
            import google.generativeai as genai
            if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY":
                return None
            genai.configure(api_key=GEMINI_API_KEY)
            _model = genai.GenerativeModel(GEMINI_MODEL)
        except Exception:
            return None
    return _model


def diagnose_and_recommend(txn: dict) -> dict:
    """
    Diagnose failure and return structured reasoning.
    Always includes: classification, confidence, reasoning, recommended_action,
    expected_recovery_probability, root_cause, customer_sentiment,
    message_to_customer, hinglish_message, explanation_summary, best_retry_window_minutes, source.
    """
    model = get_gemini_model()
    if model:
        result = _gemini_diagnose(model, txn)
    else:
        result = _rule_based_diagnose(txn)

    # Always inject hinglish message
    failure_type = txn.get("failure_type", "")
    result.setdefault("hinglish_message", HINGLISH_MESSAGES.get(failure_type, ""))
    result.setdefault("classification", FAILURE_CLASSIFICATIONS.get(failure_type, "UNKNOWN"))
    return result


def _gemini_diagnose(model, txn: dict) -> dict:
    """Gemini-powered structured diagnosis."""
    failure_type = txn.get("failure_type", "")
    classification = FAILURE_CLASSIFICATIONS.get(failure_type, "UNKNOWN")

    prompt = f"""You are an AI Revenue Recovery Agent for a fintech company using Razorpay in India.

Analyze this failed transaction or session and return a structured diagnosis.

TRANSACTION:
- ID: {txn['id']}
- Customer: {txn.get('customer_name', 'Unknown')}
- Amount: {txn.get('amount', 0)/100:.2f} INR
- Failure Type: {failure_type}
- Failure Reason: {txn.get('failure_reason', '')}
- Previous Attempts: {txn.get('attempts', 0)}
- Source: {txn.get('source_type', 'payment')}
- Pre-classified as: {classification}

Respond ONLY with valid JSON (no markdown):
{{
  "classification": "{classification}",
  "confidence": 0.0-1.0,
  "root_cause": "One sentence root cause",
  "customer_sentiment": "FRUSTRATED | UNAWARE | TECHNICAL_ISSUE",
  "recommended_action": "AUTO_RETRY | PAYMENT_LINK | EMI_OFFER | SEND_REMINDER | VOICE_CALL | ESCALATE",
  "reasoning": "Detailed justification of recommended action",
  "explanation_summary": "1-sentence summary of the recovery decision (e.g. Retry selected because 84% of similar bank downtime cases succeed within 2 hours)",
  "expected_recovery_probability": 0.0-1.0,
  "message_to_customer": "Empathetic English message (1-2 sentences)",
  "best_retry_window_minutes": 0-120
}}"""

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        result["source"] = "gemini"
        
        # Normalise key names for consistency
        if "expected_recovery_probability" not in result and "estimated_recovery_probability" in result:
            result["expected_recovery_probability"] = result.pop("estimated_recovery_probability")
        if "recommended_action" not in result and "recovery_action" in result:
            result["recommended_action"] = result.pop("recovery_action")
        return result
    except Exception as e:
        logging.warning(f"Gemini diagnosis failed using model {GEMINI_MODEL}: {e}")
        result = _rule_based_diagnose(txn)
        result["gemini_error"] = str(e)
        return result


def _rule_based_diagnose(txn: dict) -> dict:
    """Rule-based diagnosis — complete, structured, same schema as Gemini."""
    failure_type = txn.get("failure_type", "UNKNOWN")
    amount = txn.get("amount", 0) / 100
    customer = txn.get("customer_name", "Customer")
    classification = FAILURE_CLASSIFICATIONS.get(failure_type, "UNKNOWN")

    diagnosis_map = {
        # Payments
        "INSUFFICIENT_FUNDS": {
            "classification": "SOFT_DECLINE",
            "confidence": 0.82,
            "root_cause": "Customer's account balance is insufficient to cover the transaction amount.",
            "customer_sentiment": "FRUSTRATED",
            "recommended_action": "PAYMENT_LINK",
            "reasoning": "Offer UPI or another card via a payment link to complete checkout.",
            "explanation_summary": "Payment link sent because alternate payment methods resolve ~62% of balance issues.",
            "expected_recovery_probability": 0.62,
            "message_to_customer": f"Hi {customer}! Your payment of ₹{amount:.0f} couldn't process due to insufficient funds. You can complete it instantly using UPI or a different card.",
            "best_retry_window_minutes": 60,
        },
        "CARD_EXPIRED": {
            "classification": "SOFT_DECLINE",
            "confidence": 0.90,
            "root_cause": "Customer's card has expired.",
            "customer_sentiment": "UNAWARE",
            "recommended_action": "PAYMENT_LINK",
            "reasoning": "Request card update via new payment link.",
            "explanation_summary": "Payment link sent to prompt the customer to update their expired card details.",
            "expected_recovery_probability": 0.72,
            "message_to_customer": f"Hi {customer}! Your card seems to have expired. No worries — use this link to pay via a new card or UPI in seconds.",
            "best_retry_window_minutes": 30,
        },
        "BANK_DOWNTIME": {
            "classification": "TRANSIENT_FAILURE",
            "confidence": 0.88,
            "root_cause": "Issuing bank servers were temporarily unavailable.",
            "customer_sentiment": "UNAWARE",
            "recommended_action": "AUTO_RETRY",
            "reasoning": "Auto-retry after bank downtime window completes.",
            "explanation_summary": "Retry selected because 84% of similar bank downtime cases succeed within 2 hours.",
            "expected_recovery_probability": 0.83,
            "message_to_customer": f"Hi {customer}! Your bank was briefly unavailable. We're retrying your ₹{amount:.0f} payment automatically.",
            "best_retry_window_minutes": 120,
        },
        "NETWORK_TIMEOUT": {
            "classification": "TRANSIENT_FAILURE",
            "confidence": 0.92,
            "root_cause": "Network connection timed out.",
            "customer_sentiment": "UNAWARE",
            "recommended_action": "AUTO_RETRY",
            "reasoning": "Retry with idempotency key to prevent double charge.",
            "explanation_summary": "Retry triggered because network timeouts are transient and recover within 5 mins (~80% success).",
            "expected_recovery_probability": 0.80,
            "message_to_customer": f"Hi {customer}! A network glitch interrupted your payment. We're retrying automatically right now.",
            "best_retry_window_minutes": 5,
        },
        "FRAUD_FLAGGED": {
            "classification": "HARD_DECLINE",
            "confidence": 0.95,
            "root_cause": "Transaction blocked by fraud detection engine.",
            "customer_sentiment": "FRUSTRATED",
            "recommended_action": "ESCALATE",
            "reasoning": "Escalate to risk team. Bypassing retry to ensure compliance.",
            "explanation_summary": "Escalate triggered immediately to review fraud markers manually (recovery probability ~20%).",
            "expected_recovery_probability": 0.20,
            "message_to_customer": f"Hi {customer}! Your payment was flagged for a security review. Our risk team will contact you within 24 hours.",
            "best_retry_window_minutes": 0,
        },
        "WRONG_CVV": {
            "classification": "SOFT_DECLINE",
            "confidence": 0.85,
            "root_cause": "Incorrect CVV was entered.",
            "customer_sentiment": "TECHNICAL_ISSUE",
            "recommended_action": "PAYMENT_LINK",
            "reasoning": "Send CVV update payment link.",
            "explanation_summary": "Payment link sent to allow secure re-entry of card CVV, recovering 68% of CVV inputs.",
            "expected_recovery_probability": 0.68,
            "message_to_customer": f"Hi {customer}! There was a CVV mismatch on your card. Please use this secure link to retry with the correct details.",
            "best_retry_window_minutes": 15,
        },
        "LIMIT_EXCEEDED": {
            "classification": "SOFT_DECLINE",
            "confidence": 0.78,
            "root_cause": "Daily transaction limit exceeded.",
            "customer_sentiment": "FRUSTRATED",
            "recommended_action": "PAYMENT_LINK",
            "reasoning": "Suggest UPI or other cards to avoid limit block.",
            "explanation_summary": "Payment link sent because alternate payment methods bypass daily card limits (success ~55%).",
            "expected_recovery_probability": 0.55,
            "message_to_customer": f"Hi {customer}! Your card limit was reached for today. Switch to UPI — no daily limits — using the link below.",
            "best_retry_window_minutes": 60,
        },
        "UPI_TIMEOUT": {
            "classification": "TRANSIENT_FAILURE",
            "confidence": 0.87,
            "root_cause": "UPI collect request timed out.",
            "customer_sentiment": "TECHNICAL_ISSUE",
            "recommended_action": "AUTO_RETRY",
            "reasoning": "Re-trigger UPI request with idempotency.",
            "explanation_summary": "UPI re-request triggered because timeouts resolve upon retry within 10 minutes (78% success).",
            "expected_recovery_probability": 0.78,
            "message_to_customer": f"Hi {customer}! Your UPI payment timed out. We've sent a fresh request to your UPI app — please check and approve.",
            "best_retry_window_minutes": 10,
        },
        "MANDATE_FAILED": {
            "classification": "SOFT_DECLINE",
            "confidence": 0.80,
            "root_cause": "Auto-debit mandate failed.",
            "customer_sentiment": "UNAWARE",
            "recommended_action": "PAYMENT_LINK",
            "reasoning": "Send mandate recovery page.",
            "explanation_summary": "Payment link sent to complete payment manually and re-authorize subscription (68% success).",
            "expected_recovery_probability": 0.68,
            "message_to_customer": f"Hi {customer}! Your subscription auto-debit of ₹{amount:.0f} failed. Complete payment via this link to keep your subscription active.",
            "best_retry_window_minutes": 120,
        },
        "CARD_BLOCKED": {
            "classification": "HARD_DECLINE",
            "confidence": 0.93,
            "root_cause": "Card blocked permanently.",
            "customer_sentiment": "FRUSTRATED",
            "recommended_action": "ESCALATE",
            "reasoning": "Blocked card requires manual risk desk review.",
            "explanation_summary": "Escalate triggered immediately because blocked cards cannot be charged (15% manual success).",
            "expected_recovery_probability": 0.15,
            "message_to_customer": f"Hi {customer}! Your card appears to be blocked. Please contact your bank or use UPI/net banking to complete this payment.",
            "best_retry_window_minutes": 0,
        },
        # Checkout Abandonment
        "PRICE_DROP_OFF": {
            "classification": "PRICE_DROP_OFF",
            "confidence": 0.80,
            "root_cause": "Session abandoned due to high price or shipping costs.",
            "customer_sentiment": "FRUSTRATED",
            "recommended_action": "EMI_OFFER",
            "reasoning": "Offer low-cost EMI option to reduce upfront price friction.",
            "explanation_summary": "EMI offer sent because price drops recover ~45% of orders via flexible billing cycles.",
            "expected_recovery_probability": 0.45,
            "message_to_customer": f"Hi {customer}! Complete your checkout with our easy monthly EMI options starting today.",
            "best_retry_window_minutes": 60,
        },
        "FRICTION_DROP_OFF": {
            "classification": "FRICTION_DROP_OFF",
            "confidence": 0.82,
            "root_cause": "Checkout abandoned due to verification or load issues.",
            "customer_sentiment": "FRUSTRATED",
            "recommended_action": "PAYMENT_LINK",
            "reasoning": "Send alternate quick checkout payment link.",
            "explanation_summary": "Payment link sent to simplify the checkout path and bypass page friction (~60% recovery).",
            "expected_recovery_probability": 0.60,
            "message_to_customer": f"Hi {customer}! Your checkout timed out. Complete your order instantly using this secure 1-click link.",
            "best_retry_window_minutes": 15,
        },
        "DISTRACTION_DROP_OFF": {
            "classification": "DISTRACTION_DROP_OFF",
            "confidence": 0.75,
            "root_cause": "Customer navigated away before finishing check out.",
            "customer_sentiment": "UNAWARE",
            "recommended_action": "SEND_REMINDER",
            "reasoning": "Send cart recovery push notification or text message.",
            "explanation_summary": "Reminder sent because distraction drops recover ~50% with a quick nudge.",
            "expected_recovery_probability": 0.50,
            "message_to_customer": f"Hi {customer}! We've saved the items in your cart. Grab them now before they sell out!",
            "best_retry_window_minutes": 30,
        },
        # Receivables / Invoices / Subscriptions
        "WILL_PAY_SOON": {
            "classification": "WILL_PAY_SOON",
            "confidence": 0.85,
            "root_cause": "Customer agreed to pay soon but has not transacted.",
            "customer_sentiment": "UNAWARE",
            "recommended_action": "SEND_REMINDER",
            "reasoning": "Send scheduled B2B invoice payment alert.",
            "explanation_summary": "Reminder sent to prompt promised invoice fulfillment (recovers ~75% of claims).",
            "expected_recovery_probability": 0.75,
            "message_to_customer": f"Hi {customer}! Just a gentle reminder that your pending invoice of ₹{amount:.0f} is due.",
            "best_retry_window_minutes": 120,
        },
        "NEED_REMINDER": {
            "classification": "NEED_REMINDER",
            "confidence": 0.80,
            "root_cause": "Overdue payment requires follow-up reminder.",
            "customer_sentiment": "UNAWARE",
            "recommended_action": "SEND_REMINDER",
            "reasoning": "Send soft follow-up nudge with quick pay button.",
            "explanation_summary": "Reminder sent to prevent B2B account defaults and maintain account status (~65% recovery).",
            "expected_recovery_probability": 0.65,
            "message_to_customer": f"Hi {customer}! Your invoice of ₹{amount:.0f} is overdue. Please complete the payment using this link.",
            "best_retry_window_minutes": 60,
        },
        "HIGH_RISK_DEFAULT": {
            "classification": "HIGH_RISK_DEFAULT",
            "confidence": 0.90,
            "root_cause": "Invoice unpaid after multiple contacts.",
            "customer_sentiment": "FRUSTRATED",
            "recommended_action": "ESCALATE",
            "reasoning": "High-risk B2B account requires Collections desk manual call.",
            "explanation_summary": "Escalate triggered to move the invoice to manual Promise-To-Pay tracking (~30% recovery).",
            "expected_recovery_probability": 0.30,
            "message_to_customer": f"Hi {customer}! Your B2B account is pending manual credit review. Please contact collections immediately.",
            "best_retry_window_minutes": 0,
        }
    }

    result = diagnosis_map.get(failure_type, {
        "classification": "UNKNOWN",
        "confidence": 0.60,
        "root_cause": "Transaction failed due to unclassified error.",
        "customer_sentiment": "FRUSTRATED",
        "recommended_action": "PAYMENT_LINK",
        "reasoning": "Unknown issue. Send payment link as default recovery option.",
        "explanation_summary": "Payment link sent as standard default recovery option for unclassified errors.",
        "expected_recovery_probability": 0.50,
        "message_to_customer": f"Hi {customer}! Your transaction of ₹{amount:.0f} couldn't complete. Please retry using this link.",
        "best_retry_window_minutes": 30,
    })

    result["source"] = "rule_based"
    return result


def generate_recovery_message(txn: dict, diagnosis: dict, action_taken: str,
                               payment_link: str = None) -> str:
    """Generate the final recovery message to send to customer."""
    base_message = diagnosis.get("message_to_customer", "")
    if action_taken in ["PAYMENT_LINK", "EMI_OFFER"] and payment_link:
        return f"{base_message}\n\nComplete payment here: {payment_link}"
    elif action_taken == "AUTO_RETRY":
        return f"{base_message}\n\nWe'll notify you once it's processed successfully."
    elif action_taken == "ESCALATE":
        return f"{base_message}\n\nReference ID: {txn['id'][:12].upper()}"
    return base_message


def format_audit_event(action: str, classification: str, outcome: str, amount: int) -> str:
    """Format a human-readable audit event string for the feed."""
    amount_str = f" ₹{amount/100:,.0f}" if amount > 0 else ""
    return f"[AI] Classified as {classification} → {action} → {outcome}{amount_str}"
