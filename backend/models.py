"""
models.py — SQLite database models, audit trail, promise-to-pay, checkouts, subscriptions, invoices, and merchants.
All queries are scoped by merchant_id to enforce multi-tenant isolation.
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "recovery_agent.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    # Schema check: drop legacy schema if merchants table is missing to trigger recreation
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='merchants'")
            has_merchants = c.fetchone()
            conn.close()
            if not has_merchants:
                os.remove(DB_PATH)
                print("Removed legacy database to apply merchant-isolation schema.")
        except Exception:
            pass

    conn = get_db()
    c = conn.cursor()

    # 0. Merchants table
    c.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # 1. Transactions (Payment Failures) table
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            order_id TEXT,
            customer_name TEXT,
            customer_email TEXT,
            customer_phone TEXT,
            amount INTEGER,
            currency TEXT DEFAULT 'INR',
            failure_type TEXT,
            failure_reason TEXT,
            risk_score REAL,
            status TEXT DEFAULT 'FAILED',
            attempts INTEGER DEFAULT 0,
            product_description TEXT,
            hinglish_message TEXT,
            recovery_time_seconds INTEGER,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (merchant_id) REFERENCES merchants(id)
        )
    """)

    # 2. Checkout Sessions table
    c.execute("""
        CREATE TABLE IF NOT EXISTS checkout_sessions (
            id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            user_id TEXT,
            customer_name TEXT,
            customer_email TEXT,
            customer_phone TEXT,
            amount INTEGER,
            status TEXT DEFAULT 'ABANDONED', -- ABANDONED, RECOVERED, FAILED, EXHAUSTED
            created_at TEXT,
            last_activity_at TEXT,
            attempts INTEGER DEFAULT 0,
            failure_type TEXT, -- PRICE_DROP_OFF, FRICTION_DROP_OFF, DISTRACTION_DROP_OFF
            failure_reason TEXT,
            risk_score REAL DEFAULT 0.0,
            hinglish_message TEXT,
            recovery_time_seconds INTEGER,
            updated_at TEXT,
            FOREIGN KEY (merchant_id) REFERENCES merchants(id)
        )
    """)

    # 3. Subscriptions table
    c.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            user_id TEXT,
            customer_name TEXT,
            customer_email TEXT,
            customer_phone TEXT,
            amount INTEGER,
            billing_cycle TEXT DEFAULT 'MONTHLY',
            retry_count INTEGER DEFAULT 0,
            next_retry_at TEXT,
            status TEXT DEFAULT 'FAILED', -- FAILED, ACTIVE, RECOVERED, EXHAUSTED, ESCALATED
            created_at TEXT,
            failure_type TEXT, -- MANDATE_FAILED, WILL_PAY_SOON, NEED_REMINDER, HIGH_RISK_DEFAULT
            failure_reason TEXT,
            risk_score REAL DEFAULT 0.0,
            hinglish_message TEXT,
            recovery_time_seconds INTEGER,
            updated_at TEXT,
            FOREIGN KEY (merchant_id) REFERENCES merchants(id)
        )
    """)

    # 4. Invoices table
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            business_id TEXT,
            customer_name TEXT,
            customer_email TEXT,
            customer_phone TEXT,
            amount INTEGER,
            due_date TEXT,
            status TEXT DEFAULT 'UNPAID', -- UNPAID, PAID, OVERDUE, RECOVERED, ESCALATED, EXHAUSTED
            last_contacted_at TEXT,
            attempts INTEGER DEFAULT 0,
            created_at TEXT,
            failure_type TEXT, -- NEED_REMINDER, WILL_PAY_SOON, HIGH_RISK_DEFAULT
            failure_reason TEXT,
            risk_score REAL DEFAULT 0.0,
            hinglish_message TEXT,
            recovery_time_seconds INTEGER,
            updated_at TEXT,
            FOREIGN KEY (merchant_id) REFERENCES merchants(id)
        )
    """)

    # 5. Audit trail table — enriched
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_id TEXT NOT NULL,
            transaction_id TEXT, -- Acts as generic entity_id
            action TEXT,
            classification TEXT,
            confidence REAL,
            reasoning TEXT,
            outcome TEXT,
            amount_recovered INTEGER DEFAULT 0,
            metadata TEXT,
            timestamp TEXT,
            source_type TEXT DEFAULT 'payment', -- payment, checkout, subscription, invoice
            FOREIGN KEY (merchant_id) REFERENCES merchants(id),
            FOREIGN KEY (transaction_id) REFERENCES transactions(id)
        )
    """)

    # Run alter table queries (kept for schema resilience, optional in new DB)
    alter_queries = [
        ("transactions", "product_description", "TEXT"),
        ("transactions", "hinglish_message", "TEXT"),
        ("transactions", "recovery_time_seconds", "INTEGER"),
        ("audit_trail", "classification", "TEXT"),
        ("audit_trail", "confidence", "REAL"),
        ("audit_trail", "source_type", "TEXT DEFAULT 'payment'"),
    ]
    for table, col, defn in alter_queries:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
        except Exception:
            pass

    # 6. Recovery sessions
    c.execute("""
        CREATE TABLE IF NOT EXISTS recovery_sessions (
            id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            total_transactions INTEGER DEFAULT 0,
            recovered_count INTEGER DEFAULT 0,
            escalated_count INTEGER DEFAULT 0,
            exhausted_count INTEGER DEFAULT 0,
            retries_attempted INTEGER DEFAULT 0,
            links_sent INTEGER DEFAULT 0,
            amount_at_risk INTEGER DEFAULT 0,
            amount_recovered INTEGER DEFAULT 0,
            avg_recovery_time_seconds REAL,
            status TEXT DEFAULT 'RUNNING',
            FOREIGN KEY (merchant_id) REFERENCES merchants(id)
        )
    """)

    # 7. Promise-to-pay tracking
    c.execute("""
        CREATE TABLE IF NOT EXISTS promise_to_pay (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_id TEXT NOT NULL,
            transaction_id TEXT, -- Entity ID
            customer_id TEXT,
            promised_date TEXT,
            reminder_date TEXT,
            amount INTEGER,
            status TEXT DEFAULT 'PENDING',
            follow_up_count INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            source_type TEXT DEFAULT 'payment',
            FOREIGN KEY (merchant_id) REFERENCES merchants(id)
        )
    """)

    # Indexes for merchant isolation lookup
    c.execute("CREATE INDEX IF NOT EXISTS idx_transactions_merchant ON transactions(merchant_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_checkout_sessions_merchant ON checkout_sessions(merchant_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_merchant ON subscriptions(merchant_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_invoices_merchant ON invoices(merchant_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_audit_trail_merchant ON audit_trail(merchant_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_recovery_sessions_merchant ON recovery_sessions(merchant_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_promise_to_pay_merchant ON promise_to_pay(merchant_id)")

    conn.commit()
    conn.close()


# Merchant helpers
def create_merchant(merchant_id: str, name: str, email: str, password_hash: str) -> str:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO merchants (id, name, email, password_hash, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (merchant_id, name, email.lower(), password_hash, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return merchant_id


def get_merchant_by_email(email: str) -> Optional[dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM merchants WHERE email = ?", (email.lower(),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_merchant_by_id(merchant_id: str) -> Optional[dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# Ingest helper functions
def log_audit(transaction_id: str, action: str, reasoning: str,
              outcome: str, amount_recovered: int = 0, metadata: dict = None,
              classification: str = None, confidence: float = None, source_type: str = "payment",
              merchant_id: str = None):
    m_id = merchant_id or (metadata.get("merchant_id") if metadata else None)
    if not m_id:
        raise ValueError("merchant_id is required to log audit trail")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO audit_trail
        (transaction_id, merchant_id, action, classification, confidence, reasoning,
         outcome, amount_recovered, metadata, timestamp, source_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        transaction_id, m_id, action, classification, confidence, reasoning, outcome,
        amount_recovered, json.dumps(metadata or {}),
        datetime.utcnow().isoformat(), source_type
    ))
    conn.commit()
    conn.close()


def get_audit_trail(merchant_id: str, transaction_id: str = None, limit: int = 500):
    conn = get_db()
    c = conn.cursor()
    if transaction_id:
        c.execute("SELECT * FROM audit_trail WHERE merchant_id = ? AND transaction_id = ? ORDER BY timestamp",
                  (merchant_id, transaction_id))
    else:
        c.execute("SELECT * FROM audit_trail WHERE merchant_id = ? ORDER BY timestamp DESC LIMIT ?", (merchant_id, limit))
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d.get("metadata") or "{}")
        except Exception:
            d["metadata"] = {}
        result.append(d)
    return result


def upsert_transaction(txn: dict, merchant_id: str = None):
    m_id = merchant_id or txn.get("merchant_id")
    if not m_id:
        raise ValueError("merchant_id is required to upsert transaction")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO transactions
        (id, merchant_id, order_id, customer_name, customer_email, customer_phone,
         amount, currency, failure_type, failure_reason, risk_score,
         status, attempts, product_description, hinglish_message,
         recovery_time_seconds, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        txn["id"], m_id, txn.get("order_id"), txn.get("customer_name"),
        txn.get("customer_email"), txn.get("customer_phone"),
        txn.get("amount"), txn.get("currency", "INR"),
        txn.get("failure_type"), txn.get("failure_reason"),
        txn.get("risk_score", 0.5), txn.get("status", "FAILED"),
        txn.get("attempts", 0), txn.get("product_description"),
        txn.get("hinglish_message"), txn.get("recovery_time_seconds"),
        txn.get("created_at", datetime.utcnow().isoformat()),
        datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()


def upsert_checkout_session(session: dict, merchant_id: str = None):
    m_id = merchant_id or session.get("merchant_id")
    if not m_id:
        raise ValueError("merchant_id is required to upsert checkout session")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO checkout_sessions
        (id, merchant_id, user_id, customer_name, customer_email, customer_phone, amount, status,
         created_at, last_activity_at, attempts, failure_type, failure_reason,
         risk_score, hinglish_message, recovery_time_seconds, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session["id"], m_id, session.get("user_id"), session.get("customer_name"),
        session.get("customer_email"), session.get("customer_phone"),
        session.get("amount"), session.get("status", "ABANDONED"),
        session.get("created_at"), session.get("last_activity_at"),
        session.get("attempts", 0), session.get("failure_type"),
        session.get("failure_reason"), session.get("risk_score", 0.0),
        session.get("hinglish_message"), session.get("recovery_time_seconds"),
        datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()


def upsert_subscription(sub: dict, merchant_id: str = None):
    m_id = merchant_id or sub.get("merchant_id")
    if not m_id:
        raise ValueError("merchant_id is required to upsert subscription")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO subscriptions
        (id, merchant_id, user_id, customer_name, customer_email, customer_phone, amount, billing_cycle,
         retry_count, next_retry_at, status, created_at, failure_type, failure_reason,
         risk_score, hinglish_message, recovery_time_seconds, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        sub["id"], m_id, sub.get("user_id"), sub.get("customer_name"),
        sub.get("customer_email"), sub.get("customer_phone"),
        sub.get("amount"), sub.get("billing_cycle", "MONTHLY"),
        sub.get("retry_count", 0), sub.get("next_retry_at"),
        sub.get("status", "FAILED"), sub.get("created_at"),
        sub.get("failure_type"), sub.get("failure_reason"),
        sub.get("risk_score", 0.0), sub.get("hinglish_message"),
        sub.get("recovery_time_seconds"), datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()


def upsert_invoice(inv: dict, merchant_id: str = None):
    m_id = merchant_id or inv.get("merchant_id")
    if not m_id:
        raise ValueError("merchant_id is required to upsert invoice")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO invoices
        (id, merchant_id, business_id, customer_name, customer_email, customer_phone, amount, due_date,
         status, last_contacted_at, attempts, created_at, failure_type, failure_reason,
         risk_score, hinglish_message, recovery_time_seconds, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        inv["id"], m_id, inv.get("business_id"), inv.get("customer_name"),
        inv.get("customer_email"), inv.get("customer_phone"),
        inv.get("amount"), inv.get("due_date"),
        inv.get("status", "UNPAID"), inv.get("last_contacted_at"),
        inv.get("attempts", 0), inv.get("created_at"),
        inv.get("failure_type"), inv.get("failure_reason"),
        inv.get("risk_score", 0.0), inv.get("hinglish_message"),
        inv.get("recovery_time_seconds"), datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()


def get_transactions(merchant_id: str, status: str = None, failure_type: str = None, limit: int = 100):
    conn = get_db()
    c = conn.cursor()
    if status and failure_type:
        c.execute("""SELECT * FROM transactions WHERE merchant_id = ? AND status = ? AND failure_type = ?
                     ORDER BY risk_score DESC LIMIT ?""", (merchant_id, status, failure_type, limit))
    elif status:
        c.execute("SELECT * FROM transactions WHERE merchant_id = ? AND status = ? ORDER BY risk_score DESC LIMIT ?",
                  (merchant_id, status, limit))
    elif failure_type:
        c.execute("SELECT * FROM transactions WHERE merchant_id = ? AND failure_type = ? ORDER BY risk_score DESC LIMIT ?",
                  (merchant_id, failure_type, limit))
    else:
        c.execute("SELECT * FROM transactions WHERE merchant_id = ? ORDER BY updated_at DESC LIMIT ?", (merchant_id, limit))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_unified_entities(merchant_id: str, status: str = None, failure_type: str = None, limit: int = 200) -> list:
    """
    Returns a unified list of all entity types (payments, checkouts, subscriptions, invoices)
    formatted as unified dicts for the ledger view, filtered by merchant_id.
    """
    conn = get_db()
    c = conn.cursor()
    entities = []

    def map_status(src_status, src):
        src_status = src_status.upper()
        if src_status in ["RECOVERED", "ACTIVE", "PAID"]:
            return "RECOVERED"
        if src_status in ["FAILED", "ABANDONED", "UNPAID", "OVERDUE"]:
            return "FAILED"
        if src_status == "ESCALATED":
            return "ESCALATED"
        if src_status == "EXHAUSTED":
            return "EXHAUSTED"
        return src_status

    # Get payments
    p_query = "SELECT * FROM transactions WHERE merchant_id = ?"
    p_params = [merchant_id]
    if status:
        p_query += " AND status = ?"
        p_params.append("RECOVERED" if status == "RECOVERED" else ("FAILED" if status == "FAILED" else status))
    c.execute(p_query + " ORDER BY updated_at DESC LIMIT ?", p_params + [limit])
    for r in c.fetchall():
        d = dict(r)
        entities.append({
            "id": d["id"],
            "merchant_id": d["merchant_id"],
            "customer_name": d["customer_name"],
            "customer_email": d["customer_email"],
            "customer_phone": d["customer_phone"],
            "amount": d["amount"],
            "failure_type": d["failure_type"],
            "failure_reason": d["failure_reason"],
            "risk_score": d["risk_score"],
            "status": map_status(d["status"], "payment"),
            "attempts": d["attempts"],
            "source_type": "payment",
            "product_description": d["product_description"],
            "hinglish_message": d["hinglish_message"],
            "recovery_time_seconds": d["recovery_time_seconds"],
            "created_at": d["created_at"],
            "updated_at": d["updated_at"]
        })

    # Get checkouts
    c_query = "SELECT * FROM checkout_sessions WHERE merchant_id = ?"
    c_params = [merchant_id]
    if status:
        c_query += " AND status = ?"
        c_params.append("RECOVERED" if status == "RECOVERED" else ("ABANDONED" if status == "FAILED" else status))
    c.execute(c_query + " ORDER BY updated_at DESC LIMIT ?", c_params + [limit])
    for r in c.fetchall():
        d = dict(r)
        entities.append({
            "id": d["id"],
            "merchant_id": d["merchant_id"],
            "customer_name": d["customer_name"] or f"User {d['user_id']}",
            "customer_email": d["customer_email"],
            "customer_phone": d["customer_phone"],
            "amount": d["amount"],
            "failure_type": d["failure_type"],
            "failure_reason": d["failure_reason"],
            "risk_score": d["risk_score"],
            "status": map_status(d["status"], "checkout"),
            "attempts": d["attempts"],
            "source_type": "checkout",
            "product_description": "Checkout Session",
            "hinglish_message": d["hinglish_message"],
            "recovery_time_seconds": d["recovery_time_seconds"],
            "created_at": d["created_at"],
            "updated_at": d["updated_at"]
        })

    # Get subscriptions
    s_query = "SELECT * FROM subscriptions WHERE merchant_id = ?"
    s_params = [merchant_id]
    if status:
        s_query += " AND status = ?"
        s_params.append("RECOVERED" if status == "RECOVERED" else ("FAILED" if status == "FAILED" else status))
    c.execute(s_query + " ORDER BY updated_at DESC LIMIT ?", s_params + [limit])
    for r in c.fetchall():
        d = dict(r)
        entities.append({
            "id": d["id"],
            "merchant_id": d["merchant_id"],
            "customer_name": d["customer_name"] or f"Subscriber {d['user_id']}",
            "customer_email": d["customer_email"],
            "customer_phone": d["customer_phone"],
            "amount": d["amount"],
            "failure_type": d["failure_type"],
            "failure_reason": d["failure_reason"],
            "risk_score": d["risk_score"],
            "status": map_status(d["status"], "subscription"),
            "attempts": d["retry_count"],
            "source_type": "subscription",
            "product_description": f"Subscription ({d['billing_cycle']})",
            "hinglish_message": d["hinglish_message"],
            "recovery_time_seconds": d["recovery_time_seconds"],
            "created_at": d["created_at"],
            "updated_at": d["updated_at"]
        })

    # Get invoices
    i_query = "SELECT * FROM invoices WHERE merchant_id = ?"
    i_params = [merchant_id]
    if status:
        i_query += " AND status = ?"
        i_params.append("RECOVERED" if status == "RECOVERED" else ("UNPAID" if status == "FAILED" else status))
    c.execute(i_query + " ORDER BY updated_at DESC LIMIT ?", i_params + [limit])
    for r in c.fetchall():
        d = dict(r)
        entities.append({
            "id": d["id"],
            "merchant_id": d["merchant_id"],
            "customer_name": d["customer_name"] or f"Business {d['business_id']}",
            "customer_email": d["customer_email"],
            "customer_phone": d["customer_phone"],
            "amount": d["amount"],
            "failure_type": d["failure_type"],
            "failure_reason": d["failure_reason"],
            "risk_score": d["risk_score"],
            "status": map_status(d["status"], "invoice"),
            "attempts": d["attempts"],
            "source_type": "invoice",
            "product_description": "B2B Invoice",
            "hinglish_message": d["hinglish_message"],
            "recovery_time_seconds": d["recovery_time_seconds"],
            "created_at": d["created_at"],
            "updated_at": d["updated_at"]
        })

    conn.close()

    if failure_type:
        entities = [e for e in entities if e["failure_type"] == failure_type]

    entities.sort(key=lambda x: (x["risk_score"], x["updated_at"]), reverse=True)
    return entities[:limit]


def update_entity_status(entity_id: str, source_type: str, status: str, attempts: int = None,
                         recovery_time_seconds: int = None, hinglish_message: str = None,
                         merchant_id: str = None):
    """
    Unified updates status and metadata for a specific entity type, restricted to merchant_id.
    """
    if not merchant_id:
        raise ValueError("merchant_id is required to update entity status")
    conn = get_db()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()

    table_map = {
        "payment": ("transactions", "status", "attempts", "id"),
        "checkout": ("checkout_sessions", "status", "attempts", "id"),
        "subscription": ("subscriptions", "status", "retry_count", "id"),
        "invoice": ("invoices", "status", "attempts", "id")
    }

    if source_type not in table_map:
        return

    table, status_col, attempt_col, id_col = table_map[source_type]

    updates = [f"{status_col} = ?", "updated_at = ?"]
    values = [status, now]

    if attempts is not None:
        updates.append(f"{attempt_col} = ?")
        values.append(attempts)
    if recovery_time_seconds is not None:
        updates.append("recovery_time_seconds = ?")
        values.append(recovery_time_seconds)
    if hinglish_message is not None:
        updates.append("hinglish_message = ?")
        values.append(hinglish_message)

    values.append(entity_id)
    values.append(merchant_id)
    c.execute(f"UPDATE {table} SET {', '.join(updates)} WHERE {id_col} = ? AND merchant_id = ?", values)
    conn.commit()
    conn.close()


def save_session(session: dict, merchant_id: str = None):
    m_id = merchant_id or session.get("merchant_id")
    if not m_id:
        raise ValueError("merchant_id is required to save recovery session")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO recovery_sessions
        (id, merchant_id, started_at, completed_at, total_transactions, recovered_count,
         escalated_count, exhausted_count, retries_attempted, links_sent,
         amount_at_risk, amount_recovered, avg_recovery_time_seconds, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session["id"], m_id, session.get("started_at"), session.get("completed_at"),
        session.get("total_transactions", 0), session.get("recovered_count", 0),
        session.get("escalated_count", 0), session.get("exhausted_count", 0),
        session.get("retries_attempted", 0), session.get("links_sent", 0),
        session.get("amount_at_risk", 0), session.get("amount_recovered", 0),
        session.get("avg_recovery_time_seconds"), session.get("status", "RUNNING")
    ))
    conn.commit()
    conn.close()


def get_sessions(merchant_id: str, limit: int = 20):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM recovery_sessions WHERE merchant_id = ? ORDER BY started_at DESC LIMIT ?", (merchant_id, limit))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Promise-to-Pay helpers
def create_promise_to_pay(txn_id: str, customer_id: str, promised_date: str,
                           reminder_date: str, amount: int, notes: str = None,
                           source_type: str = "payment", merchant_id: str = None) -> int:
    if not merchant_id:
        raise ValueError("merchant_id is required to create promise to pay")
    conn = get_db()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute("""
        INSERT INTO promise_to_pay
        (transaction_id, merchant_id, customer_id, promised_date, reminder_date,
         amount, status, notes, created_at, updated_at, source_type)
        VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)
    """, (txn_id, merchant_id, customer_id, promised_date, reminder_date, amount, notes, now, now, source_type))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_promises(merchant_id: str, status: str = None, limit: int = 50):
    conn = get_db()
    c = conn.cursor()
    if status:
        c.execute("SELECT * FROM promise_to_pay WHERE merchant_id = ? AND status = ? ORDER BY promised_date LIMIT ?",
                  (merchant_id, status, limit))
    else:
        c.execute("SELECT * FROM promise_to_pay WHERE merchant_id = ? ORDER BY promised_date DESC LIMIT ?", (merchant_id, limit))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_dashboard_stats(merchant_id: str):
    conn = get_db()
    c = conn.cursor()

    # Query Payment Failures (transactions)
    c.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as amt FROM transactions WHERE merchant_id = ?", (merchant_id,))
    row = c.fetchone()
    p_total = row["cnt"]
    p_at_risk = row["amt"]

    c.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as amt FROM transactions WHERE merchant_id = ? AND status = 'RECOVERED'", (merchant_id,))
    row = c.fetchone()
    p_recovered_cnt = row["cnt"]
    p_recovered = row["amt"]

    c.execute("SELECT COUNT(*) as cnt FROM transactions WHERE merchant_id = ? AND status = 'FAILED'", (merchant_id,))
    p_failed = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM transactions WHERE merchant_id = ? AND status = 'ESCALATED'", (merchant_id,))
    p_escalated = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM transactions WHERE merchant_id = ? AND status = 'EXHAUSTED'", (merchant_id,))
    p_exhausted = c.fetchone()["cnt"]

    # Query Checkout Sessions
    c.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as amt FROM checkout_sessions WHERE merchant_id = ?", (merchant_id,))
    row = c.fetchone()
    chk_total = row["cnt"]
    chk_at_risk = row["amt"]

    c.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as amt FROM checkout_sessions WHERE merchant_id = ? AND status IN ('RECOVERED', 'SUCCESS')", (merchant_id,))
    row = c.fetchone()
    chk_recovered_cnt = row["cnt"]
    chk_recovered = row["amt"]

    c.execute("SELECT COUNT(*) as cnt FROM checkout_sessions WHERE merchant_id = ? AND status IN ('ABANDONED', 'FAILED')", (merchant_id,))
    chk_failed = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM checkout_sessions WHERE merchant_id = ? AND status = 'ESCALATED'", (merchant_id,))
    chk_escalated = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM checkout_sessions WHERE merchant_id = ? AND status = 'EXHAUSTED'", (merchant_id,))
    chk_exhausted = c.fetchone()["cnt"]

    # Query Subscriptions
    c.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as amt FROM subscriptions WHERE merchant_id = ?", (merchant_id,))
    row = c.fetchone()
    sub_total = row["cnt"]
    sub_at_risk = row["amt"]

    c.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as amt FROM subscriptions WHERE merchant_id = ? AND status IN ('RECOVERED', 'ACTIVE')", (merchant_id,))
    row = c.fetchone()
    sub_recovered_cnt = row["cnt"]
    sub_recovered = row["amt"]

    c.execute("SELECT COUNT(*) as cnt FROM subscriptions WHERE merchant_id = ? AND status = 'FAILED'", (merchant_id,))
    sub_failed = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM subscriptions WHERE merchant_id = ? AND status = 'ESCALATED'", (merchant_id,))
    sub_escalated = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM subscriptions WHERE merchant_id = ? AND status = 'EXHAUSTED'", (merchant_id,))
    sub_exhausted = c.fetchone()["cnt"]

    # Query Invoices
    c.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as amt FROM invoices WHERE merchant_id = ?", (merchant_id,))
    row = c.fetchone()
    inv_total = row["cnt"]
    inv_at_risk = row["amt"]

    c.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as amt FROM invoices WHERE merchant_id = ? AND status IN ('RECOVERED', 'PAID')", (merchant_id,))
    row = c.fetchone()
    inv_recovered_cnt = row["cnt"]
    inv_recovered = row["amt"]

    c.execute("SELECT COUNT(*) as cnt FROM invoices WHERE merchant_id = ? AND status IN ('UNPAID', 'OVERDUE')", (merchant_id,))
    inv_failed = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM invoices WHERE merchant_id = ? AND status = 'ESCALATED'", (merchant_id,))
    inv_escalated = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM invoices WHERE merchant_id = ? AND status = 'EXHAUSTED'", (merchant_id,))
    inv_exhausted = c.fetchone()["cnt"]

    # Totals aggregation
    total = p_total + chk_total + sub_total + inv_total
    recovered = p_recovered_cnt + chk_recovered_cnt + sub_recovered_cnt + inv_recovered_cnt
    failed = p_failed + chk_failed + sub_failed + inv_failed
    escalated = p_escalated + chk_escalated + sub_escalated + inv_escalated
    exhausted = p_exhausted + chk_exhausted + sub_exhausted + inv_exhausted
    amount_at_risk = p_at_risk + chk_at_risk + sub_at_risk + inv_at_risk
    amount_recovered = p_recovered + chk_recovered + sub_recovered + inv_recovered

    # Retries & links in audit
    c.execute("SELECT COUNT(*) as cnt FROM audit_trail WHERE merchant_id = ? AND action = 'AUTO_RETRY'", (merchant_id,))
    retries_attempted = c.fetchone()["cnt"]

    c.execute("SELECT COUNT(*) as cnt FROM audit_trail WHERE merchant_id = ? AND action = 'PAYMENT_LINK'", (merchant_id,))
    links_sent = c.fetchone()["cnt"]

    # Average recovery time
    avg_recovery_time = 0.0
    times = []
    c.execute("SELECT recovery_time_seconds FROM transactions WHERE merchant_id = ? AND status = 'RECOVERED' AND recovery_time_seconds IS NOT NULL", (merchant_id,))
    times += [r["recovery_time_seconds"] for r in c.fetchall()]
    c.execute("SELECT recovery_time_seconds FROM checkout_sessions WHERE merchant_id = ? AND status IN ('RECOVERED', 'SUCCESS') AND recovery_time_seconds IS NOT NULL", (merchant_id,))
    times += [r["recovery_time_seconds"] for r in c.fetchall()]
    c.execute("SELECT recovery_time_seconds FROM subscriptions WHERE merchant_id = ? AND status IN ('RECOVERED', 'ACTIVE') AND recovery_time_seconds IS NOT NULL", (merchant_id,))
    times += [r["recovery_time_seconds"] for r in c.fetchall()]
    c.execute("SELECT recovery_time_seconds FROM invoices WHERE merchant_id = ? AND status IN ('RECOVERED', 'PAID') AND recovery_time_seconds IS NOT NULL", (merchant_id,))
    times += [r["recovery_time_seconds"] for r in c.fetchall()]
    if times:
        avg_recovery_time = round(sum(times) / len(times), 1)

    # Compliance / stop rules
    c.execute("SELECT COUNT(*) as cnt FROM audit_trail WHERE merchant_id = ? AND action = 'STOP_RULE_APPLIED'", (merchant_id,))
    stop_rules_applied = c.fetchone()["cnt"]

    # Action breakdown
    c.execute("SELECT action, COUNT(*) as cnt FROM audit_trail WHERE merchant_id = ? GROUP BY action", (merchant_id,))
    action_breakdown = {r["action"]: r["cnt"] for r in c.fetchall()}

    # Failure breakdown
    failure_breakdown = {}
    c.execute("SELECT failure_type, COUNT(*) as cnt FROM transactions WHERE merchant_id = ? GROUP BY failure_type", (merchant_id,))
    for r in c.fetchall():
        if r["failure_type"]:
            failure_breakdown[r["failure_type"]] = failure_breakdown.get(r["failure_type"], 0) + r["cnt"]
    c.execute("SELECT failure_type, COUNT(*) as cnt FROM checkout_sessions WHERE merchant_id = ? GROUP BY failure_type", (merchant_id,))
    for r in c.fetchall():
        if r["failure_type"]:
            failure_breakdown[r["failure_type"]] = failure_breakdown.get(r["failure_type"], 0) + r["cnt"]
    c.execute("SELECT failure_type, COUNT(*) as cnt FROM subscriptions WHERE merchant_id = ? GROUP BY failure_type", (merchant_id,))
    for r in c.fetchall():
        if r["failure_type"]:
            failure_breakdown[r["failure_type"]] = failure_breakdown.get(r["failure_type"], 0) + r["cnt"]
    c.execute("SELECT failure_type, COUNT(*) as cnt FROM invoices WHERE merchant_id = ? GROUP BY failure_type", (merchant_id,))
    for r in c.fetchall():
        if r["failure_type"]:
            failure_breakdown[r["failure_type"]] = failure_breakdown.get(r["failure_type"], 0) + r["cnt"]

    # Actionable computation
    c.execute("SELECT COUNT(*) as cnt FROM transactions WHERE merchant_id = ? AND failure_type NOT IN ('FRAUD_FLAGGED', 'CARD_BLOCKED')", (merchant_id,))
    p_act = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM checkout_sessions WHERE merchant_id = ? AND failure_type NOT IN ('FRAUD_FLAGGED', 'CARD_BLOCKED')", (merchant_id,))
    chk_act = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM subscriptions WHERE merchant_id = ? AND failure_type NOT IN ('FRAUD_FLAGGED', 'CARD_BLOCKED')", (merchant_id,))
    sub_act = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM invoices WHERE merchant_id = ? AND failure_type NOT IN ('FRAUD_FLAGGED', 'CARD_BLOCKED')", (merchant_id,))
    inv_act = c.fetchone()["cnt"]
    actionable = p_act + chk_act + sub_act + inv_act

    conn.close()

    recovery_rate = round((recovered / total * 100) if total > 0 else 0, 1)

    source_breakdown = {
        "payment": {
            "at_risk": p_at_risk,
            "recovered": p_recovered,
            "count": p_total,
            "recovered_count": p_recovered_cnt
        },
        "checkout": {
            "at_risk": chk_at_risk,
            "recovered": chk_recovered,
            "count": chk_total,
            "recovered_count": chk_recovered_cnt
        },
        "subscription": {
            "at_risk": sub_at_risk,
            "recovered": sub_recovered,
            "count": sub_total,
            "recovered_count": sub_recovered_cnt
        },
        "invoice": {
            "at_risk": inv_at_risk,
            "recovered": inv_recovered,
            "count": inv_total,
            "recovered_count": inv_recovered_cnt
        }
    }

    return {
        "total_transactions": total,
        "actionable": actionable,
        "recovered": recovered,
        "failed": failed,
        "escalated": escalated,
        "exhausted": exhausted,
        "recovery_rate": recovery_rate,
        "amount_at_risk": amount_at_risk,
        "amount_recovered": amount_recovered,
        "amount_without_agent": 0,
        "avg_recovery_time_seconds": avg_recovery_time,
        "retries_attempted": retries_attempted,
        "links_sent": links_sent,
        "stop_rules_applied": stop_rules_applied,
        "failure_breakdown": failure_breakdown,
        "action_breakdown": action_breakdown,
        "source_breakdown": source_breakdown
    }
