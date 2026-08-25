# reclaim. — AI-Driven Revenue Recovery Agent

`reclaim.` is a multi-tenant, compliance-safe AI revenue recovery platform designed to detect failed transactions (checkout drops, subscription failures, overdue invoices), diagnose the root causes using Gemini Flash AI, and automate recovery outreach via localized, conversational workflows.

---

## 🚀 Key Features

- **Gemini-Powered Diagnostics:** Classifies transaction failure reasons (gateway down, wrong CVV, fraud flags, transient issues) and recommends optimal recovery actions.
- **Compliance-Safe Guardrails:** Hard-coded stop-rules protecting businesses and customers from excessive retries and harassment (fully aligned with RBI recovery guidelines).
- **Hinglish Notification Engine:** Personalized outreach using natural, customer-friendly Hinglish scripts for higher payment link conversions.
- **Simulated Voice Assistant Desk:** Interactive customer collections script desk simulations for transactions requiring manual intervention.
- **Promise-to-Pay Manager:** Automated commitment calendar that tracking pay dates and schedules follow-up triggers on the dashboard.
- **Tenant Scoping & Authentication:** Fully isolated multi-tenant architecture using JWT authentication and SQLite row-level scoping (`merchant_id` filters) to prevent cross-merchant data leakage.

---

## 🛡️ Compliance & Correctness Rules

`reclaim.` prioritizes ethical collections and complies strictly with regulatory boundaries:
1. **Under-₹10 Economic Stop:** Workflows for failures under ₹10 (1000 paise) are automatically stopped (`EXHAUSTED` under `ECONOMIC_LIMIT`) since recovery costs exceed transaction value.
2. **Hard Decline Bypass:** Suspected frauds or blocked card failures bypass retries entirely and are escalated directly to the human operations desk.
3. **Outreach Frequency Caps:** A maximum cap of **3 attempts** is strictly enforced per transaction.
4. **Outreach Cooldowns:** Enforces a minimum **30-minute cooldown** window between outreach attempts. Any retry triggered before this window is blocked under `COOLDOWN_BLOCK`.
5. **Platform-Wide Do Not Contact (DNC):** A platform-level unsubscribe list instantly terminates any active or future recovery outreaches.

---

## 🛠️ Tech Stack

- **Backend:** FastAPI (Python async routes)
- **Database:** SQLite (with indexes and constraints on `merchant_id`)
- **AI Core:** Gemini 1.5 Flash (via Python SDK)
- **Security:** `bcrypt` password hashing & `python-jose` JWT tokens
- **Frontend:** Vanilla HTML5, CSS3, JavaScript (Glassmorphic dashboard)

---

## ⚙️ Setup & Run Instructions

### 1. Prerequisites
- Python 3.9+ installed.

### 2. Install Dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file in the root and `backend/` directory:
```env
JWT_SECRET=your-secure-jwt-secret-key
GEMINI_API_KEY=your-gemini-api-key
```
*(If `JWT_SECRET` is left unset, the backend will auto-generate a random fallback secret at startup for testing).*

### 4. Seed database & run simulation
Run the demo runner script to initialize schema tables and seed sample data:
```bash
python3 demo_runner.py
```
This seeds two demo accounts:
- **Merchant 1 (Bharat Retail Co.):** `demo1@reclaim.test` / `password123`
- **Merchant 2 (Second Merchant):** `demo2@reclaim.test` / `password123`

### 5. Start the FastAPI Dev Server
```bash
python3 -m uvicorn main:app --reload
```
The application will run locally at **[http://127.0.0.1:8000](http://127.0.0.1:8000)**.
- **Home/Landing Page:** `http://localhost:8000/`
- **Merchant Dashboard:** `http://localhost:8000/dashboard`
- **Interactive Slides Slide Deck:** `http://localhost:8000/presentation`
