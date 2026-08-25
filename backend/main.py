"""
main.py — FastAPI application — all API endpoints for reclaim. platform with Auth Gate & Multi-tenant Scoping.
"""

import uuid
import os
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# Import Database operations
from models import (
    init_db, get_unified_entities, get_audit_trail,
    get_dashboard_stats, get_sessions, upsert_transaction,
    get_promises, create_promise_to_pay, create_merchant,
    get_merchant_by_email, get_merchant_by_id
)
from detector import load_and_classify_transactions
from recovery import run_batch_recovery, execute_recovery
from razorpay_client import is_configured
from demo_runner import run_simulation

# Import Auth dependencies
from auth import (
    hash_password, verify_password, create_access_token,
    get_current_merchant
)

init_db()

app = FastAPI(
    title="reclaim. — AI Revenue Recovery Platform",
    description="Detects revenue at risk across payments, checkouts, subscriptions, and invoices. Diagnoses with AI and executes compliance outreach.",
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

_active_sessions: dict = {}
_loaded_entities: dict = {}  # Scoped by merchant_id


# ── Auth Pydantic Schemas ───────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str


# ── Public Routes ─────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    landing_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(landing_path):
        return FileResponse(landing_path)
    return {"message": "reclaim. — AI Revenue Recovery Platform", "docs": "/docs"}


@app.get("/dashboard")
async def dashboard():
    dash_path = os.path.join(frontend_dir, "dashboard.html")
    if os.path.exists(dash_path):
        return FileResponse(dash_path)
    return {"message": "Dashboard not found"}


@app.get("/presentation")
async def presentation():
    pres_path = os.path.join(frontend_dir, "presentation.html")
    if os.path.exists(pres_path):
        return FileResponse(pres_path)
    return {"message": "Presentation not found"}


# ── Auth Endpoints ────────────────────────────────────────────────────────────

@app.post("/api/auth/register", response_model=AuthResponse)
async def register(req: RegisterRequest):
    # Check if merchant exists
    existing = get_merchant_by_email(req.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    merchant_id = f"merchant_{uuid.uuid4().hex[:12]}"
    password_hash = hash_password(req.password)
    
    create_merchant(
        merchant_id=merchant_id,
        name=req.name,
        email=req.email,
        password_hash=password_hash
    )
    
    token = create_access_token(merchant_id=merchant_id, email=req.email)
    return {"access_token": token, "token_type": "bearer"}


@app.post("/api/auth/login", response_model=AuthResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    merchant = get_merchant_by_email(form_data.username)
    if not merchant or not verify_password(form_data.password, merchant["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = create_access_token(merchant_id=merchant["id"], email=merchant["email"])
    return {"access_token": token, "token_type": "bearer"}


@app.get("/api/auth/me")
async def get_me(current_merchant: dict = Depends(get_current_merchant)):
    merchant = get_merchant_by_id(current_merchant["merchant_id"])
    if not merchant:
        return {
            "id": "merchant_demo_1",
            "name": "Bharat Retail Co.",
            "email": "demo1@reclaim.test"
        }
    return {
        "id": merchant["id"],
        "name": merchant["name"],
        "email": merchant["email"]
    }


# ── Scoped Protected Data Routes ──────────────────────────────────────────────

@app.get("/api/health")
async def health(current_merchant: dict = Depends(get_current_merchant)):
    return {
        "status": "healthy",
        "razorpay_configured": is_configured(),
        "version": "4.0.0",
    }


@app.get("/api/stats")
async def get_stats(current_merchant: dict = Depends(get_current_merchant)):
    """Dashboard statistics — scoped by current merchant."""
    merchant_id = current_merchant["merchant_id"]
    stats = get_dashboard_stats(merchant_id)
    # proven money recovered: without agent = 0, with agent = amount_recovered
    stats["amount_without_agent"] = 0
    stats["uplift_inr"] = stats["amount_recovered"] / 100
    return stats


@app.get("/api/transactions")
async def list_transactions(
    status: Optional[str] = None,
    failure_type: Optional[str] = None,
    limit: int = 300,
    current_merchant: dict = Depends(get_current_merchant)
):
    """List unified transactions with filters, scoped by current merchant."""
    merchant_id = current_merchant["merchant_id"]
    entities = get_unified_entities(merchant_id, status=status, failure_type=failure_type, limit=limit)
    return {"transactions": entities}


@app.get("/api/transactions/{txn_id}/audit")
async def get_transaction_audit(
    txn_id: str,
    current_merchant: dict = Depends(get_current_merchant)
):
    """Get single transaction audit trail, scoped by merchant."""
    merchant_id = current_merchant["merchant_id"]
    trail = get_audit_trail(merchant_id, transaction_id=txn_id)
    if not trail:
        raise HTTPException(status_code=404, detail="Transaction audit trail not found or access denied")
    return {"transaction_id": txn_id, "audit_trail": trail}


@app.get("/api/audit")
async def get_full_audit(
    limit: int = 500,
    current_merchant: dict = Depends(get_current_merchant)
):
    """Full audit trail, scoped by current merchant."""
    merchant_id = current_merchant["merchant_id"]
    return {"audit_trail": get_audit_trail(merchant_id, limit=limit)}


@app.get("/api/sessions")
async def list_sessions(current_merchant: dict = Depends(get_current_merchant)):
    """List sessions, scoped by current merchant."""
    merchant_id = current_merchant["merchant_id"]
    return {"sessions": get_sessions(merchant_id)}


@app.post("/api/load-transactions")
async def load_transactions_endpoint(current_merchant: dict = Depends(get_current_merchant)):
    """
    Load failed entities, scoped by current merchant.
    """
    merchant_id = current_merchant["merchant_id"]
    loaded = load_and_classify_transactions(merchant_id)
    _loaded_entities[merchant_id] = loaded
    total = len(loaded)

    return {
        "message": f"Loaded {total} failed payments",
        "total": total,
        "source": "synthetic",
        "transactions_preview": loaded[:3],
    }


@app.post("/api/run-recovery")
async def run_recovery_batch(current_merchant: dict = Depends(get_current_merchant)):
    """Run recovery batch across loaded payment entities, scoped by merchant."""
    merchant_id = current_merchant["merchant_id"]
    loaded = _loaded_entities.get(merchant_id, [])

    if not loaded:
        loaded = load_and_classify_transactions(merchant_id)

    session_id = f"session_{uuid.uuid4().hex[:12]}"
    _active_sessions[session_id] = {"status": "RUNNING"}

    result = await run_batch_recovery(loaded, session_id)
    _active_sessions[session_id] = {"status": "COMPLETED", "result": result}
    _loaded_entities[merchant_id] = []

    return result


@app.post("/api/recover/{txn_id}")
async def recover_single(
    txn_id: str,
    current_merchant: dict = Depends(get_current_merchant)
):
    """Recover a single entity by ID, scoped by merchant."""
    merchant_id = current_merchant["merchant_id"]
    entities = get_unified_entities(merchant_id, limit=500)
    entity = next((e for e in entities if e["id"] == txn_id), None)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found or access denied")
    return await execute_recovery(entity)


# ── Promise-to-Pay ────────────────────────────────────────────────────────────

class PromiseToPayRequest(BaseModel):
    transaction_id: str
    customer_id: str
    promised_date: str
    reminder_date: str
    amount: int
    notes: Optional[str] = None
    source_type: Optional[str] = "payment"


@app.post("/api/promise-to-pay")
async def create_p2p(
    req: PromiseToPayRequest,
    current_merchant: dict = Depends(get_current_merchant)
):
    """Create a promise to pay commitment, scoped by merchant."""
    merchant_id = current_merchant["merchant_id"]
    row_id = create_promise_to_pay(
        txn_id=req.transaction_id,
        customer_id=req.customer_id,
        promised_date=req.promised_date,
        reminder_date=req.reminder_date,
        amount=req.amount,
        notes=req.notes,
        source_type=req.source_type,
        merchant_id=merchant_id
    )
    return {"id": row_id, "status": "created"}


@app.get("/api/promise-to-pay")
async def list_promises(
    status: Optional[str] = None,
    current_merchant: dict = Depends(get_current_merchant)
):
    """List promises, scoped by merchant."""
    merchant_id = current_merchant["merchant_id"]
    return {"promises": get_promises(merchant_id, status=status)}


# ── Compliance Summary ────────────────────────────────────────────────────────

@app.get("/api/compliance")
async def compliance_summary(current_merchant: dict = Depends(get_current_merchant)):
    """Return compliance status logs, scoped by merchant."""
    merchant_id = current_merchant["merchant_id"]
    trail = get_audit_trail(merchant_id, limit=500)
    compliance_blocks = [e for e in trail if e["action"] == "COMPLIANCE_BLOCK"]
    stop_rules = [e for e in trail if e["action"] == "STOP_RULE_APPLIED"]
    escalations = [e for e in trail if e["action"] == "ESCALATED"]
    
    # Simple count breakdowns
    dnc_count = len([e for e in compliance_blocks if "DNC" in e.get("reasoning", "")])
    cooldown_count = len([e for e in compliance_blocks if e.get("classification") == "COOLDOWN_BLOCK"])

    return {
        "stop_rules_applied": len(stop_rules),
        "escalations": len(escalations),
        "dnc_blocked_count": dnc_count,
        "cooldown_blocked_count": cooldown_count,
        "compliance_events": compliance_blocks + stop_rules
    }


# ── Demo Runner Route (Simulation) ───────────────────────────────────────────

@app.post("/api/demo/run")
async def trigger_demo_runner(current_merchant: dict = Depends(get_current_merchant)):
    """Runs a complete simulation over both demo accounts. Access restricted to authenticated merchants."""
    result = await run_simulation()
    return result
